"""Science recipes: candidate searches built on top of the merged view.

``nuclear_transients`` finds recent ZTF transients sitting on the nucleus of a
galaxy (TDEs, AGN flares, nuclear SNe) and asks every broker what it thinks
they are. The positional cuts are the usual ZTF nuclear-transient ones:

- ``distnr``: distance to the nearest source in the ZTF reference image —
  small means the transient is on top of a pre-existing source (the nucleus);
- ``distpsnr1`` / ``sgscore1``: the nearest Pan-STARRS1 source is close and is
  galaxy-like (star/galaxy score < 0.5), which removes variable stars.

Candidates can come from two sources:

- ``"fink"``: Fink class streams (SN-like, Ambiguous, TNS- and SIMBAD-labelled
  AGN/TDE streams), with the cuts applied to each alert;
- ``"lasair"``: objects Lasair's Sherlock classifies as nuclear transients
  (``NT``, within 1 arcsec of the host core). Needs a Lasair token. The ZTF
  alert-level cuts can't be applied to these, so they rely on Sherlock's
  association and the magnitude limit.

With ``new_within_days`` set, objects are split into *new* and *known*. An
object first detected less than ``new_within_days`` ago is new. An older one
is known only if its nucleus was already active before the current flare:
at least one positive alert in the ``flare_lookback`` days before the flare
began, where the flare is the run of positive alerts with no gap longer than
``flare_gap`` days, or a flare that itself started more than
``new_within_days`` ago. This keeps TDEs in galaxies with old detections in the
new list (75% of confirmed TDEs instead of 56%, at the cost of filing 79%
instead of 94% of AGN as known; tuned on 2024-2025, see validation/). Alert
histories come from ALeRCE, which keeps every public ZTF alert.
Known objects are listed in ``search.known`` and not looked up in every broker.

``lsst_nuclear_transients`` does the same for Rubin/LSST. Rubin alerts carry
no host information yet, so the nucleus test uses the Legacy Surveys DR10
catalogue instead (see :mod:`nucleuswatch.hosts`): the nearest catalogued
source must be a galaxy within ``max_offset`` arcsec.

All thresholds are parameters; tune them for your science case.
"""

from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone

import pandas as pd

from . import hosts
from ._http import BrokerError
from .brokers import alerce, fink, fink_lsst, lasair
from .brokers.base import JD_TO_MJD, ZTF_BANDS, flux_to_mag
from .core import MergedObject, get_object

# Fink streams a nuclear transient can plausibly end up in. TDEs and AGN
# flares are often "Ambiguous"; known AGN arrive via the SIMBAD/TNS streams.
NUCLEAR_FINK_CLASSES = (
    "SN candidate", "Early SN Ia candidate", "SLSN candidate", "Ambiguous",
    "(TNS) TDE", "(TNS) AGN", "(SIMBAD) AGN", "(SIMBAD) QSO", "(SIMBAD) Seyfert1",
)
SOURCES = ("fink", "lasair")


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
    """Result of a recipe run: a candidate table plus the merged objects.

    ``known`` holds objects that passed the cuts but were first detected more
    than ``new_within_days`` ago; they are not merged across brokers.
    """

    candidates: pd.DataFrame
    objects: dict[str, MergedObject] = field(default_factory=dict)
    query: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    known: pd.DataFrame = field(default_factory=lambda: pd.DataFrame(columns=CANDIDATE_COLUMNS))
    funnel: dict = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.candidates)

    def __iter__(self):
        return iter(self.objects.values())


