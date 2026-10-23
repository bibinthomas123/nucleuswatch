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
