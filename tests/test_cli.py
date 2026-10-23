from datetime import datetime, timezone

import pytest

from nucleuswatch import cli, recipes
from nucleuswatch.brokers import fink

from .conftest import OBJECT_ID, load


def test_object_command(offline_brokers, capsys):
    assert cli.main(["object", OBJECT_ID]) == 0
    out = capsys.readouterr().out
    assert "Consensus: AGREE" in out


def test_nuclear_command_exports(offline_brokers, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(fink, "latests",
                        lambda cls, *a, **kw: load("fink_latests_sn.json") if cls == "SN candidate" else [])
    monkeypatch.setattr(fink, "cutout", lambda *a, **kw: [[0.0, 1.0], [1.0, 0.0]])
    real = recipes.nuclear_transients
    monkeypatch.setattr(recipes, "nuclear_transients",
                        lambda *a, **kw: real(*a, now=datetime(2026, 10, 23, tzinfo=timezone.utc), **kw))

    rc = cli.main(["nuclear", "--days", "14", "--csv", str(tmp_path / "c.csv"),
                   "--zooniverse", str(tmp_path / "zoo")])
    assert rc == 0
    assert "2 nuclear transient candidate(s)" in capsys.readouterr().out
    assert (tmp_path / "c.csv").exists()
    assert (tmp_path / "zoo" / "manifest.csv").exists()


def test_upload_missing_manifest(tmp_path):
    with pytest.raises(SystemExit):
        cli.main(["zooniverse-upload", str(tmp_path), "--project", "1", "--subject-set", "x"])