def nuclear_transients(
    days: float = 7,
    cuts: NuclearCuts | None = None,
    *,
    sources: tuple[str, ...] = ("fink",),
    fink_classes: tuple[str, ...] = NUCLEAR_FINK_CLASSES,
    new_within_days: float | None = None,
    flare_gap: float = 30,
    flare_lookback: float = 730,
    merge: bool = True,
    brokers: list[str] | None = None,
    use_cache: bool = True,
    now: datetime | None = None,
) -> CandidateSearch:
    """Nuclear transients with an alert in the ``days`` days before ``now``.

    Each candidate is then looked up in every broker with :func:`get_object`
    to build the cross-broker consensus (``merge=True``).
    """
    cuts = cuts or NuclearCuts()
    now = now or datetime.now(timezone.utc)
    unknown = set(sources) - set(SOURCES)
    if unknown:
        raise ValueError(f"unknown source(s) {sorted(unknown)}; choose from {SOURCES}")
    start, stop = (now - timedelta(days=days)).date(), (now + timedelta(days=1)).date()
    # Windows that ended more than two days ago no longer change; cache them long.
    historical = now < datetime.now(timezone.utc) - timedelta(days=2)
    ttl = HISTORICAL_TTL if historical else 600
    errors: list[str] = []
    funnel = {"alerts_scanned": 0, "objects_scanned": 0, "passed_cuts": 0, "by_source": {}}
    found: dict[str, dict] = {}

    if "fink" in sources:
        alerts: list[dict] = []
        for cls in fink_classes:
            try:
                rows = fink.latests_all(cls, start, stop, use_cache=use_cache, ttl=ttl)
            except BrokerError as exc:
                errors.append(f"fink latests {cls!r}: {exc}")
                continue
            for r in rows:
                r["_fink_class"] = cls
            alerts.extend(rows)
        funnel["alerts_scanned"] += len(alerts)
        funnel["objects_scanned"] += len({a.get("i:objectId") for a in alerts})
        # One row per object: keep its brightest passing alert.
        best: dict[str, dict] = {}
        first_pass: dict[str, float] = {}
        streams: dict[str, set] = {}
        for a in alerts:
            if not cuts.passes(a):
                continue
            oid = a.get("i:objectId")
            if not oid:
                continue
            streams.setdefault(oid, set()).add(a["_fink_class"])
            if a.get("i:jd"):
                first_pass[oid] = min(first_pass.get(oid, math.inf), a["i:jd"] - JD_TO_MJD)
            if oid not in best or a["i:magpsf"] < best[oid]["i:magpsf"]:
                best[oid] = a
        for oid, a in best.items():
            found[oid] = {**_fink_row(a, sorted(streams[oid])), "first_pass_mjd": first_pass.get(oid)}
        funnel["by_source"]["fink"] = len(best)

    used: list[str] = ["fink"] if "fink" in sources else []
    if "lasair" in sources:
        if now < datetime.now(timezone.utc) - timedelta(days=1):
            errors.append("lasair source skipped: its query API only searches up to now, not past windows")
        else:
            try:
                rows = lasair.nuclear_candidates(days, cuts.mag_max, use_cache=use_cache)
            except BrokerError as exc:
                errors.append(f"lasair nuclear_candidates: {exc}")
                rows = []
            funnel["objects_scanned"] += len(rows)
            for r in rows:
                oid = r["objectId"]
                if oid in found:
                    found[oid]["sources"] += "; lasair:NT"
                else:
                    found[oid] = _lasair_row(r)
            funnel["by_source"]["lasair"] = len(rows)
            if not any(e.startswith("lasair") for e in errors):
                used.append("lasair")

    table = pd.DataFrame(list(found.values()), columns=CANDIDATE_COLUMNS)
    table["age_days"] = [_age_days(r, now) for r in table.to_dict("records")]
    funnel["passed_cuts"] = len(table)

    table["flare_onset_mjd"] = None
    table["prior_alerts"] = None
    known = table.iloc[0:0]
    if new_within_days is not None:
        old = ~(table["age_days"].isna() | (table["age_days"] <= new_within_days))
        now_mjd = now.timestamp() / 86400 + 40587
        history = _flare_history(list(table.loc[old, "object_id"]), now_mjd, flare_gap, flare_lookback,
                                 use_cache=use_cache, errors=errors)
        for oid, (onset, prior) in history.items():
            idx = table.index[table["object_id"] == oid]
            table.loc[idx, "flare_onset_mjd"] = onset
            table.loc[idx, "prior_alerts"] = prior
        # Old objects stay new if the current flare is recent and the nucleus was
        # quiet before it. A nucleus that never paused (one endless "flare") is known.
        def quiet_recent_flare(oid):
            onset, prior = history.get(oid, (None, 1))
            return onset is not None and prior == 0 and now_mjd - onset <= new_within_days

        quiet = table["object_id"].map(quiet_recent_flare)
        is_new = ~old | quiet
        known = table[~is_new].sort_values("mag").reset_index(drop=True)
        table = table[is_new]
    funnel["new"], funnel["known"] = len(table), len(known)

    objects: dict[str, MergedObject] = {}
    if merge and len(table):
        objects = merge_objects(list(table["object_id"]), brokers, use_cache=use_cache)
        table = _add_consensus(table, objects)

    table = table.sort_values("mag").reset_index(drop=True)
    query = {"recipe": "nuclear_transients", "days": days, "startdate": start.isoformat(),
             "stopdate": stop.isoformat(), "sources": used, "sources_requested": list(sources),
             "fink_classes": list(fink_classes),
             "new_within_days": new_within_days, "flare_gap": flare_gap, "flare_lookback": flare_lookback,
             "cuts": asdict(cuts)}
    return CandidateSearch(table, objects, query, errors, known, funnel)


