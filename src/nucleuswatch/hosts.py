"""Host galaxies for Rubin/LSST positions, from the Legacy Surveys DR10 catalogue.

ZTF alerts carry their own host information (distance to the nearest
reference source, a Pan-STARRS star/galaxy score), so the ZTF nuclear cuts
read it straight from the alert. Rubin alerts don't yet, so for LSST
candidates the nearest catalogued source is looked up here, in the Legacy
Surveys DR10 Tractor catalogue (DECam imaging, which covers most of the Rubin
sky), through NOIRLab's Astro Data Lab TAP service.

A Tractor ``type`` of PSF means a point source (a star or a quasar); REX, EXP,
DEV and SER are galaxy models.
"""

from __future__ import annotations

import csv
import io
import json
import math

from ._http import BrokerError, _cache_path, _fresh, _send, _store

TAP = "https://datalab.noirlab.edu/tap/sync"
TABLE = "ls_dr10.tractor"
SEARCH_ARCSEC = 2.0  # look this far for the nearest source
BATCH = 25  # positions per ADQL query
TTL = 30 * 86400  # the catalogue doesn't change
GALAXY_TYPES = {"REX", "EXP", "DEV", "SER"}


def _sep_arcsec(ra1, dec1, ra2, dec2) -> float:
    d1, d2 = math.radians(dec1), math.radians(dec2)
    dra = math.radians(ra2 - ra1)
    c = math.sin(d1) * math.sin(d2) + math.cos(d1) * math.cos(d2) * math.cos(dra)
    return math.degrees(math.acos(max(-1.0, min(1.0, c)))) * 3600


def _query(positions: list[tuple[float, float]], *, use_cache: bool) -> list[dict]:
    radius = SEARCH_ARCSEC / 3600
    where = " OR ".join(f"'t'=q3c_radial_query(ra, dec, {ra:.7f}, {dec:.7f}, {radius:.7f})"
                        for ra, dec in positions)
    params = {"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "csv",
              "QUERY": f"SELECT ra, dec, type, shape_r FROM {TABLE} WHERE {where}"}
    path = _cache_path("GET", TAP, params, None, None, ".json")
    if use_cache and _fresh(path, TTL):
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            pass
    text = _send("GET", TAP, params=params, timeout=120).text
    if text.lstrip().startswith("<"):  # TAP reports errors as a VOTable
        raise BrokerError(f"{TAP}: catalogue query failed")
    rows = [{"ra": float(r["ra"]), "dec": float(r["dec"]), "type": r["type"].strip(),
             "shape_r": float(r["shape_r"]) if r.get("shape_r") else None}
            for r in csv.DictReader(io.StringIO(text))]
    if use_cache:
        _store(path, json.dumps(rows).encode())
    return rows


def nearest_sources(positions: dict, *, use_cache: bool = True) -> dict:
    """Nearest DR10 source within 2″ of each position, or None.

    ``positions`` maps an id to (ra, dec) in degrees. Each result has ``sep``
    (arcsec), ``type`` (Tractor model) and ``shape_r`` (half-light radius,
    arcsec). None means nothing catalogued nearby, which includes positions
    outside the DR10 footprint.
    """
    items = list(positions.items())
    out: dict = {}
    for i in range(0, len(items), BATCH):
        chunk = items[i:i + BATCH]
        found = _query([p for _, p in chunk], use_cache=use_cache)
        for key, (ra, dec) in chunk:
            near = [(_sep_arcsec(ra, dec, s["ra"], s["dec"]), s) for s in found]
            near = [(d, s) for d, s in near if d <= SEARCH_ARCSEC]
            if near:
                d, s = min(near, key=lambda t: t[0])
                out[key] = {"sep": round(d, 3), "type": s["type"], "shape_r": s["shape_r"]}
            else:
                out[key] = None
    return out


def is_nuclear(host: dict | None, max_offset: float) -> bool:
    """On the centre of a galaxy: the nearest source is a galaxy model within ``max_offset``″."""
    return host is not None and host["type"] in GALAXY_TYPES and host["sep"] <= max_offset
