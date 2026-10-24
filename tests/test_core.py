import pytest

import nucleuswatch as bm
from nucleuswatch.brokers import fink
from nucleuswatch.taxonomy import categorize

from .conftest import OBJECT_ID


def test_get_object_merges_all_brokers(offline_brokers):
    obj = bm.get_object(OBJECT_ID)
    assert obj.brokers == ["alerce", "fink", "lasair", "antares"]
    assert not obj.errors
    assert set(obj.lightcurve["broker"]) == {"alerce", "fink", "lasair", "antares"}
    assert {"category", "votes"} <= set(obj.classifications.columns)


def test_summary_positions_agree(offline_brokers):
    s = bm.get_object(OBJECT_ID).summary()
    assert (s["offset_arcsec"] < 1).all()


def test_coverage_matches_on_candid(offline_brokers):
    cov = bm.get_object(OBJECT_ID).coverage()
    assert cov["alerce"].all() and cov["fink"].all()
    # brokers process the same ZTF alert, so magnitudes should be identical
    assert (cov["mag_spread"].dropna() < 1e-3).all()


def test_consensus(offline_brokers):
    c = bm.get_object(OBJECT_ID).consensus()
    assert c["status"] == "agree"
    assert c["categories"] == ["SN"]
    assert {v["broker"] for v in c["votes"]} == {"alerce", "fink", "lasair"}


def test_failing_broker_does_not_break_the_call(offline_brokers, monkeypatch):
    def boom(oid, **kw):
        raise bm.BrokerError("fink is down")

    monkeypatch.setattr(fink, "fetch", boom)
    obj = bm.get_object(OBJECT_ID)
    assert "fink" not in obj.brokers
    assert obj.errors["fink"] == "fink is down"


def test_lasair_without_token_is_skipped(monkeypatch, tmp_path):
    monkeypatch.delenv("LASAIR_TOKEN", raising=False)
    monkeypatch.chdir(tmp_path)  # no .env here
    with pytest.raises(bm.BrokerError, match="token"):
        from nucleuswatch.brokers import lasair
        lasair.fetch(OBJECT_ID)


@pytest.mark.parametrize("bad", ["ZTF21", "2459396", "ztf21abfmbix", ""])
def test_rejects_bad_ids(bad):
    with pytest.raises(ValueError):
        bm.get_object(bad)


def test_rejects_unknown_broker():
    with pytest.raises(ValueError, match="unknown broker"):
        bm.get_object(OBJECT_ID, brokers=["nope"])


@pytest.mark.parametrize("label, expected", [
    ("SNIbc", "SN"), ("SN candidate", "SN"), ("Early SN Ia candidate", "SN"), ("SN", "SN"),
    ("QSO", "AGN"), ("Blazar", "AGN"), ("Seyfert_1", "AGN"),
    ("CV/Nova", "CV/Nova"), ("CataclyV*", "CV/Nova"),
    ("RRL", "VarStar"), ("EB*", "VarStar"), ("VS", "VarStar"),
    ("NT", None), ("TDE", "TDE"), ("Kilonova candidate", "Kilonova"),
    ("Solar System MPC", "SolarSystem"), ("bogus", "Bogus"),
    ("Unknown", None), ("Ambiguous", None), ("UNCLEAR", None), (None, None),
])
def test_categorize(label, expected):
    assert categorize(label) == expected


def test_plot(offline_brokers):
    import matplotlib
    matplotlib.use("Agg")
    ax = bm.get_object(OBJECT_ID).plot()
    assert ax.yaxis_inverted()


def test_duplicate_classifier_versions_vote_once(offline_brokers, monkeypatch):
    from nucleuswatch.brokers import alerce

    from .conftest import load

    monkeypatch.setattr(alerce, "fetch", lambda oid, **kw: alerce.parse(
        oid, load("alerce_object.json"), load("alerce_probabilities_multiversion.json"),
        load("alerce_lightcurve.json"),
    ))
    obj = bm.get_object(OBJECT_ID)
    cls = obj.classifications
    assert (cls["classifier"] == "lc_classifier").sum() == 2  # both versions kept
    votes = [v for v in obj.consensus()["votes"] if v["broker"] == "alerce"]
    assert len(votes) == 1
    # The forced-photometry classifier answered, so it casts ALeRCE's vote.
    assert votes[0]["classifier"] == "lc_classifier_BHRF_forced_phot"


def test_scores_get_no_category(offline_brokers):
    cls = bm.get_object(OBJECT_ID).classifications
    assert cls.loc[~cls["votes"], "category"].isna().all()


def test_disagreement(offline_brokers, monkeypatch):
    from nucleuswatch.brokers import lasair

    from .conftest import load

    data = load("lasair_object.json")
    data["sherlock"]["classification"] = "AGN"
    monkeypatch.setattr(lasair, "fetch", lambda oid, **kw: lasair.parse(oid, data))
    c = bm.get_object(OBJECT_ID).consensus()
    assert c["status"] == "disagree"
    assert c["categories"] == ["AGN", "SN"]


def test_one_vote_per_broker_falls_back_to_light_curve_classifier(offline_brokers):
    # No forced-photometry classification yet: the alert light curve votes.
    votes = bm.get_object(OBJECT_ID).consensus()["votes"]
    assert sorted(v["broker"] for v in votes) == ["alerce", "fink", "lasair"]
    assert next(v for v in votes if v["broker"] == "alerce")["classifier"] == "lc_classifier"


def test_sherlock_nt_abstains(offline_brokers, monkeypatch):
    from nucleuswatch.brokers import lasair

    from .conftest import load

    data = load("lasair_object.json")
    data["sherlock"]["classification"] = "NT"
    monkeypatch.setattr(lasair, "fetch", lambda oid, **kw: lasair.parse(oid, data))
    c = bm.get_object(OBJECT_ID).consensus()
    assert "lasair" not in {v["broker"] for v in c["votes"]}
    assert c["status"] == "agree"  # ALeRCE and Fink still agree on SN


def test_antares_votes_agn_only_on_a_catalogue_match():
    from nucleuswatch.brokers import antares

    from .conftest import load

    listing = load("antares_listing.json")
    r = antares.parse(OBJECT_ID, listing, load("antares_locus.json"))
    assert r.classifications.empty  # no AGN catalogue: abstains
    assert len(r.lightcurve) == 14 and set(r.lightcurve["band"]) <= {"g", "r"}
    listing["attributes"]["catalogs"] = ["milliquas", "ned"]
    r = antares.parse(OBJECT_ID, listing, load("antares_locus.json"))
    assert r.classifications.iloc[0]["label"] == "AGN"
    assert categorize(r.classifications.iloc[0]["label"]) == "AGN"
