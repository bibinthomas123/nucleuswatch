"""Host galaxies for Rubin/LSST positions, from the Legacy Surveys DR10 catalogue.

ZTF alerts carry their own host information (distance to the nearest
reference source, a Pan-STARRS star/galaxy score), so the ZTF nuclear cuts
read it straight from the alert. Rubin alerts don't yet, so for LSST
candidates the nearest catalogued source is looked up here, in the Legacy
Surveys DR10 Tractor catalogue (DECam imaging, which covers most of the Rubin
sky), through NOIRLab's Astro Data Lab TAP service.

The same lookup gives host properties for the TDE score: dereddened g, r, z
magnitudes, size, and a redshift (a spectroscopic one from the photo-z
training set where it exists, otherwise the photometric median). ZTF objects
use DR9, which covers both the northern (BASS/MzLS) and southern (DECaLS)
Legacy Surveys; Rubin objects use the deeper, DECam-only DR10.

A Tractor ``type`` of PSF means a point source (a star or a quasar); REX, EXP,
DEV and SER are galaxy models.
"""

from __future__ import annotations

import csv
import io
import json
import math

from . import _http
from ._http import BrokerError

TAP = "https://datalab.noirlab.edu/tap/sync"
RELEASES = {"dr10": "ls_dr10", "dr9": "ls_dr9"}
TABLE = "ls_dr10.tractor"  # Rubin default
SEARCH_ARCSEC = 2.0  # look this far for the nearest source
BATCH = 25  # positions per ADQL query
TTL = 30 * 86400  # the catalogue doesn't change
GALAXY_TYPES = {"REX", "EXP", "DEV", "SER"}


def _sep_arcsec(ra1, dec1, ra2, dec2) -> float:
    d1, d2 = math.radians(dec1), math.radians(dec2)
    dra = math.radians(ra2 - ra1)
    c = math.sin(d1) * math.sin(d2) + math.cos(d1) * math.cos(d2) * math.cos(dra)
    return math.degrees(math.acos(max(-1.0, min(1.0, c)))) * 3600


COLUMNS = ("t.ra, t.dec, t.type, t.shape_r, t.flux_g, t.flux_r, t.flux_z, t.mw_transmission_g, "
           "t.mw_transmission_r, t.mw_transmission_z, p.z_phot_median, p.z_phot_std, p.z_spec")


def _mag(flux: str | None, transmission: str | None) -> float | None:
    """Dereddened AB magnitude from a Tractor flux (nanomaggies)."""
    try:
        f, mw = float(flux), float(transmission)
    except (TypeError, ValueError):
        return None
    return 22.5 - 2.5 * math.log10(f / mw) if f > 0 and mw > 0 else None


def _num(v: str | None) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) else x


def _redshift(row: dict) -> tuple[float | None, float | None, bool]:
    spec = _num(row.get("z_spec"))
    if spec is not None and spec > 0:
        return spec, 0.0, True
    z = _num(row.get("z_phot_median"))
    return (z, _num(row.get("z_phot_std")), False) if z is not None and z > 0 else (None, None, False)


def _query(positions: list[tuple[float, float]], *, use_cache: bool, release: str = "dr10") -> list[dict]:
    radius = SEARCH_ARCSEC / 3600
    schema = RELEASES[release]
    where = " OR ".join(f"'t'=q3c_radial_query(t.ra, t.dec, {ra:.7f}, {dec:.7f}, {radius:.7f})"
                        for ra, dec in positions)
    params = {"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "csv",
              "QUERY": (f"SELECT {COLUMNS} FROM {schema}.tractor t "
                        f"LEFT JOIN {schema}.photo_z p ON t.ls_id = p.ls_id WHERE {where}")}
    path = _http._cache_path("GET", TAP, params, None, None, ".json")
    if use_cache and _http._fresh(path, TTL):
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            pass
    # Through the module, so the offline tests' network guard applies here too.
    text = _http._send("GET", TAP, params=params, timeout=120).text
    if text.lstrip().startswith("<"):  # TAP reports errors as a VOTable
        raise BrokerError(f"{TAP}: catalogue query failed")
    rows = []
    for r in csv.DictReader(io.StringIO(text)):
        z, z_err, z_is_spec = _redshift(r)
        rows.append({"ra": float(r["ra"]), "dec": float(r["dec"]), "type": r["type"].strip(),
                     "shape_r": _num(r.get("shape_r")),
                     "mag_g": _mag(r.get("flux_g"), r.get("mw_transmission_g")),
                     "mag_r": _mag(r.get("flux_r"), r.get("mw_transmission_r")),
                     "mag_z": _mag(r.get("flux_z"), r.get("mw_transmission_z")),
                     "z": z, "z_err": z_err, "z_is_spec": z_is_spec})
    if use_cache:
        _http._store(path, json.dumps(rows).encode())
    return rows


def nearest_sources(positions: dict, *, use_cache: bool = True, release: str = "dr10") -> dict:
    """Nearest Legacy Surveys source within 2″ of each position, or None.

    ``positions`` maps an id to (ra, dec) in degrees; ``release`` is "dr10"
    (Rubin) or "dr9" (ZTF). Each result has ``sep`` (arcsec), ``type``
    (Tractor model), ``shape_r`` (half-light radius, arcsec), dereddened
    ``mag_g``, ``mag_r``, ``mag_z``, and ``z``, ``z_err``, ``z_is_spec``
    (None where unknown). None means nothing catalogued nearby, which
    includes positions outside the survey footprint.
    """
    items = list(positions.items())
    out: dict = {}
    for i in range(0, len(items), BATCH):
        chunk = items[i:i + BATCH]
        found = _query([p for _, p in chunk], use_cache=use_cache, release=release)
        for key, (ra, dec) in chunk:
            near = [(_sep_arcsec(ra, dec, s["ra"], s["dec"]), s) for s in found]
            near = [(d, s) for d, s in near if d <= SEARCH_ARCSEC]
            if near:
                d, s = min(near, key=lambda t: t[0])
                out[key] = {"sep": round(d, 3), **{k: v for k, v in s.items() if k not in ("ra", "dec")}}
            else:
                out[key] = None
    return out


def is_nuclear(host: dict | None, max_offset: float) -> bool:
    """On the centre of a galaxy: the nearest source is a galaxy model within ``max_offset``″."""
    return host is not None and host["type"] in GALAXY_TYPES and host["sep"] <= max_offset
