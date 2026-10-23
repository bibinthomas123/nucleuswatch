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


class FakeQuery:
    """Stands in for lasair._query; records every call."""

    def __init__(self, tns_error=None):
        self.calls, self.tns_error = [], tns_error

    def __call__(self, selected, tables, conditions, **kw):
        self.calls.append((tables, conditions))
        ids = [part.strip(" '") for part in conditions[conditions.index("(") + 1:-1].split(",")]
        if "crossmatch_tns" in tables:
            if self.tns_error:
                raise self.tns_error
            return [{"objectId": ids[0], "tns_name": "2026abc"}]
        return [{"objectId": o, "ramean": 1.0, "decmean": 2.0, "classification": "SN",
                 "description": "<em>near</em> a galaxy"} for o in ids if o != "ZTF26zzzzzzz"]


def test_lasair_batch_uses_two_calls_per_hundred(monkeypatch):
    fake = FakeQuery()
    monkeypatch.setattr(lasair, "_query", fake)
    ids = ["ZTF26abcdefg", "ZTF26zzzzzzz"]
    out = lasair.batch_results(ids)
    assert len(fake.calls) == 2  # Sherlock + TNS, not one per object
    assert "ZTF26zzzzzzz" not in out  # unknown to Lasair
    r = out["ZTF26abcdefg"]
    assert r.classifications.iloc[0]["label"] == "SN"
    assert r.extra["sherlock_description"] == "near a galaxy"


def test_lasair_batch_rejects_unsafe_ids(monkeypatch):
    fake = FakeQuery()
    monkeypatch.setattr(lasair, "_query", fake)
    lasair.batch_results(["ZTF26abcdefg", "x'); DROP TABLE objects; --"])
    assert all("DROP" not in cond for _, cond in fake.calls)


def test_lasair_batch_survives_tns_failure(monkeypatch):
    from nucleuswatch import BrokerError

    monkeypatch.setattr(lasair, "_query", FakeQuery(tns_error=BrokerError("rate limit exceeded")))
    out = lasair.batch_results(["ZTF26abcdefg"])
    assert out["ZTF26abcdefg"].classifications.iloc[0]["label"] == "SN"


def test_merge_records_lasair_failure_per_object(offline_brokers, monkeypatch):
    from nucleuswatch import BrokerError
    from nucleuswatch.recipes import merge_objects

    def limited(ids, **kw):
        raise BrokerError("https://lasair-ztf.lsst.ac.uk/api/query/: rate limit exceeded")

    monkeypatch.setattr(lasair, "batch_results", limited)
    objs = merge_objects([OBJECT_ID])
    assert "lasair" not in objs[OBJECT_ID].results
    assert "rate limit" in objs[OBJECT_ID].errors["lasair"]
    assert objs[OBJECT_ID].results["alerce"].ndet > 0
