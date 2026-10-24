from datetime import datetime, timezone

import pytest

import nucleuswatch as bm
from nucleuswatch.brokers import fink
from nucleuswatch.recipes import NuclearCuts, nuclear_transients

from .conftest import load

NOW = datetime(2026, 10, 23, tzinfo=timezone.utc)


@pytest.fixture
def fink_stream(monkeypatch):
    """Recorded 'SN candidate' stream; other classes return nothing."""
    calls = []

    def latests(cls, start, stop, n=1000, **kw):
        calls.append((cls, start, stop))
        return load("fink_latests_sn.json") if cls == "SN candidate" else []

    monkeypatch.setattr(fink, "latests", latests)
    return calls


def test_cuts_select_nuclear_alerts(fink_stream):
    search = nuclear_transients(14, merge=False, now=NOW)
    assert list(search.candidates["object_id"]) == ["ZTF26acakche", "ZTF26ablsebw"]
    assert (search.candidates["mag"] <= 19).all()
    assert (search.candidates["distnr"] <= 0.5).all()
    assert (search.candidates["sgscore1"] < 0.5).all()


def test_query_window_and_provenance(fink_stream):
    search = nuclear_transients(7, merge=False, now=NOW)
    assert fink_stream[0][1:] == ("2026-10-16", "2026-10-24")
    assert search.query["cuts"]["mag_max"] == 19.0
    assert set(search.query["fink_classes"]) == {c for c, *_ in fink_stream}


def test_tighter_cuts_remove_candidates(fink_stream):
    search = nuclear_transients(14, NuclearCuts(mag_max=18.0), merge=False, now=NOW)
    assert list(search.candidates["object_id"]) == ["ZTF26acakche"]


@pytest.mark.parametrize("override", [
    {"i:sgscore1": 0.9},      # star-like host -> variable star
    {"i:distnr": 2.0},        # offset from the nucleus
    {"i:isdiffpos": "f"},     # negative subtraction
    {"i:ndethist": 1},        # single detection
    {"i:distnr": -999.0},     # missing value must not pass
])
def test_cut_rejections(override):
    alert = {"i:magpsf": 18.0, "i:distnr": 0.2, "i:distpsnr1": 0.3, "i:sgscore1": 0.01,
             "i:ndethist": 5, "i:isdiffpos": "t"}
    assert NuclearCuts().passes(alert)
    assert not NuclearCuts().passes({**alert, **override})


def test_merge_adds_consensus(fink_stream, offline_brokers):
    search = nuclear_transients(14, now=NOW)
    assert {"consensus", "categories", "broker_labels"} <= set(search.candidates.columns)
    assert len(list(search)) == len(search) == 2


def test_failing_class_stream_is_reported(monkeypatch):
    def boom(cls, *a, **kw):
        raise bm.BrokerError("fink down")

    monkeypatch.setattr(fink, "latests", boom)
    search = nuclear_transients(merge=False, now=NOW)
    assert len(search) == 0
    assert search.errors and "fink down" in search.errors[0]


def test_query_records_sources_actually_used(fink_stream):
    past = datetime(2026, 1, 1, tzinfo=timezone.utc)  # Lasair can't search the past
    search = nuclear_transients(7, merge=False, sources=("fink", "lasair"), now=past)
    assert search.query["sources"] == ["fink"]
    assert search.query["sources_requested"] == ["fink", "lasair"]


def test_flare_onset_follows_the_latest_run():
    from nucleuswatch.recipes import flare_onset

    mjds = [100, 110, 500, 510, 520, 530]
    assert flare_onset(mjds, 600, gap=30) == 500   # gap 110 -> 500 breaks the run
    assert flare_onset(mjds, 600, gap=400) == 100  # long gap allowed: one run
    assert flare_onset(mjds, 105, gap=30) == 100   # only alerts up to `at` count
    assert flare_onset([], 600, gap=30) is None


def test_old_object_with_quiet_nucleus_stays_new(monkeypatch):
    """A TDE in a galaxy with an old detection must not be filed as known."""
    from nucleuswatch.brokers import alerce, lasair

    old_id = "ZTF18tdeaaaa"
    rows = [{"objectId": old_id, "ramean": 1.0, "decmean": 2.0, "gmag": 18.0, "rmag": 18.2,
             "jdmax": 2461330.5, "ncand": 5, "separationArcsec": 0.1},
            {"objectId": "ZTF18agnbbbb", "ramean": 3.0, "decmean": 4.0, "gmag": 18.0, "rmag": None,
             "jdmax": 2461330.5, "ncand": 300, "separationArcsec": 0.1}]
    now = datetime.now(timezone.utc)
    now_mjd = now.timestamp() / 86400 + 40587
    histories = {
        old_id: [now_mjd - 2000, now_mjd - 20, now_mjd - 10, now_mjd - 1],      # quiet for years, new flare
        "ZTF18agnbbbb": [now_mjd - d for d in range(0, 1500, 15)],              # always active
    }
    monkeypatch.setattr(fink, "latests", lambda *a, **kw: [])
    monkeypatch.setattr(lasair, "nuclear_candidates", lambda *a, **kw: [dict(r) for r in rows])
    monkeypatch.setattr(alerce, "positive_alert_mjds", lambda oid, **kw: histories[oid])
    s = nuclear_transients(7, sources=("fink", "lasair"), new_within_days=365, merge=False, now=now)
    assert list(s.candidates["object_id"]) == [old_id]
    assert list(s.known["object_id"]) == ["ZTF18agnbbbb"]
    assert s.candidates.iloc[0]["prior_alerts"] == 0


def test_flare_history_falls_back_to_fink_then_files_as_known(monkeypatch):
    from nucleuswatch.brokers import alerce
    from nucleuswatch.recipes import _flare_history

    def down(oid, **kw):
        raise bm.BrokerError("down")

    monkeypatch.setattr(alerce, "positive_alert_mjds", down)
    monkeypatch.setattr(fink, "positive_alert_mjds", lambda oid, **kw: [100.0, 110.0])
    errors = []
    assert _flare_history(["ZTF18aaaaaaa"], 120.0, 30, 730, use_cache=False, errors=errors) == \
        {"ZTF18aaaaaaa": (100.0, 0)}
    monkeypatch.setattr(fink, "positive_alert_mjds", down)
    assert _flare_history(["ZTF18aaaaaaa"], 120.0, 30, 730, use_cache=False, errors=errors) == {}
    assert "filed as known" in errors[-1]
