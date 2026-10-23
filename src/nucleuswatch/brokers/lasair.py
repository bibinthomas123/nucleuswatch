"""Lasair (https://lasair-ztf.lsst.ac.uk) — needs a free API token.

Create an account on the Lasair website, copy your token from your profile
page, and either set the ``LASAIR_TOKEN`` environment variable, put a
``LASAIR_TOKEN=...`` line in a ``.env`` file in the working directory, or pass
``lasair_token=`` to :func:`nucleuswatch.get_object`. Without a token this
broker is skipped.
"""

from __future__ import annotations

import html
import os
import re
from pathlib import Path

from .._http import BrokerError, request_json
from .base import ZTF_BANDS, BrokerResult, classification_frame, lightcurve_frame

NAME = "lasair"
API = "https://lasair-ztf.lsst.ac.uk/api"


def fetch(object_id: str, *, use_cache: bool = True, token: str | None = None) -> BrokerResult:
    token = token or os.environ.get("LASAIR_TOKEN") or _token_from_dotenv()
    if not token:
        raise BrokerError("no Lasair token; set LASAIR_TOKEN (env or .env) to include Lasair")
    obj = request_json(
        "POST",
        f"{API}/object/",
        data={"objectId": object_id, "lite": "true", "lasair_added": "true"},
        headers={"Authorization": f"Token {token}"},
        use_cache=use_cache,
    )
    return parse(object_id, obj)


def _token_from_dotenv(path: str | os.PathLike = ".env", key: str = "LASAIR_TOKEN") -> str | None:
    """Read ``key`` from a .env file without adding a dependency."""
    try:
        lines = Path(path).read_text().splitlines()
    except OSError:
        return None
    for line in lines:
        name, sep, value = line.strip().partition("=")
        if sep and name.strip().removeprefix("export ").strip() == key:
            return value.strip().strip("'\"") or None
    return None


def _strip_html(text: str | None) -> str | None:
    return html.unescape(re.sub(r"<[^>]+>", "", text)) if text else text


def parse(object_id: str, obj: dict) -> BrokerResult:
    if "error" in obj:
        raise BrokerError(f"lasair: {obj['error']}")

    # Lasair mixes detections and non-detection upper limits in one list;
    # only real detections carry a candid.
    detections = [
        {
            "mjd": c.get("mjd"),
            "band": ZTF_BANDS.get(c.get("fid")),
            "mag": c.get("magpsf"),
            "mag_err": c.get("sigmapsf"),
            "candid": c.get("candid"),
        }
        for c in obj.get("candidates", [])
        if c.get("candid")
    ]

    sherlock = obj.get("sherlock") or {}
    classes = []
    if sherlock.get("classification"):
        classes.append(
            {"classifier": "sherlock", "label": sherlock["classification"],
             "probability": None, "version": None}
        )

    data = obj.get("objectData") or {}
    tns = obj.get("TNS") or {}
    return BrokerResult(
        broker=NAME,
        object_id=object_id,
        ra=data.get("ramean"),
        dec=data.get("decmean"),
        lightcurve=lightcurve_frame(detections, NAME),
        classifications=classification_frame(classes, NAME),
        extra={
            "sherlock_description": _strip_html(sherlock.get("description")),
            "tns": tns.get("name"),
        },
        url=f"https://lasair-ztf.lsst.ac.uk/objects/{object_id}/",
    )
