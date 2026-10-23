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


def nuclear_candidates(days: float = 7, mag_max: float = 20.0, max_separation: float = 1.0, *,
                       token: str | None = None, use_cache: bool = True, limit: int = 1000) -> list[dict]:
    """Objects Sherlock classifies as nuclear transients (``NT``), active recently.

    Uses Lasair's query API: the object must have a detection in the last
    ``days`` days, at least two detections, a latest g or r magnitude brighter
    than ``mag_max``, and lie within ``max_separation`` arcsec of the host
    galaxy's core. Returns one dict per object. Costs one API call.
    """
    conditions = (
        "sherlock_classifications.classification = 'NT'"
        f" AND sherlock_classifications.separationArcsec <= {float(max_separation)}"
        f" AND objects.jdmax > JDNOW() - {float(days)}"
        " AND objects.ncand >= 2"
        f" AND (objects.gmag <= {float(mag_max)} OR objects.rmag <= {float(mag_max)})"
    )
    return _query(
        "objects.objectId, objects.ramean, objects.decmean, objects.gmag, objects.rmag,"
        " objects.jdmax, objects.ncand, sherlock_classifications.separationArcsec",
        "objects, sherlock_classifications", conditions, limit=limit, token=token,
        use_cache=use_cache, ttl=600,
    )


BATCH = 100  # object ids per query
_ZTF_ID = re.compile(r"^ZTF\d{2}[a-z]{7}$")


def batch_results(object_ids: list[str], *, token: str | None = None,
                  use_cache: bool = True) -> dict[str, BrokerResult]:
    """Sherlock classification, host description and TNS name for many objects.

    Two query-API calls per 100 objects instead of one ``/object/`` call each,
    which keeps a weekly digest far below Lasair's 100 calls/hour. The results
    carry no light curve (ALeRCE and Fink report the same ZTF alerts).
    Objects Lasair doesn't know are absent from the returned dict.
    """
    ids = sorted({o for o in object_ids if _ZTF_ID.match(o)})  # ids go into SQL: validate strictly
    out: dict[str, BrokerResult] = {}
    tns: dict[str, str] = {}
    for i in range(0, len(ids), BATCH):
        chunk = ids[i:i + BATCH]
        members = ", ".join(f"'{o}'" for o in chunk)
        rows = _query(
            "objects.objectId, objects.ramean, objects.decmean,"
            " sherlock_classifications.classification, sherlock_classifications.description",
            "objects, sherlock_classifications", f"objects.objectId IN ({members})",
            limit=len(chunk), token=token, use_cache=use_cache,
        )
        for r in rows:
            out[r["objectId"]] = _batch_result(r)
        try:
            for r in _query("objects.objectId, crossmatch_tns.tns_name", "objects, crossmatch_tns",
                            f"objects.objectId IN ({members})", limit=len(chunk), token=token,
                            use_cache=use_cache):
                if r.get("tns_name"):
                    tns[r["objectId"]] = r["tns_name"]
        except BrokerError:
            pass  # TNS names are optional; never lose the Sherlock labels over them
    for oid, name in tns.items():
        if oid in out:
            out[oid].extra["tns"] = name
    return out


def _batch_result(r: dict) -> BrokerResult:
    classes = []
    if r.get("classification"):
        classes.append({"classifier": "sherlock", "label": r["classification"],
                        "probability": None, "version": None})
    return BrokerResult(
        broker=NAME,
        object_id=r["objectId"],
        ra=r.get("ramean"),
        dec=r.get("decmean"),
        classifications=classification_frame(classes, NAME),
        extra={"sherlock_description": _strip_html(r.get("description")), "tns": None},
        url=f"https://lasair-ztf.lsst.ac.uk/objects/{r['objectId']}/",
    )


def _query(selected: str, tables: str, conditions: str, *, limit: int, token: str | None,
           use_cache: bool, ttl: float = 6 * 3600) -> list[dict]:
    token = token or os.environ.get("LASAIR_TOKEN") or _token_from_dotenv()
    if not token:
        raise BrokerError("no Lasair token; set LASAIR_TOKEN (env or .env) to include Lasair")
    rows = request_json(
        "POST",
        f"{API}/query/",
        data={"selected": selected, "tables": tables, "conditions": conditions, "limit": limit},
        headers={"Authorization": f"Token {token}"},
        use_cache=use_cache,
        ttl=ttl,
    )
    if isinstance(rows, dict) and "error" in rows:
        raise BrokerError(f"lasair query: {rows['error']}")
    return rows


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
