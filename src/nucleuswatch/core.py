"""Query several alert brokers for one object and merge what they return."""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ._http import BrokerError
from .brokers import SURVEYS
from .brokers.base import CLASSIFICATION_COLUMNS, LIGHTCURVE_COLUMNS, BrokerResult
from .taxonomy import VOTE_PRIORITY, VOTING_CLASSIFIERS, categorize

ZTF_ID = re.compile(r"^ZTF\d{2}[a-z]{7}$")
LSST_ID = re.compile(r"^\d{15,20}$")  # Rubin diaObjectId


def survey_of(object_id: str) -> str:
    """'ztf' or 'lsst', from the shape of the object id."""
    object_id = str(object_id).strip()
    if ZTF_ID.match(object_id):
        return "ztf"
    if LSST_ID.match(object_id):
        return "lsst"
    raise ValueError(f"{object_id!r} is neither a ZTF object id (e.g. 'ZTF21abfmbix') "
                     "nor an LSST diaObjectId (e.g. 170587117485817955)")


@dataclass
class MergedObject:
    """Everything the queried brokers know about one object, side by side.

    Every row in every table carries a ``broker`` column, so any value can be
    traced back to the broker that produced it.
    """

    object_id: str
    survey: str = "ztf"
    results: dict[str, BrokerResult] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def brokers(self) -> list[str]:
        return list(self.results)

    @property
    def lightcurve(self) -> pd.DataFrame:
        """All detections from all brokers (one row per broker per detection)."""
        frames = [r.lightcurve for r in self.results.values() if not r.lightcurve.empty]
        if not frames:
            return pd.DataFrame(columns=LIGHTCURVE_COLUMNS)
        return pd.concat(frames, ignore_index=True).sort_values(["mjd", "broker"]).reset_index(drop=True)

    @property
    def classifications(self) -> pd.DataFrame:
        """Every broker's classifier outputs, plus a normalised ``category``."""
        frames = [r.classifications for r in self.results.values() if not r.classifications.empty]
        if not frames:
            df = pd.DataFrame(columns=CLASSIFICATION_COLUMNS)
        else:
            df = pd.concat(frames, ignore_index=True)
        df["votes"] = [
            (b, c) in VOTING_CLASSIFIERS for b, c in zip(df["broker"], df["classifier"])
        ]
        # Only whole-object labels get a category: a binary score such as
        # "Kilonova: 0.0" is not a claim that the object is a kilonova.
        df["category"] = df["label"].map(categorize).where(df["votes"])
        return df

    def summary(self) -> pd.DataFrame:
        """One row per broker: position, detection count, time span, link."""
        rows = []
        for name, r in self.results.items():
            lc = r.lightcurve
            rows.append({
                "broker": name,
                "ra": r.ra,
                "dec": r.dec,
                "ndet": r.ndet,
                "first_mjd": lc["mjd"].min() if not lc.empty else None,
                "last_mjd": lc["mjd"].max() if not lc.empty else None,
                "url": r.url,
                **{k: v for k, v in r.extra.items() if v not in (None, "")},
            })
        df = pd.DataFrame(rows)
        if not df.empty and df["ra"].notna().any():
            ra0, dec0 = df["ra"].median(), df["dec"].median()
            dra = (df["ra"] - ra0) * np.cos(np.radians(dec0))
            df.insert(3, "offset_arcsec", np.hypot(dra, df["dec"] - dec0) * 3600)
        return df

    def coverage(self) -> pd.DataFrame:
        """Which broker has which detection, matched on ZTF ``candid``.

        A detection missing from one broker usually means it was filtered out
        or the broker has not processed it yet. ``mag_spread`` shows whether the
        brokers report the same magnitude for the same alert.
        """
        lc = self.lightcurve.dropna(subset=["candid"])
        if lc.empty:
            return pd.DataFrame()
        table = lc.pivot_table(index="candid", columns="broker", values="mag", aggfunc="first")
        meta = lc.groupby("candid").agg(mjd=("mjd", "min"), band=("band", "first"))
        out = meta.join(table.notna())
        out["mag_spread"] = table.max(axis=1) - table.min(axis=1)
        return out.sort_values("mjd").reset_index()

    def consensus(self) -> dict:
        """Do the brokers agree on what this object is?

        Returns a dict with ``status`` ("agree", "disagree" or "insufficient"),
        the distinct ``categories`` voted for, and the individual ``votes``.
        """
        df = self.classifications
        usable = df[df["votes"] & df["category"].notna()]
        votes = []
        for broker, rows in usable.groupby("broker", sort=False):
            # One vote per broker: its most-preferred classifier that answered.
            # A classifier can appear several times (ALeRCE serves several
            # model versions); the most confident row wins.
            for name in VOTE_PRIORITY.get(broker, ()):
                chosen = rows[rows["classifier"] == name]
                if len(chosen):
                    best = chosen.assign(_p=pd.to_numeric(chosen["probability"], errors="coerce").fillna(-1))
                    row = best.sort_values("_p", ascending=False).iloc[0]
                    votes.append({"broker": broker, "classifier": name, "label": row["label"],
                                  "category": row["category"]})
                    break
        categories = sorted({v["category"] for v in votes})
        if len(votes) < 2:
            status = "insufficient"
        elif len(categories) == 1:
            status = "agree"
        else:
            status = "disagree"
        return {"status": status, "categories": categories, "votes": votes}

    def plot(self, ax=None, *, annotate: bool = True):
        """Plot the merged light curve: colour = band, marker = broker.

        ``annotate=False`` hides ids and broker labels (for volunteers).
        """
        from .plot import plot_object
        return plot_object(self, ax=ax, annotate=annotate)

    def __repr__(self) -> str:
        c = self.consensus()
        parts = [f"MergedObject({self.object_id!r}, brokers={self.brokers}"]
        if self.errors:
            parts.append(f", failed={list(self.errors)}")
        parts.append(f", consensus={c['status']} {c['categories']})")
        return "".join(parts)


