import pandas as pd
import pytest

from nucleuswatch.brokers import alerce, fink, lasair
from nucleuswatch.brokers.base import CLASSIFICATION_COLUMNS, LIGHTCURVE_COLUMNS

from .conftest import OBJECT_ID, load


def check_schema(result):
    assert list(result.lightcurve.columns) == LIGHTCURVE_COLUMNS
    assert list(result.classifications.columns) == CLASSIFICATION_COLUMNS
    assert result.lightcurve["mjd"].is_monotonic_increasing
    assert set(result.lightcurve["band"].dropna()) <= {"g", "r", "i"}


def test_alerce_parse():
    r = alerce.parse(OBJECT_ID, load("alerce_object.json"), load("alerce_probabilities.json"),
                     load("alerce_lightcurve.json"))
    check_schema(r)
    assert r.ndet == 14
    assert r.ra == pytest.approx(193.8217, rel=1e-3)
    # one top-ranked row per classifier version
    assert not r.classifications.duplicated(["classifier", "version"]).any()
    stamp = r.classifications.set_index("classifier").loc["stamp_classifier"]
    assert stamp["label"] == "SN"


def test_fink_parse_uses_latest_alert_and_converts_jd():
    r = fink.parse(OBJECT_ID, load("fink_objects.json"))
    check_schema(r)
    assert r.ndet == 14
    # MJD, not JD
    assert 59000 < r.lightcurve["mjd"].min() < 60000
    labels = r.classifications.set_index("classifier")["label"]
    assert labels["fink_classification"] == "SN candidate"


def test_alerce_and_fink_see_the_same_alerts():
    a = alerce.parse(OBJECT_ID, load("alerce_object.json"), load("alerce_probabilities.json"),
                     load("alerce_lightcurve.json"))
    f = fink.parse(OBJECT_ID, load("fink_objects.json"))
    assert set(a.lightcurve["candid"]) == set(f.lightcurve["candid"])


def test_lasair_skips_upper_limits():
    r = lasair.parse(OBJECT_ID, load("lasair_object.json"))
    check_schema(r)
    assert r.lightcurve["candid"].notna().all()
    assert r.classifications.iloc[0]["label"] == "SN"


def test_fink_empty_response():
    r = fink.parse(OBJECT_ID, [])
    assert r.ndet == 0
    assert isinstance(r.lightcurve, pd.DataFrame)



def test_lasair_real_response():
    r = lasair.parse(OBJECT_ID, load("lasair_object.json"))
    assert r.ndet == 14
    assert r.extra["tns"] == "2021pkz"
    assert "<" not in r.extra["sherlock_description"]
    assert "NGC4799" in r.extra["sherlock_description"]


def test_all_three_brokers_see_the_same_alerts():
    a = alerce.parse(OBJECT_ID, load("alerce_object.json"), load("alerce_probabilities.json"),
                     load("alerce_lightcurve.json"))
    la = lasair.parse(OBJECT_ID, load("lasair_object.json"))
    assert set(a.lightcurve["candid"]) == set(la.lightcurve["candid"])


@pytest.mark.parametrize("line", ["LASAIR_TOKEN=abc", "export LASAIR_TOKEN='abc'", ' LASAIR_TOKEN = "abc" '])
def test_lasair_token_from_dotenv(tmp_path, line):
    env = tmp_path / ".env"
    env.write_text(f"OTHER=1\n{line}\n")
    assert lasair._token_from_dotenv(env) == "abc"


def test_lasair_dotenv_missing(tmp_path):
    assert lasair._token_from_dotenv(tmp_path / ".env") is None
