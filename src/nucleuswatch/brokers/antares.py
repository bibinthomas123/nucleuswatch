"""ANTARES (https://antares.noirlab.edu) — NOIRLab's broker, public, no token.

ANTARES has no general-purpose classifier. What it adds is context: its
filters tag each locus (``nuclear_transient``, ``extragalactic``, ...) and it
crossmatches catalogues, including the Milliquas and Véron-Cetty quasar/AGN
catalogues and a catalogue of post-starburst galaxies, hosts that TDEs are
unusually fond of. The vote is therefore catalogue-based: ``AGN`` when the
locus matches a quasar/AGN catalogue, otherwise ANTARES abstains.
"""

from __future__ import annotations

import csv
import io
import json

from .._http import BrokerError, request_json
from .base import BrokerResult, classification_frame, lightcurve_frame

NAME = "antares"
API = "https://api.antares.noirlab.edu/v1"
AGN_CATALOGS = ("milliquas", "veron_agn_qso")
POST_STARBURST_CATALOGS = ("french_post_starburst_gals",)
BANDS = {"g": "g", "R": "r", "r": "r", "i": "i"}


def fetch(object_id: str, *, use_cache: bool = True) -> BrokerResult:
    query = {"query": {"bool": {"filter": {"term": {"properties.ztf_object_id": object_id}}}}}
    listing = request_json("GET", f"{API}/loci", params={"elasticsearch_query[locus_listing]": json.dumps(query)},
                           use_cache=use_cache)
    hits = listing.get("data") or []
    if not hits:
        raise BrokerError(f"{API}/loci: {object_id} not found")
    locus = request_json("GET", f"{API}/loci/{hits[0]['id']}", use_cache=use_cache)
    return parse(object_id, hits[0], locus.get("data", {}))


def parse(object_id: str, listing: dict, locus: dict) -> BrokerResult:
    attrs = listing.get("attributes", {})
    catalogs = list(attrs.get("catalogs") or [])
    tags = list(attrs.get("tags") or [])
    rows = []
    text = (locus.get("attributes") or {}).get("lightcurve") or ""
    for r in csv.DictReader(io.StringIO(text)):
        if not r.get("ant_mag") or not r.get("alert_id", "").startswith("ztf_candidate:"):
            continue  # upper limits, or non-ZTF points
        rows.append({"mjd": float(r["ant_mjd"]), "band": BANDS.get(r.get("ant_passband")),
                     "mag": float(r["ant_mag"]), "mag_err": float(r["ant_magerr"]) if r.get("ant_magerr") else None,
                     "candid": int(r["alert_id"].split(":", 1)[1])})
    classes = []
    agn = [c for c in catalogs if c in AGN_CATALOGS]
    if agn:
        classes.append({"classifier": "catalog_agn", "label": "AGN", "probability": None,
                        "version": ",".join(agn)})
    return BrokerResult(
        broker=NAME,
        object_id=object_id,
        ra=attrs.get("ra"),
        dec=attrs.get("dec"),
        lightcurve=lightcurve_frame(rows, NAME),
        classifications=classification_frame(classes, NAME),
        extra={"antares_tags": ", ".join(tags), "antares_catalogs": ", ".join(catalogs),
               "post_starburst_host": any(c in POST_STARBURST_CATALOGS for c in catalogs) or None},
        url=f"https://antares.noirlab.edu/loci/{listing.get('id')}",
    )