def get_object(
    object_id: str,
    brokers: list[str] | None = None,
    *,
    use_cache: bool = True,
    lasair_token: str | None = None,
) -> MergedObject:
    """Query brokers in parallel for a ZTF or LSST object and merge the results.

    The survey is inferred from the id: ``ZTF..`` names are ZTF, integer
    ``diaObjectId`` values are Rubin/LSST.

    A broker that fails (down, rate-limited, no token) is recorded in
    ``.errors`` instead of failing the whole call.

    >>> obj = get_object("ZTF21abfmbix")
    >>> obj.consensus()["status"]
    """
    object_id = str(object_id).strip()
    survey = survey_of(object_id)
    adapters = SURVEYS[survey]

    names = brokers or list(adapters)
    unknown = set(names) - set(adapters)
    if unknown:
        raise ValueError(f"unknown broker(s): {sorted(unknown)}; choose from {list(adapters)}")

    def run(name: str) -> BrokerResult:
        kwargs = {"use_cache": use_cache}
        if name == "lasair":
            kwargs["token"] = lasair_token
        return adapters[name].fetch(object_id, **kwargs)

    merged = MergedObject(object_id, survey)
    with ThreadPoolExecutor(max_workers=len(names)) as pool:
        futures = {name: pool.submit(run, name) for name in names}
    for name, fut in futures.items():
        try:
            result = fut.result()
        except BrokerError as exc:
            merged.errors[name] = str(exc)
            continue
        except (KeyError, TypeError, ValueError) as exc:
            # The broker answered but in a shape we don't understand —
            # usually an API change. Report it rather than crash.
            merged.errors[name] = f"could not parse response: {exc!r}"
            continue
        if result.ndet == 0 and result.classifications.empty:
            merged.errors[name] = "object not found"
            continue
        merged.results[name] = result
    return merged
