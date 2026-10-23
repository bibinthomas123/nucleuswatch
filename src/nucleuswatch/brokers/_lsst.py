"""Helpers shared by the Rubin/LSST adapters (``*_lsst.py``).

LSST objects are identified by an integer ``diaObjectId``. Photometry is a
difference-image flux in nJy, which can be negative, so LSST adapters fill
the ``flux``/``flux_err`` columns and derive ``mag`` only where flux > 0.
"""

from __future__ import annotations

import ast

from .base import flux_to_mag


def photometry(mjd, band, flux, flux_err, candid) -> dict:
    mag, mag_err = flux_to_mag(flux, flux_err)
    return {"mjd": mjd, "band": band, "mag": mag, "mag_err": mag_err,
            "flux": flux, "flux_err": flux_err, "candid": candid}


def literal(value) -> dict:
    """Lasair LSST serialises some nested fields as Python-literal strings."""
    if isinstance(value, dict):
        return value
    if not value or not isinstance(value, str):
        return {}
    try:
        parsed = ast.literal_eval(value)  # literals only; never executes code
    except (ValueError, SyntaxError):
        return {}
    return parsed if isinstance(parsed, dict) else {}
