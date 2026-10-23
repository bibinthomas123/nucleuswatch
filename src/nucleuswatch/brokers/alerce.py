"""ALeRCE (https://alerce.online) — public REST API, no token required."""

from __future__ import annotations

from .._http import request_json
from .base import ZTF_BANDS, BrokerResult, classification_frame, lightcurve_frame

NAME = "alerce"
API = "https://api.alerce.online/ztf/v1"


def fetch(object_id: str, *, use_cache: bool = True) -> BrokerResult:
    base = f"{API}/objects/{object_id}"
    obj = request_json("GET", base, use_cache=use_cache)
    probs = request_json("GET", f"{base}/probabilities", use_cache=use_cache)
    lc = request_json("GET", f"{base}/lightcurve", use_cache=use_cache)
    return parse(object_id, obj, probs, lc)


def parse(object_id: str, obj: dict, probs: list[dict], lc: dict) -> BrokerResult:
    detections = [
        {
            "mjd": d.get("mjd"),
            "band": ZTF_BANDS.get(d.get("fid")),
            "mag": d.get("magpsf"),
            "mag_err": d.get("sigmapsf"),
            "candid": d.get("candid"),
        }
        for d in lc.get("detections", [])
    ]

    # ALeRCE reports a full ranked probability vector per classifier; keep the
    # top-ranked class from each so it lines up with other brokers' outputs.
    top = [
        {
            "classifier": p["classifier_name"],
            "label": p["class_name"],
            "probability": p.get("probability"),
            "version": p.get("classifier_version"),
        }
        for p in probs
        if p.get("ranking") == 1
    ]

    return BrokerResult(
        broker=NAME,
        object_id=object_id,
        ra=obj.get("meanra"),
        dec=obj.get("meandec"),
        lightcurve=lightcurve_frame(detections, NAME),
        classifications=classification_frame(top, NAME),
        extra={"stellar": obj.get("stellar")},
        url=f"https://alerce.online/object/{object_id}",
    )
