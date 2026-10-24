"""How TDE-like is a nuclear transient? A small logistic model.

The features are things known when the weekly search picks an object: its
ZTF light curve so far (colour, colour change, rise, fade, amplitude), its
host galaxy from the Legacy Surveys (colour, size, luminosity, and the
transient's peak absolute magnitude from the host's redshift; see
:mod:`nucleuswatch.hosts`), and Fink's alert-time SIMBAD crossmatch.

Broker classifications (ALeRCE's TDE vote, consensus) are deliberately left
out: the training set only has today's classifications, not what the brokers
said when each object was first picked, so including them would flatter the
model. They are still shown next to the score.

The weights are fitted by ``validation/tde_score.py`` on TNS-classified
nuclear objects, using each light curve as it looked at selection and 1, 2,
4 and 8 weeks later, and stored in ``assets/tde_score.json``. The score ranks
candidates; it isn't a calibrated probability. See validation/TDE_SCORE.md.
"""

from __future__ import annotations

import json
import math
from functools import lru_cache
from importlib.resources import files

import numpy as np

from ._http import BrokerError
from .taxonomy import categorize

MODEL_FILE = "tde_score.json"
PAIR_DAYS = 1.0       # g and r detections this close in time make a colour
H0, OMEGA_M = 70.0, 0.3
C_KMS = 299792.458

FEATURES = [
    "g_r",             # median g - r colour of the transient
    "g_r_slope",       # change of g - r per 100 days
    "log_rise",        # log10(1 + days from first detection to peak)
    "fade_rate",       # mag/day after peak (positive = fading); NaN before the peak is passed
    "amplitude",       # brightest to faintest detection, mag
    "log_ndet",        # log10(number of detections)
    "peak_abs_mag",    # transient peak magnitude minus the host's distance modulus
    "host_g_r",        # host colour, dereddened
    "host_abs_r",      # host r-band absolute magnitude
    "host_log_size",   # log10 of the host's half-light radius (arcsec)
    "host_point",      # the host is a point source (Tractor PSF): quasar-like
    "simbad_agn",      # Fink's alert-time SIMBAD crossmatch is an AGN/QSO
]


def distance_modulus(z: float) -> float:
    """Flat ΛCDM (H0 = 70, Ωm = 0.3) distance modulus; NaN for z <= 0."""
    if z is None or not z > 0:
        return math.nan
    zz = np.linspace(0.0, z, 256)
    inv_e = 1.0 / np.sqrt(OMEGA_M * (1 + zz) ** 3 + 1 - OMEGA_M)
    comoving = C_KMS / H0 * float(np.sum((inv_e[1:] + inv_e[:-1]) / 2 * np.diff(zz)))  # Mpc
    return 5 * math.log10((1 + z) * comoving * 1e6 / 10)


def _colour(lc) -> tuple[float, float]:
    g = lc[lc["band"] == "g"].sort_values("mjd")
    r = lc[lc["band"] == "r"].sort_values("mjd")
    if g.empty or r.empty:
        return math.nan, math.nan
    rm, rv = r["mjd"].to_numpy(), r["mag"].to_numpy()
    t, c = [], []
    for mjd, mag in zip(g["mjd"], g["mag"]):
        i = int(np.argmin(np.abs(rm - mjd)))
        if abs(rm[i] - mjd) <= PAIR_DAYS:
            t.append(mjd)
            c.append(mag - rv[i])
    if not c:
        return math.nan, math.nan
    slope = math.nan
    if len(c) >= 3 and np.ptp(t) >= 10:
        slope = float(np.polyfit(np.array(t) - t[0], c, 1)[0] * 100)
    return float(np.median(c)), slope


def _fade_rate(lc, peak_mjd: float) -> float:
    after = lc[lc["mjd"] > peak_mjd + 3]
    if len(after) < 2 or np.ptp(after["mjd"]) < 5:
        return math.nan
    rates = []
    for _, band in after.groupby("band"):
        if len(band) >= 2 and np.ptp(band["mjd"]) >= 5:
            rates.append(np.polyfit(band["mjd"], band["mag"], 1)[0])
    return float(np.mean(rates)) if rates else math.nan


def light_curve(obj, at: float | None = None):
    """The object's detections up to MJD ``at`` (ALeRCE's when it has them)."""
    lc = obj.lightcurve
    # ALeRCE ingests every alert; Fink's history is a quality-filtered subset.
    if (lc["broker"] == "alerce").any():
        lc = lc[lc["broker"] == "alerce"]
    lc = lc.dropna(subset=["mjd", "mag"])
    return lc if at is None else lc[lc["mjd"] <= at]


