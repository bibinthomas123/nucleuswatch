"""Fink for Rubin/LSST (https://lsst.fink-portal.org) — public, no token.

Columns are prefixed ``r:`` (Rubin alert fields) and ``f:`` (Fink added).
"""

from __future__ import annotations

from .._http import request_json
from ._lsst import photometry
from .base import BrokerResult, classification_frame, lightcurve_frame

NAME = "fink"
API = "https://api.lsst.fink-portal.org/api/v1"

# CATS broad classes (f:clf_cats_class), as documented in the Fink LSST schema.
CATS_CLASSES = {11: "SN-like", 12: "Fast", 13: "Long", 21: "Periodic", 22: "Non-periodic"}

SCORES = {
    "f:clf_snnSnVsOthers_score": ("supernnova_sn_vs_others", "SN"),
    "f:clf_earlySNIa_score": ("early_snia", "SNIa"),
}


def fetch(object_id: str, *, use_cache: bool = True) -> BrokerResult:
    sources = request_json(
        "GET", f"{API}/sources", params={"diaObjectId": object_id, "output-format": "json"},
        use_cache=use_cache,
    )
    return parse(object_id, sources)


def parse(object_id: str, sources: list[dict]) -> BrokerResult:
    if not sources:
        return BrokerResult(broker=NAME, object_id=object_id)
    rows = [
        photometry(s.get("r:midpointMjdTai"), s.get("r:band"), s.get("r:psfFlux"), s.get("r:psfFluxErr"),
                   s.get("r:diaSourceId"))
        for s in sources
    ]
    latest = max(sources, key=lambda s: s.get("r:midpointMjdTai") or 0)
    version = latest.get("f:fink_science_version")
    classes = []
    cats = latest.get("f:clf_cats_class")
    if cats is not None and cats != -1:  # -1 = not processed
        classes.append({"classifier": "cats", "label": CATS_CLASSES.get(cats, str(cats)),
                        "probability": latest.get("f:clf_cats_score"), "version": version})
    for col, (classifier, label) in SCORES.items():
        score = latest.get(col)
        if score is not None and score >= 0:  # -1 = not applicable
            classes.append({"classifier": classifier, "label": label, "probability": score,
                            "version": version})
    return BrokerResult(
        broker=NAME,
        object_id=object_id,
        ra=latest.get("r:ra"),
        dec=latest.get("r:dec"),
        lightcurve=lightcurve_frame(rows, NAME),
        classifications=classification_frame(classes, NAME),
        extra={
            "simbad": latest.get("f:xm_simbad_otype"),
            "tns": latest.get("f:xm_tns_fullname"),
            "tns_type": latest.get("f:xm_tns_type"),
        },
        url=f"https://lsst.fink-portal.org/{object_id}",
    )


def tags(tag: str, startdate: str | None = None, stopdate: str | None = None, n: int = 20000,
         columns: list[str] | None = None, *, use_cache: bool = True, ttl: float = 600) -> list[dict]:
    """Alerts Fink tagged ``tag`` (see /api/v1/tags for the list), newest first.

    With no dates, returns the latest ``n`` alerts with that tag.
    """
    body: dict = {"tag": tag, "n": n, "output-format": "json"}
    if startdate:
        body["startdate"] = str(startdate)
    if stopdate:
        body["stopdate"] = str(stopdate)
    if columns:
        body["columns"] = ",".join(columns)
    return request_json("POST", f"{API}/tags", json_body=body, use_cache=use_cache, ttl=ttl) or []
