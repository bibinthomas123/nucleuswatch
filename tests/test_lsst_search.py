from nucleuswatch import hosts, recipes
from nucleuswatch.brokers import fink_lsst

ALERTS = [
    # on a galaxy nucleus, bright
    {"r:diaObjectId": 1001, "r:midpointMjdTai": 61230.0, "r:ra": 10.0, "r:dec": -20.0, "r:psfFlux": 50000.0,
     "r:psfFluxErr": 500.0, "r:band": "i"},
    # the same object, fainter: the brightest alert is kept
    {"r:diaObjectId": 1001, "r:midpointMjdTai": 61228.0, "r:ra": 10.0, "r:dec": -20.0, "r:psfFlux": 20000.0,
     "r:psfFluxErr": 500.0, "r:band": "r"},
    # on a star
    {"r:diaObjectId": 1002, "r:midpointMjdTai": 61230.0, "r:ra": 30.0, "r:dec": -20.0, "r:psfFlux": 50000.0,
     "r:psfFluxErr": 500.0, "r:band": "i"},
    # offset 1.5" from its galaxy
    {"r:diaObjectId": 1003, "r:midpointMjdTai": 61230.0, "r:ra": 50.0, "r:dec": -20.0, "r:psfFlux": 50000.0,
     "r:psfFluxErr": 500.0, "r:band": "i"},
    # too faint (mag ~ 21.6)
    {"r:diaObjectId": 1004, "r:midpointMjdTai": 61230.0, "r:ra": 70.0, "r:dec": -20.0, "r:psfFlux": 8000.0,
     "r:psfFluxErr": 500.0, "r:band": "i"},
    # negative difference flux
    {"r:diaObjectId": 1005, "r:midpointMjdTai": 61230.0, "r:ra": 90.0, "r:dec": -20.0, "r:psfFlux": -9000.0,
     "r:psfFluxErr": 500.0, "r:band": "i"},
]
CATALOGUE = [
    {"ra": 10.0 + 0.1 / 3600, "dec": -20.0, "type": "SER", "shape_r": 1.2},
    {"ra": 30.0, "dec": -20.0 + 0.05 / 3600, "type": "PSF", "shape_r": 0.0},
    {"ra": 50.0, "dec": -20.0 + 1.5 / 3600, "type": "EXP", "shape_r": 2.0},
]


def test_nearest_source_and_nucleus_test(monkeypatch):
    monkeypatch.setattr(hosts, "_query", lambda pos, **kw: CATALOGUE)
    found = hosts.nearest_sources({"a": (10.0, -20.0), "b": (30.0, -20.0), "c": (50.0, -20.0), "d": (5.0, 5.0)})
    assert found["a"]["type"] == "SER" and abs(found["a"]["sep"] - 0.094) < 0.01  # 0.1" of RA at dec -20
    assert found["d"] is None
    assert hosts.is_nuclear(found["a"], 0.5)
    assert not hosts.is_nuclear(found["b"], 0.5)  # a star
    assert not hosts.is_nuclear(found["c"], 0.5)  # off-nucleus
    assert not hosts.is_nuclear(found["d"], 0.5)


def test_lsst_search_keeps_bright_nuclear_objects(monkeypatch):
    monkeypatch.setattr(fink_lsst, "tags", lambda *a, **kw: [dict(x) for x in ALERTS])
    monkeypatch.setattr(hosts, "_query", lambda pos, **kw: CATALOGUE)
    s = recipes.lsst_nuclear_transients(7, 20.0, merge=False)
    assert list(s.candidates["object_id"]) == ["1001"]
    row = s.candidates.iloc[0]
    assert row["band"] == "i" and row["host_type"] == "SER"
    assert s.funnel == {"alerts_scanned": 6, "objects_scanned": 5, "bright": 3, "passed_cuts": 1, "new": 1}


def test_quiet_stream_reports_last_alert(monkeypatch):
    def tags(tag, startdate=None, stopdate=None, n=20000, **kw):
        return [{"r:midpointMjdTai": 61235.4}] if n == 1 else []

    monkeypatch.setattr(fink_lsst, "tags", tags)
    s = recipes.lsst_nuclear_transients(7)
    assert len(s.candidates) == 0
    assert s.query["latest_alert_mjd"] == 61235.4
