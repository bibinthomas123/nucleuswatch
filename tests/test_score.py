import math

import pandas as pd

import nucleuswatch as bm
from nucleuswatch import score

from .conftest import OBJECT_ID


def test_features_cover_every_model_input(offline_brokers):
    f = score.features(bm.get_object(OBJECT_ID))
    assert list(f) == score.FEATURES
    assert f["log_ndet"] > 0
    assert not math.isnan(f["g_r"])


def test_stored_model_matches_features():
    model = score.load_model()
    assert model["features"] == score.FEATURES
    assert len(model["coef"]) == len(model["mean"]) == len(score.FEATURES)


def test_score_is_a_fraction(offline_brokers):
    s = score.tde_score(bm.get_object(OBJECT_ID))
    assert 0 <= s <= 1


def _lc(g_minus_r_start, g_minus_r_end):
    """A 60-day rise-and-fall light curve with a linearly changing colour."""
    rows = []
    for i, t in enumerate(range(0, 100, 4)):
        r = 19 - 1.5 * math.exp(-((t - 30) / 25) ** 2)
        c = g_minus_r_start + (g_minus_r_end - g_minus_r_start) * i / 24
        rows += [{"mjd": 60000.0 + t, "band": "r", "mag": r}, {"mjd": 60000.2 + t, "band": "g", "mag": r + c}]
    return pd.DataFrame(rows)


def test_colour_measured_from_same_night_pairs():
    g_r, slope = score._colour(_lc(-0.2, 0.8))
    assert abs(g_r - 0.3) < 0.05
    assert slope > 0.5  # reddening by ~1 mag over 96 days


def test_blue_constant_colour_ranks_above_reddening():
    model = score.load_model()
    base = dict(model["fill"])
    blue = {**base, "g_r": -0.2, "g_r_slope": 0.0}
    red = {**base, "g_r": 0.4, "g_r_slope": 1.0}
    tde_like, sn_like = score.apply(model, [blue, red])
    assert tde_like > sn_like
