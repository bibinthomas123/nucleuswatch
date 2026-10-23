"""Lasair for Rubin/LSST (https://lasair.lsst.ac.uk) — needs its own token.

Lasair runs a separate LSST server with separate accounts: set
``LASAIR_LSST_TOKEN`` (env or ``.env``). The ZTF ``LASAIR_TOKEN`` is rejected.
"""

from __future__ import annotations

import os

from .._http import BrokerError, request_json
from ._lsst import literal, photometry
from .base import BrokerResult, classification_frame, lightcurve_frame
from .lasair import _strip_html, _token_from_dotenv

NAME = "lasair"
API = "https://api.lasair.lsst.ac.uk/api"
TOKEN_VAR = "LASAIR_LSST_TOKEN"


def fetch(object_id: str, *, use_cache: bool = True, token: str | None = None) -> BrokerResult:
    token = token or os.environ.get(TOKEN_VAR) or _token_from_dotenv(key=TOKEN_VAR)
    if not token:
        raise BrokerError(f"no Lasair LSST token; set {TOKEN_VAR} (separate from the ZTF token)")
    obj = request_json(
        "POST",
        f"{API}/object/",
        data={"objectId": object_id, "lasair_added": "true"},
        headers={"Authorization": f"Token {token}"},
        use_cache=use_cache,
    )
    return parse(object_id, obj)


def parse(object_id: str, obj: dict) -> BrokerResult:
    if set(obj) == {"detail"}:
        raise BrokerError(f"lasair lsst: {obj['detail']}")
    rows = [
        photometry(s.get("midpointMjdTai"), s.get("band"), s.get("psfFlux"), s.get("psfFluxErr"),
                   s.get("diaSourceId"))
        for s in obj.get("diaSourcesList", [])
    ]
    data = obj.get("lasairData") or {}
    sherlock = literal(data.get("sherlock"))
    classes = []
    if sherlock.get("classification"):
        classes.append({"classifier": "sherlock", "label": sherlock["classification"],
                        "probability": None, "version": None})
    tns = literal(data.get("TNS")).get("name") or data.get("tns_name")
    dia = obj.get("diaObject") or {}
    return BrokerResult(
        broker=NAME,
        object_id=object_id,
        ra=dia.get("ra"),
        dec=dia.get("decl"),
        lightcurve=lightcurve_frame(rows, NAME),
        classifications=classification_frame(classes, NAME),
        extra={
            "sherlock_description": _strip_html(sherlock.get("description")),
            "tns": None if tns in (None, "None", "") else tns,
        },
        url=f"https://lasair.lsst.ac.uk/objects/{object_id}/",
    )
