"""Fink (https://fink-portal.org) — public REST API, no token required.

Fink returns one row per alert. Columns are prefixed by origin: ``i:`` raw
ZTF alert fields, ``d:`` Fink science-module outputs, ``v:`` derived values.
"""

from __future__ import annotations

from datetime import date

from .._http import BrokerError, request_json
from .base import JD_TO_MJD, ZTF_BANDS, BrokerResult, classification_frame, lightcurve_frame

NAME = "fink"
API = "https://api.ztf.fink-portal.org/api/v1"

# Fink science modules that output a score for a specific class.
SCORES = {
    "d:snn_snia_vs_nonia": ("supernnova_snia_vs_nonia", "SNIa"),
    "d:snn_sn_vs_all": ("supernnova_sn_vs_all", "SN"),
    "d:rf_snia_vs_nonia": ("rf_snia_vs_nonia", "SNIa"),
    "d:rf_kn_vs_nonkn": ("rf_kn_vs_nonkn", "Kilonova"),
}


def fetch(object_id: str, *, use_cache: bool = True) -> BrokerResult:
    rows = request_json(
        "POST",
        f"{API}/objects",
        json_body={"objectId": object_id, "output-format": "json"},
        use_cache=use_cache,
    )
    return parse(object_id, rows)


def latests(fink_class: str, startdate: str, stopdate: str, n: int = 1000,
            *, use_cache: bool = True, ttl: float = 600) -> list[dict]:
    """Latest alerts Fink assigned to ``fink_class`` between two ISO dates.

    Returns raw alert rows (one per alert, several per object possible).
    Valid classes are listed by ``GET {API}/classes``.
    """
    return request_json(
        "POST",
        f"{API}/latests",
        json_body={"class": fink_class, "n": str(n), "startdate": startdate,
                   "stopdate": stopdate, "output-format": "json"},
        use_cache=use_cache,
        ttl=ttl,  # default is short because recent windows are still filling
    )


LATESTS_CAP = 1000  # Fink returns at most this many alerts per request


def latests_all(fink_class: str, start: date, stop: date, *, use_cache: bool = True,
                ttl: float = 600) -> list[dict]:
    """Every alert of ``fink_class`` in [start, stop), however many there are.

    Fink caps each request at LATESTS_CAP alerts (the newest ones), so a full
    window is split in half until each piece fits. A single day that still
    hits the cap is returned as-is (capped), which is then a known limit.
    """
    rows = latests(fink_class, start.isoformat(), stop.isoformat(), n=LATESTS_CAP,
                   use_cache=use_cache, ttl=ttl)
    if len(rows) < LATESTS_CAP or (stop - start).days <= 1:
        return rows
    mid = start + (stop - start) / 2
    return (latests_all(fink_class, start, mid, use_cache=use_cache, ttl=ttl)
            + latests_all(fink_class, mid, stop, use_cache=use_cache, ttl=ttl))


CUTOUT_KINDS = ("Science", "Template", "Difference")


def cutout(object_id: str, kind: str = "Science", *, use_cache: bool = True) -> list[list[float]]:
    """Raw pixel values of the latest alert's image stamp (``kind`` in CUTOUT_KINDS).

    Fink also serves PNGs, but those are pre-stretched per image; raw values
    let the caller apply one consistent stretch.
    """
    if kind not in CUTOUT_KINDS:
        raise ValueError(f"kind must be one of {CUTOUT_KINDS}")
    data = request_json(
        "POST",
        f"{API}/cutouts",
        json_body={"objectId": object_id, "kind": kind, "output-format": "array"},
        use_cache=use_cache,
        ttl=30 * 86400,  # stamps of a past alert never change
    )
    try:
        return data[f"b:cutout{kind}_stampData"]
    except (KeyError, TypeError) as exc:
        raise BrokerError(f"fink cutout {kind} for {object_id}: unexpected response") from exc


def parse(object_id: str, rows: list[dict]) -> BrokerResult:
    if not rows:
        return BrokerResult(broker=NAME, object_id=object_id)

    detections = [
        {
            "mjd": r["i:jd"] - JD_TO_MJD if r.get("i:jd") is not None else None,
            "band": ZTF_BANDS.get(r.get("i:fid")),
            "mag": r.get("i:magpsf"),
            "mag_err": r.get("i:sigmapsf"),
            "candid": r.get("i:candid"),
        }
        for r in rows
    ]

    # Classification fields are recomputed per alert; the latest alert holds
    # the most up-to-date view of the object.
    latest = max(rows, key=lambda r: r.get("i:jd") or 0)
    version = latest.get("i:fink_science_version")
    classes = [
        {
            "classifier": "fink_classification",
            "label": latest.get("v:classification"),
            "probability": None,
            "version": version,
        }
    ]
    for col, (classifier, label) in SCORES.items():
        score = latest.get(col)
        if score is not None:
            classes.append(
                {"classifier": classifier, "label": label,
                 "probability": score, "version": version}
            )

    return BrokerResult(
        broker=NAME,
        object_id=object_id,
        ra=latest.get("i:ra"),
        dec=latest.get("i:dec"),
        lightcurve=lightcurve_frame(detections, NAME),
        classifications=classification_frame(classes, NAME),
        extra={
            "simbad": latest.get("d:cdsxmatch"),
            "tns_type": latest.get("d:tns") or None,  # TNS classification, not the name
        },
        url=f"https://fink-portal.org/{object_id}",
    )
