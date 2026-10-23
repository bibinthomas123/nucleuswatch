"""Rubin/LSST adapters. Fixtures are real API responses (see tests/data)."""

import math

import pytest

import nucleuswatch as nw
from nucleuswatch.brokers import alerce_lsst, fink_lsst, lasair_lsst
from nucleuswatch.brokers.base import LIGHTCURVE_COLUMNS, flux_to_mag
from nucleuswatch.core import survey_of

from .conftest import load

LSST_ID = "170587117485817955"


def alerce_result():
    return alerce_lsst.parse(LSST_ID, load("alerce_lsst_object.json"), load("alerce_lsst_lightcurve.json"),
                             load("alerce_lsst_probabilities.json"))


@pytest.fixture
def offline_lsst(monkeypatch):
    monkeypatch.setattr(alerce_lsst, "fetch", lambda oid, **kw: alerce_result())
    monkeypatch.setattr(fink_lsst, "fetch", lambda oid, **kw: fink_lsst.parse(oid, load("fink_lsst_sources.json")))
    monkeypatch.setattr(lasair_lsst, "fetch", lambda oid, **kw: (_ for _ in ()).throw(
        nw.BrokerError("no Lasair LSST token")))


def test_survey_detection():
    assert survey_of("ZTF21abfmbix") == "ztf"
    assert survey_of(LSST_ID) == "lsst"
    assert survey_of(f" {LSST_ID} ") == "lsst"
    with pytest.raises(ValueError, match="diaObjectId"):
        survey_of("12345")


def test_flux_to_mag():
    mag, err = flux_to_mag(3631e9, 3631e8)  # 3631 Jy is AB magnitude 0
    assert mag == pytest.approx(0.0, abs=1e-3)
    assert err == pytest.approx(2.5 / math.log(10) * 0.1)
    assert flux_to_mag(-10.0, 1.0) == (None, None)
    assert flux_to_mag(None, None) == (None, None)


def test_alerce_lsst_newest_stamp_model_votes():
    r = alerce_result()
    assert list(r.lightcurve.columns) == LIGHTCURVE_COLUMNS
    assert r.ndet == 16
    cls = r.classifications.set_index("classifier")
    assert cls.loc["stamp_classifier", "label"] == "SN"
    assert "stamp_classifier_rubin_beta_20260421" in cls.loc["stamp_classifier", "version"]
    # the older model is kept, under its own name, so it doesn't vote
    assert "stamp_classifier_rubin_beta" in cls.index


def test_fink_lsst_parse():
    r = fink_lsst.parse(LSST_ID, load("fink_lsst_sources.json"))
    assert r.ndet == 16
    assert set(r.lightcurve["band"]) <= set("ugrizy")
    assert r.lightcurve["flux"].notna().all()
    cls = r.classifications.set_index("classifier")
    assert cls.loc["cats", "label"] == "SN-like"
    assert r.extra["tns"] == "AT 2026qhm"


def test_lasair_lsst_keeps_negative_fluxes():
    r = lasair_lsst.parse("313761042336317573", load("lasair_lsst_object.json"))
    lc = r.lightcurve
    assert len(lc) == 40
    negative = lc[lc["flux"] <= 0]
    assert len(negative) > 0 and negative["mag"].isna().all()
    assert r.classifications.iloc[0]["label"] == "SN"  # parsed from a Python-literal string
    assert r.extra["tns"] is None


def test_lasair_lsst_needs_its_own_token(monkeypatch, tmp_path):
    monkeypatch.delenv("LASAIR_LSST_TOKEN", raising=False)
    monkeypatch.setenv("LASAIR_TOKEN", "ztf-token")  # must not be used for LSST
    monkeypatch.chdir(tmp_path)
    with pytest.raises(nw.BrokerError, match="LASAIR_LSST_TOKEN"):
        lasair_lsst.fetch(LSST_ID)


def test_lasair_lsst_invalid_token_message():
    with pytest.raises(nw.BrokerError, match="Invalid token"):
        lasair_lsst.parse(LSST_ID, {"detail": "Invalid token."})


def test_get_object_lsst(offline_lsst):
    obj = nw.get_object(LSST_ID)
    assert obj.survey == "lsst"
    assert set(obj.brokers) == {"alerce", "fink"}
    c = obj.consensus()
    assert c["status"] == "agree" and c["categories"] == ["SN"]
    # ALeRCE and Fink report the same diaSources
    cov = obj.coverage()
    assert cov["alerce"].all() and cov["fink"].all()


def test_lsst_plot_uses_flux(offline_lsst):
    import matplotlib
    matplotlib.use("Agg")
    obj = nw.get_object(LSST_ID)
    ax = obj.plot()
    assert "flux" in ax.get_ylabel().lower()
    assert not ax.yaxis_inverted()
    ax = obj.plot(annotate=False)
    assert "flux" in ax.get_ylabel().lower()


@pytest.mark.parametrize("label, expected", [
    ("SN-like", "SN"), ("Non-periodic", "AGN"), ("Periodic", "VarStar"), ("Fast", None), ("Long", None),
])
def test_cats_labels(label, expected):
    assert nw.categorize(label) == expected