def merge_objects(object_ids: list[str], brokers: list[str] | None = None, *,
                  use_cache: bool = True) -> dict[str, MergedObject]:
    """Look many objects up in every broker, economically.

    ALeRCE, Fink and ANTARES are queried per object, in parallel. Lasair is queried in
    one batch (see :func:`lasair.batch_results`), because its API allows only
    100 calls per hour and a per-object lookup would exhaust that in a week
    with ~30 candidates. If the batch fails, the reason is recorded on every
    object instead of failing the search.
    """
    wanted = brokers or ["alerce", "fink", "lasair", "antares"]
    per_object = [b for b in wanted if b != "lasair"]
    with ThreadPoolExecutor(max_workers=4) as pool:
        merged = list(pool.map(lambda oid: get_object(oid, per_object, use_cache=use_cache), object_ids))
    objects = {m.object_id: m for m in merged}
    if "lasair" in wanted:
        try:
            found = lasair.batch_results(object_ids, use_cache=use_cache)
        except BrokerError as exc:
            for m in objects.values():
                m.errors["lasair"] = str(exc)
        else:
            for oid, m in objects.items():
                if oid in found:
                    m.results["lasair"] = found[oid]
                else:
                    m.errors["lasair"] = "object not found"
    return objects


LSST_TAG = "extragalactic_lt20mag_candidate"  # Fink: rising, mag < 20, extragalactic
LSST_COLUMNS = ["r:diaObjectId", "r:midpointMjdTai", "r:ra", "r:dec", "r:psfFlux", "r:psfFluxErr", "r:band",
                "f:clf_cats_class"]


def lsst_nuclear_transients(
    days: float = 7,
    mag_max: float = 20.0,
    *,
    max_offset: float = 0.5,
    tag: str = LSST_TAG,
    merge: bool = True,
    use_cache: bool = True,
    now: datetime | None = None,
) -> CandidateSearch:
    """Rubin/LSST transients on a galaxy nucleus with an alert in the last ``days`` days.

    Alerts come from Fink's ``tag`` stream; the brightest alert per object
    must be brighter than ``mag_max``, and the nearest Legacy Surveys DR10
    source must be a galaxy within ``max_offset`` arcsec. If the window has no
    alerts, ``query["latest_alert_mjd"]`` says when the stream last had one.
    """
    now = now or datetime.now(timezone.utc)
    start, stop = (now - timedelta(days=days)).date(), (now + timedelta(days=1)).date()
    historical = now < datetime.now(timezone.utc) - timedelta(days=2)
    ttl = HISTORICAL_TTL if historical else 600
    errors: list[str] = []
    funnel = {"alerts_scanned": 0, "objects_scanned": 0, "bright": 0, "passed_cuts": 0}
    query = {"recipe": "lsst_nuclear_transients", "survey": "lsst", "days": days,
             "startdate": start.isoformat(), "stopdate": stop.isoformat(), "tag": tag,
             "mag_max": mag_max, "max_offset": max_offset, "host_catalogue": hosts.TABLE}
    empty = pd.DataFrame(columns=LSST_CANDIDATE_COLUMNS)

    try:
        alerts = fink_lsst.tags(tag, start.isoformat(), stop.isoformat(), columns=LSST_COLUMNS,
                                use_cache=use_cache, ttl=ttl)
    except BrokerError as exc:
        return CandidateSearch(empty, {}, query, [f"fink lsst tags {tag!r}: {exc}"], funnel=funnel)
    funnel["alerts_scanned"] = len(alerts)
    funnel["objects_scanned"] = len({a.get("r:diaObjectId") for a in alerts})
    if not alerts:
        try:
            last = fink_lsst.tags(tag, n=1, columns=["r:midpointMjdTai"], use_cache=use_cache)
            query["latest_alert_mjd"] = last[0]["r:midpointMjdTai"] if last else None
        except BrokerError as exc:
            errors.append(f"fink lsst latest alert: {exc}")

    best: dict[str, dict] = {}
    for a in alerts:
        mag, _ = flux_to_mag(a.get("r:psfFlux"), a.get("r:psfFluxErr"))
        oid = a.get("r:diaObjectId")
        if mag is None or mag > mag_max or oid is None:
            continue
        oid = str(oid)
        if oid not in best or mag < best[oid]["mag"]:
            best[oid] = {"object_id": oid, "ra": a.get("r:ra"), "dec": a.get("r:dec"), "mag": mag,
                         "band": a.get("r:band"), "mjd": a.get("r:midpointMjdTai"),
                         "sources": f"fink-lsst:{tag}"}
    funnel["bright"] = len(best)

    try:
        found = hosts.nearest_sources({o: (r["ra"], r["dec"]) for o, r in best.items()}, use_cache=use_cache)
    except BrokerError as exc:
        errors.append(f"host catalogue: {exc}")
        found = {}
    rows = []
    for oid, r in best.items():
        host = found.get(oid)
        if hosts.is_nuclear(host, max_offset):
            rows.append({**r, "host_sep": host["sep"], "host_type": host["type"], "host_r50": host["shape_r"]})
    table = pd.DataFrame(rows, columns=LSST_CANDIDATE_COLUMNS)
    funnel["passed_cuts"] = funnel["new"] = len(table)

    objects: dict[str, MergedObject] = {}
    if merge and len(table):
        with ThreadPoolExecutor(max_workers=4) as pool:
            merged = pool.map(lambda o: get_object(o, ["alerce", "fink"], use_cache=use_cache), table["object_id"])
            objects = {m.object_id: m for m in merged}
        table = _add_consensus(table, objects)
    return CandidateSearch(table.sort_values("mag").reset_index(drop=True), objects, query, errors,
                           funnel=funnel)


