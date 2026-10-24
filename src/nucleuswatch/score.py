"""How TDE-like is a nuclear transient? A small logistic model over broker outputs.

The features are things every candidate has: ALeRCE's light curve and
classifiers, and Fink's alert-time SIMBAD crossmatch and SuperNNova score.
The weights are fitted by ``validation/tde_score.py`` on TNS-classified
nuclear objects and stored in ``assets/tde_score.json``; this module only
computes features and applies the stored model.

The score is a ranking aid, not a probability you can quote: it was fitted on
retrospective light curves and a few dozen TDEs. See validation/TDE_SCORE.md.
"""

from __future__ import annotations

import json
import math
from functools import lru_cache
from importlib.resources import files

import numpy as np

from .taxonomy import categorize

MODEL_FILE = "tde_score.json"
PAIR_DAYS = 1.0  # g and r detections this close in time make a colour

# Deliberately absent: whether ALeRCE has a forced-photometry classification
# yet. In the archive it happens to correlate with being a TDE, but in the live
# digest it mostly marks how new an object is, so it would rank on age.
FEATURES = [
    "alerce_tde",          # ALeRCE forced-photometry classifier's top class is TDE
    "alerce_transient_tde",  # its transient-branch sub-classifier's top class is TDE
    "simbad_agn",          # Fink's alert-time SIMBAD crossmatch is an AGN/QSO
    "snn_sn",              # SuperNNova P(SN vs all)
    "disagree",            # ALeRCE and Fink vote for different categories
    "g_r",                 # median g - r colour
    "g_r_slope",           # change of g - r per 100 days
    "log_rise",            # log10(1 + days from first detection to peak)
    "log_ndet",            # log10(number of detections)
    "amplitude",           # brightest to faintest detection, mag
]


def _top(cls, broker: str, classifier: str) -> str | None:
    rows = cls[(cls["broker"] == broker) & (cls["classifier"] == classifier)]
    if not len(rows):
        return None
    p = rows["probability"].astype(float).fillna(-1)
    return rows.loc[p.idxmax(), "label"]


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


def features(obj) -> dict[str, float]:
    """Feature values for one MergedObject (NaN where unavailable)."""
    cls = obj.classifications
    lc = obj.lightcurve
    # ALeRCE ingests every alert; Fink's history is a quality-filtered subset,
    # so use ALeRCE's light curve when there is one.
    if (lc["broker"] == "alerce").any():
        lc = lc[lc["broker"] == "alerce"]
    lc = lc.dropna(subset=["mjd", "mag"])

    forced = _top(cls, "alerce", "lc_classifier_BHRF_forced_phot")
    transient = _top(cls, "alerce", "lc_classifier_BHRF_forced_phot_transient")
    fink = obj.results.get("fink")
    simbad = fink.extra.get("simbad") if fink is not None else None
    snn = math.nan
    if fink is not None and len(fink.classifications):
        s = fink.classifications.set_index("classifier")["probability"]
        snn = float(s.get("supernnova_sn_vs_all", math.nan))

    votes = {v["broker"]: v["category"] for v in obj.consensus()["votes"]}
    disagree = float("alerce" in votes and "fink" in votes and votes["alerce"] != votes["fink"])

    g_r, slope = _colour(lc)
    if len(lc):
        peak = lc.loc[lc["mag"].idxmin()]
        rise = float(peak["mjd"] - lc["mjd"].min())
        ndet, amp = len(lc), float(lc["mag"].max() - lc["mag"].min())
    else:
        rise, ndet, amp = math.nan, 0, math.nan
    return {
        "alerce_tde": float(categorize(forced) == "TDE"),
        "alerce_transient_tde": float(categorize(transient) == "TDE"),
        "simbad_agn": float(categorize(simbad) == "AGN"),
        "snn_sn": snn,
        "disagree": disagree,
        "g_r": g_r,
        "g_r_slope": slope,
        "log_rise": math.log10(1 + rise) if not math.isnan(rise) else math.nan,
        "log_ndet": math.log10(ndet) if ndet else math.nan,
        "amplitude": amp,
    }


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


def tde_score(obj) -> float | None:
    """Model output in [0, 1] for one object; None if it has no ALeRCE data."""
    if "alerce" not in obj.results:
        return None
    return float(apply(load_model(), [features(obj)])[0])
