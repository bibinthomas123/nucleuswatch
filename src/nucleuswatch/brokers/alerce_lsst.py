"""ALeRCE for Rubin/LSST (https://alerce.online) — public, no token."""

from __future__ import annotations

from .._http import request_json
from ._lsst import photometry
from .base import BrokerResult, classification_frame, lightcurve_frame

NAME = "alerce"
API = "https://api-lsst.alerce.online"
BANDS = {1: "g", 2: "r", 3: "i", 4: "z", 5: "y", 6: "u"}


def fetch(object_id: str, *, use_cache: bool = True) -> BrokerResult:
    params = {"survey_id": "lsst", "oid": object_id}
    obj = request_json("GET", f"{API}/object_api/object", params=params, use_cache=use_cache)
    lc = request_json("GET", f"{API}/lightcurve_api/lightcurve", params=params, use_cache=use_cache)
    probs = request_json("GET", f"{API}/probability_api/probability", params=params, use_cache=use_cache)
    return parse(object_id, obj, lc, probs)


def parse(object_id: str, obj: dict, lc: dict, probs: list[dict]) -> BrokerResult:
    rows = [
        photometry(d.get("mjd"), BANDS.get(d.get("band")), d.get("psfFlux"), d.get("psfFluxErr"),
                   d.get("measurement_id"))
        for d in lc.get("detections", [])
    ]
    top = [p for p in probs if p.get("ranking") == 1]
    # ALeRCE serves several generations of its Rubin stamp classifier; the one
    # with the highest version is the current model and is the one that votes.
    stamp = [p for p in top if str(p.get("classifier_name", "")).startswith("stamp_classifier")]
    newest = max(stamp, key=lambda p: p.get("classifier_version") or 0, default=None)
    classes = [
        {
            "classifier": "stamp_classifier" if p is newest else p["classifier_name"],
            "label": p.get("class_name"),
            "probability": p.get("probability"),
            "version": f"{p['classifier_name']}:{p.get('classifier_version')}",
        }
        for p in top
    ]
    return BrokerResult(
        broker=NAME,
        object_id=object_id,
        ra=obj.get("meanra"),
        dec=obj.get("meandec"),
        lightcurve=lightcurve_frame(rows, NAME),
        classifications=classification_frame(classes, NAME),
        url=f"https://alerce.online/object/{object_id}?survey=lsst",
    )