LSST_CANDIDATE_COLUMNS = ["object_id", "ra", "dec", "mag", "band", "mjd", "host_sep", "host_type", "host_r50",
                          "sources"]
CANDIDATE_COLUMNS = [
    "object_id", "ra", "dec", "mag", "band", "mjd", "distnr", "distpsnr1", "sgscore1",
    "ndethist", "fink_class", "first_seen_mjd", "first_pass_mjd", "sources",
]
HISTORICAL_TTL = 30 * 86400


def _fink_row(a: dict, streams: list[str]) -> dict:
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
        "first_seen_mjd": a["i:jdstarthist"] - JD_TO_MJD if a.get("i:jdstarthist") else None,
        "sources": "; ".join(f"fink:{s}" for s in streams),
    }


def _lasair_row(r: dict) -> dict:
    mags = {b: r.get(f"{b}mag") for b in ("g", "r") if r.get(f"{b}mag") is not None}
    band = min(mags, key=mags.get) if mags else None
    return {
        "object_id": r["objectId"],
        "ra": r.get("ramean"),
        "dec": r.get("decmean"),
        "mag": mags.get(band),
        "band": band,
        "mjd": r["jdmax"] - JD_TO_MJD if r.get("jdmax") else None,
        "ndethist": r.get("ncand"),
        "sources": "lasair:NT",
    }


def flare_onset(mjds: list[float], at: float, gap: float) -> float | None:
    """Start of the run of positive alerts ending at the latest alert before ``at``.

    The run is broken by any gap longer than ``gap`` days. None without alerts.
    """
    past = [m for m in sorted(mjds) if m <= at]
    if not past:
        return None
    start = past[-1]
    for m in reversed(past[:-1]):
        if start - m > gap:
            break
        start = m
    return start


def _flare_history(object_ids: list[str], at: float, gap: float, lookback: float, *,
                   use_cache: bool, errors: list[str]) -> dict[str, tuple[float | None, int]]:
    """(flare onset, positive alerts in the ``lookback`` days before it) per object."""

    def one(oid):
        # ALeRCE has the complete alert history; Fink's is filtered, so it's the fallback.
        try:
            mjds = alerce.positive_alert_mjds(oid, use_cache=use_cache)
        except BrokerError:
            try:
                mjds = fink.positive_alert_mjds(oid, use_cache=use_cache)
            except BrokerError:
                return oid, None
        onset = flare_onset(mjds, at, gap)
        if onset is None:
            return oid, (None, 0)
        return oid, (onset, sum(1 for m in mjds if onset - lookback <= m < onset))

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = dict(pool.map(one, object_ids))
    failed = [o for o, v in results.items() if v is None]
    if failed:
        # Without a history we can't tell, so keep the conservative "known" label.
        errors.append(f"alert history unavailable for {len(failed)} object(s); filed as known")
    return {o: v for o, v in results.items() if v is not None}


def _age_days(row: dict, now: datetime) -> float | None:
    """Days since first detection, from Fink's jdstarthist or the ZTF name.

    ZTF names encode the year of first detection (ZTF26... = 2026); for
    Lasair-only rows that is the best available age, counted from 1 January.
    """
    now_mjd = now.timestamp() / 86400 + 40587
    first = row.get("first_seen_mjd")
    if first is not None and pd.notna(first):
        return now_mjd - first
    oid = str(row.get("object_id", ""))
    if oid.startswith("ZTF") and oid[3:5].isdigit():
        jan1 = datetime(2000 + int(oid[3:5]), 1, 1, tzinfo=timezone.utc)
        return (now - jan1).total_seconds() / 86400
    return None


def _add_consensus(table: pd.DataFrame, objects: dict[str, MergedObject]) -> pd.DataFrame:
    status, cats, labels = [], [], []
    for oid in table["object_id"]:
        c = objects[oid].consensus()
        status.append(c["status"])
        cats.append(", ".join(c["categories"]))
        labels.append("; ".join(f"{v['broker']}:{v['label']}" for v in c["votes"]))
    return table.assign(consensus=status, categories=cats, broker_labels=labels)
