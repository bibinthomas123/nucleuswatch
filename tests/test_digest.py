import json
from datetime import datetime, timezone

import pytest

from nucleuswatch import digest
from nucleuswatch.brokers import fink

from .conftest import load

NOW = datetime(2026, 10, 23, tzinfo=timezone.utc)


LASAIR_NT = [
    # new this year, not in Fink's streams
    {"objectId": "ZTF26aaaaaaa", "ramean": 10.0, "decmean": 5.0, "gmag": 18.5, "rmag": 18.9,
     "jdmax": 2461330.5, "ncand": 4, "separationArcsec": 0.2},
    # an old AGN that is active again
    {"objectId": "ZTF18bbbbbbb", "ramean": 20.0, "decmean": -5.0, "gmag": 17.0, "rmag": None,
     "jdmax": 2461330.5, "ncand": 300, "separationArcsec": 0.1},
]


@pytest.fixture
def offline_digest(offline_brokers, monkeypatch):
    from nucleuswatch.brokers import lasair

    monkeypatch.setattr(fink, "latests",
                        lambda cls, *a, **kw: load("fink_latests_sn.json") if cls == "SN candidate" else [])
    monkeypatch.setattr(fink, "cutout", lambda *a, **kw: [[0.0, 1.0], [1.0, 0.0]])
    monkeypatch.setattr(lasair, "nuclear_candidates", lambda *a, **kw: [dict(r) for r in LASAIR_NT])


def test_build_record_is_json_serialisable(offline_digest):
    record, search = digest.build(14, now=NOW)
    assert record["n_candidates"] == len(search) == len(record["candidates"])
    text = json.dumps(record)  # must not raise
    assert "NaN" not in text
    first = record["candidates"][0]
    assert {"object_id", "consensus", "votes", "links", "tns_name", "tns_type", "tier"} <= set(first)


def test_new_and_known_are_split(offline_digest):
    record, _ = digest.build(14, now=NOW)
    new_ids = {c["object_id"] for c in record["candidates"]}
    known_ids = {k["object_id"] for k in record["known"]}
    assert "ZTF26aaaaaaa" in new_ids and "ZTF18bbbbbbb" in known_ids
    assert record["n_known"] == len(record["known"])
    assert record["funnel"]["new"] == record["n_candidates"]


def test_tiers_follow_bright_mag(offline_digest):
    record, _ = digest.build(14, 20.0, bright_mag=18.0, now=NOW)
    for c in record["candidates"]:
        assert c["tier"] == ("bright" if c["mag"] <= 18.0 else "faint")
    assert all(c["mag"] <= 20.0 for c in record["candidates"])


def test_write_data_keeps_history(offline_digest, tmp_path):
    record, _ = digest.build(14, now=NOW)
    path = digest.write_data(record, tmp_path)
    assert path.name == "2026-10-23.json"
    assert (tmp_path / "2026-10-23.csv").exists()
    assert json.loads((tmp_path / "latest.json").read_text()) == json.loads(path.read_text())


def test_render_site(offline_digest, tmp_path):
    record, search = digest.build(14, now=NOW)
    digest.write_data(record, tmp_path / "data")
    # an older run already in the history
    old = {**record, "generated": "2026-10-16T00:00:00+00:00", "n_candidates": 0, "candidates": []}
    (tmp_path / "data" / "2026-10-16.json").write_text(json.dumps(old))

    page = digest.render_site(record, tmp_path / "data", tmp_path / "site", search)
    html = page.read_text()
    assert "ZTF26acakche" in html and "ZTF26aaaaaaa" in html and "ZTF18bbbbbbb" in html
    assert "<svg" in html  # sky map
    assert html.index("2026-10-23") < html.index("2026-10-16")  # newest first in archive
    assert (tmp_path / "site" / "img" / "ZTF26acakche_lc.png").exists()
    assert (tmp_path / "site" / "img" / "ZTF26acakche_stamps.png").exists()
    assert (tmp_path / "site" / "data" / "2026-10-16.json").exists()
    assert (tmp_path / "site" / ".nojekyll").exists()


def test_site_escapes_broker_text(offline_digest, tmp_path):
    record, _ = digest.build(14, now=NOW)
    record["candidates"][0]["sherlock"] = "<script>alert(1)</script>"
    page = digest.render_site(record, tmp_path, tmp_path / "site")
    assert "<script>" not in page.read_text()


def test_empty_week_renders(tmp_path):
    record = {"generated": "2026-10-23T00:00:00+00:00", "nucleuswatch_version": "x",
              "query": {"startdate": "a", "stopdate": "b", "cuts": {}}, "errors": [],
              "n_candidates": 0, "n_disagree": 0, "candidates": []}
    page = digest.render_site(record, tmp_path, tmp_path / "site")
    assert "No new candidates" in page.read_text()
