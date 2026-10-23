"""Science recipes: candidate searches built on top of the merged view.

``nuclear_transients`` finds recent ZTF transients sitting on the nucleus of a
galaxy (TDEs, AGN flares, nuclear SNe) and asks every broker what it thinks
they are. The positional cuts are the usual ZTF nuclear-transient ones:

- ``distnr``: distance to the nearest source in the ZTF reference image —
  small means the transient is on top of a pre-existing source (the nucleus);
- ``distpsnr1`` / ``sgscore1``: the nearest Pan-STARRS1 source is close and is
  galaxy-like (star/galaxy score < 0.5), which removes variable stars.

All thresholds are parameters; tune them for your science case.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone

import pandas as pd

from ._http import BrokerError
from .brokers import fink
from .brokers.base import JD_TO_MJD, ZTF_BANDS
from .core import MergedObject, get_object

# Fink classes a nuclear transient can plausibly end up in. TDEs and AGN
# flares are often "Ambiguous" in Fink, so it is included on purpose.
NUCLEAR_FINK_CLASSES = ("SN candidate", "Early SN Ia candidate", "SLSN candidate", "Ambiguous")


@dataclass
class NuclearCuts:
    mag_max: float = 19.0         # alert must be brighter (numerically <=) than this
    distnr_max: float = 0.5       # arcsec to nearest ZTF reference source
    distpsnr1_max: float = 1.0    # arcsec to nearest PS1 source
    sgscore1_max: float = 0.5     # PS1 star/galaxy score: 0 galaxy, 1 star
    ndethist_min: int = 2         # drop single-detection objects

    def passes(self, alert: dict) -> bool:
        def val(key, default):
            v = alert.get(key)
            # ZTF uses -999 for "no value"
            return default if v is None or v == -999 else v

        return (
            val("i:magpsf", 99) <= self.mag_max
            and val("i:distnr", 99) <= self.distnr_max
            and val("i:distpsnr1", 99) <= self.distpsnr1_max
            and 0 <= val("i:sgscore1", 1) <= self.sgscore1_max
            and val("i:ndethist", 0) >= self.ndethist_min
            and alert.get("i:isdiffpos") in ("t", "1", 1, True)
        )


@dataclass
class CandidateSearch:
    """Result of a recipe run: a candidate table plus the merged objects."""

    candidates: pd.DataFrame
    objects: dict[str, MergedObject] = field(default_factory=dict)
    query: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.candidates)

    def __iter__(self):
        return iter(self.objects.values())


def nuclear_transients(
    days: float = 7,
    cuts: NuclearCuts | None = None,
    *,
    fink_classes: tuple[str, ...] = NUCLEAR_FINK_CLASSES,
    merge: bool = True,
    brokers: list[str] | None = None,
    use_cache: bool = True,
    now: datetime | None = None,
) -> CandidateSearch:
    """Nuclear transients with an alert in the last ``days`` days.

    Candidates come from Fink's class streams (the only broker whose public
    API exposes the needed alert fields without a token), then each one is
    looked up in every broker with :func:`get_object` to build the
    cross-broker consensus.
    """
    cuts = cuts or NuclearCuts()
    now = now or datetime.now(timezone.utc)
    start, stop = (now - timedelta(days=days)).date().isoformat(), (now + timedelta(days=1)).date().isoformat()

    errors: list[str] = []
    alerts: list[dict] = []
    for cls in fink_classes:
        try:
            rows = fink.latests(cls, start, stop, use_cache=use_cache)
        except BrokerError as exc:
            errors.append(f"fink latests {cls!r}: {exc}")
            continue
        for r in rows:
            r["_fink_class"] = cls
        alerts.extend(rows)

    # One row per object: keep its brightest passing alert.
    best: dict[str, dict] = {}
    for a in alerts:
        if not cuts.passes(a):
            continue
        oid = a.get("i:objectId")
        if oid and (oid not in best or a["i:magpsf"] < best[oid]["i:magpsf"]):
            best[oid] = a

    rows = [_candidate_row(a) for a in best.values()]
    table = pd.DataFrame(rows, columns=CANDIDATE_COLUMNS)

    objects: dict[str, MergedObject] = {}
    if merge and len(table):
        with ThreadPoolExecutor(max_workers=4) as pool:
            merged = pool.map(
                lambda oid: get_object(oid, brokers, use_cache=use_cache), table["object_id"]
            )
            objects = {m.object_id: m for m in merged}
        table = _add_consensus(table, objects)

    table = table.sort_values("mag").reset_index(drop=True)
    query = {"recipe": "nuclear_transients", "days": days, "startdate": start, "stopdate": stop,
             "fink_classes": list(fink_classes), "cuts": asdict(cuts)}
    return CandidateSearch(table, objects, query, errors)


CANDIDATE_COLUMNS = [
    "object_id", "ra", "dec", "mag", "band", "mjd", "distnr", "distpsnr1", "sgscore1",
    "ndethist", "fink_class",
]


def _candidate_row(a: dict) -> dict:
    return {
        "object_id": a.get("i:objectId"),
        "ra": a.get("i:ra"),
        "dec": a.get("i:dec"),
        "mag": a.get("i:magpsf"),
        "band": ZTF_BANDS.get(a.get("i:fid")),
        "mjd": a["i:jd"] - JD_TO_MJD if a.get("i:jd") else None,
        "distnr": a.get("i:distnr"),
        "distpsnr1": a.get("i:distpsnr1"),
        "sgscore1": a.get("i:sgscore1"),
        "ndethist": a.get("i:ndethist"),
        "fink_class": a.get("v:classification") or a.get("_fink_class"),
    }


def _add_consensus(table: pd.DataFrame, objects: dict[str, MergedObject]) -> pd.DataFrame:
    status, cats, labels = [], [], []
    for oid in table["object_id"]:
        c = objects[oid].consensus()
        status.append(c["status"])
        cats.append(", ".join(c["categories"]))
        labels.append("; ".join(f"{v['broker']}:{v['label']}" for v in c["votes"]))
    return table.assign(consensus=status, categories=cats, broker_labels=labels)
