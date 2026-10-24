import json
from datetime import datetime, timezone

import pytest

from nucleuswatch import digest
from nucleuswatch.brokers import alerce, fink, fink_lsst

from .conftest import load

# "Now", not a fixed date: the Lasair source only searches windows ending today.
NOW = datetime.now(timezone.utc)
STAMP = NOW.date().isoformat()


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
    monkeypatch.setattr(fink_lsst, "tags", lambda *a, **kw: [])  # Rubin stream quiet
    from nucleuswatch import hosts
    monkeypatch.setattr(hosts, "_query", lambda *a, **kw: [])  # no catalogued hosts
    now_mjd = NOW.timestamp() / 86400 + 40587
    # The old AGN has fired every 10 days for years; everything else has no history.
    busy = [now_mjd - d for d in range(0, 1500, 10)]
    monkeypatch.setattr(alerce, "positive_alert_mjds",
                        lambda oid, **kw: busy if oid == "ZTF18bbbbbbb" else [])


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
    assert path.name == f"{STAMP}.json"
    assert (tmp_path / f"{STAMP}.csv").exists()
    assert json.loads((tmp_path / "latest.json").read_text()) == json.loads(path.read_text())


def test_render_site(offline_digest, tmp_path):
    record, search = digest.build(14, now=NOW)
    digest.write_data(record, tmp_path / "data")
    # an older run already in the history
    old = {**record, "generated": "2020-01-01T00:00:00+00:00", "n_candidates": 0, "candidates": []}
    (tmp_path / "data" / "2020-01-01.json").write_text(json.dumps(old))

    page = digest.render_site(record, tmp_path / "data", tmp_path / "site", search)
    html = page.read_text()
    assert "ZTF26acakche" in html and "ZTF26aaaaaaa" in html and "ZTF18bbbbbbb" in html
    assert "<svg" in html  # sky map
    assert html.index(STAMP) < html.index("2020-01-01")  # newest first in archive
    assert (tmp_path / "site" / "img" / "ZTF26acakche_lc.png").exists()
    assert (tmp_path / "site" / "img" / "ZTF26acakche_stamps.png").exists()
    assert (tmp_path / "site" / "data" / "2020-01-01.json").exists()
    assert (tmp_path / "site" / "img" / "sky_equatorial.jpg").exists()
    assert 'href="img/sky_equatorial.jpg"' in html and "ESA/Gaia/DPAC" in html
    assert (tmp_path / "site" / ".nojekyll").exists()


def test_site_escapes_broker_text(offline_digest, tmp_path):
    record, _ = digest.build(14, now=NOW)
    record["candidates"][0]["sherlock"] = "<script>alert(1)</script>"
    page = digest.render_site(record, tmp_path, tmp_path / "site")
    html = page.read_text()
    assert "<script>alert(1)" not in html
    assert "&lt;script&gt;alert(1)" in html
    assert html.count("<script>") == 1  # only the page's own observability script


def test_empty_week_renders(tmp_path):
    record = {"generated": "2026-10-23T00:00:00+00:00", "nucleuswatch_version": "x",
              "query": {"startdate": "a", "stopdate": "b", "cuts": {}}, "errors": [],
              "n_candidates": 0, "n_disagree": 0, "candidates": []}
    page = digest.render_site(record, tmp_path, tmp_path / "site")
    assert "No new candidates" in page.read_text()


def test_citation_version_matches_package():
    from pathlib import Path

    import nucleuswatch

    cff = (Path(__file__).parents[1] / "CITATION.cff").read_text()
    assert f"version: {nucleuswatch.__version__}\n" in cff


def test_mark_returning(tmp_path):
    (tmp_path / "2026-10-12.json").write_text(json.dumps({"candidates": [{"object_id": "A"}]}))
    (tmp_path / "2026-10-19.json").write_text(json.dumps({"candidates": [{"object_id": "A"}, {"object_id": "B"}]}))
    (tmp_path / "2026-10-26.json").write_text(json.dumps({"candidates": [{"object_id": "C"}]}))  # this week's
    record = {"generated": "2026-10-26T06:00:00+00:00",
              "candidates": [{"object_id": "A"}, {"object_id": "B"}, {"object_id": "C"}]}
    digest.mark_returning(record, tmp_path)
    by = {c["object_id"]: c for c in record["candidates"]}
    assert by["A"]["first_listed"] == "2026-10-12" and by["A"]["returning"]
    assert by["B"]["first_listed"] == "2026-10-19"
    assert by["C"]["first_listed"] == "2026-10-26" and not by["C"]["returning"]
    assert record["n_new_this_week"] == 1