def features(obj, host: dict | None = None, at: float | None = None) -> dict[str, float]:
    """Feature values for one MergedObject, as known at MJD ``at`` (default: now).

    ``host`` is the object's entry from :func:`hosts.nearest_sources` (None if
    no catalogued host). Missing values are NaN.
    """
    lc = light_curve(obj, at)
    g_r, slope = _colour(lc)
    if len(lc):
        peak = lc.loc[lc["mag"].idxmin()]
        rise = float(peak["mjd"] - lc["mjd"].min())
        fade = _fade_rate(lc, float(peak["mjd"]))
        ndet, amp, peak_mag = len(lc), float(lc["mag"].max() - lc["mag"].min()), float(peak["mag"])
    else:
        rise = fade = amp = peak_mag = math.nan
        ndet = 0

    host = host or {}
    mu = distance_modulus(host.get("z"))
    hg, hr = host.get("mag_g"), host.get("mag_r")
    size = host.get("shape_r")
    fink = obj.results.get("fink")
    simbad = fink.extra.get("simbad") if fink is not None else None
    nan = math.nan
    return {
        "g_r": g_r,
        "g_r_slope": slope,
        "log_rise": math.log10(1 + rise) if not math.isnan(rise) else nan,
        "fade_rate": fade,
        "amplitude": amp,
        "log_ndet": math.log10(ndet) if ndet else nan,
        "peak_abs_mag": peak_mag - mu,
        "host_g_r": hg - hr if hg is not None and hr is not None else nan,
        "host_abs_r": hr - mu if hr is not None else nan,
        "host_log_size": math.log10(size) if size and size > 0 else nan,
        "host_point": float(host["type"] == "PSF") if host.get("type") else nan,
        "simbad_agn": float(categorize(simbad) == "AGN"),
    }


def position(obj) -> tuple[float, float] | None:
    """Median position over the brokers that report one."""
    pos = [(r.ra, r.dec) for r in obj.results.values() if r.ra is not None and r.dec is not None]
    if not pos:
        return None
    return float(np.median([p[0] for p in pos])), float(np.median([p[1] for p in pos]))


def hosts_for(objects: dict, *, use_cache: bool = True) -> dict:
    """Host entries for many MergedObjects, in as few catalogue queries as possible.

    ZTF objects are matched in Legacy Surveys DR9, Rubin objects in DR10.
    Objects without a position, or whose lookup failed, map to None.
    """
    from . import hosts

    out: dict = {oid: None for oid in objects}
    for survey, release in (("ztf", "dr9"), ("lsst", "dr10")):
        pos = {oid: position(o) for oid, o in objects.items() if o.survey == survey}
        pos = {k: v for k, v in pos.items() if v is not None}
        if not pos:
            continue
        try:
            out.update(hosts.nearest_sources(pos, use_cache=use_cache, release=release))
        except BrokerError:
            pass  # scored without host features (filled with training medians)
    return out


def design(rows: list[dict], fill: dict[str, float]) -> np.ndarray:
    """Feature matrix with missing values replaced by ``fill`` (training medians)."""
    x = np.array([[r[f] for f in FEATURES] for r in rows], dtype=float)
    for j, f in enumerate(FEATURES):
        x[np.isnan(x[:, j]), j] = fill[f]
    return x


@lru_cache(maxsize=1)
def load_model() -> dict:
    return json.loads(files("nucleuswatch").joinpath("assets", MODEL_FILE).read_text())


def apply(model: dict, rows: list[dict]) -> np.ndarray:
    x = design(rows, model["fill"])
    z = (x - np.array(model["mean"])) / np.array(model["scale"])
    return 1 / (1 + np.exp(-(z @ np.array(model["coef"]) + model["intercept"])))


def tde_score(obj, host: dict | None = None, *, lookup_host: bool = True) -> float | None:
    """Score in [0, 1] for one ZTF object; None if it has no light curve.

    Without ``host``, the host is looked up (one catalogue query); pass
    ``lookup_host=False`` to score without host features. For many objects
    use :func:`tde_scores`, which batches the lookups.
    """
    if obj.survey != "ztf" or light_curve(obj).empty:
        return None
    if host is None and lookup_host:
        host = hosts_for({obj.object_id: obj}).get(obj.object_id)
    return float(apply(load_model(), [features(obj, host)])[0])


def tde_scores(objects: dict, *, use_cache: bool = True) -> tuple[dict, dict]:
    """Scores for many objects at once: ({object_id: score or None}, {object_id: host}).

    Only ZTF objects are scored; the model was fitted on ZTF light curves.
    """
    found = hosts_for(objects, use_cache=use_cache)
    scores = {oid: tde_score(o, found.get(oid), lookup_host=False) for oid, o in objects.items()}
    return scores, found
