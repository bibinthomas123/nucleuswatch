import csv
import json

import nucleuswatch as bm
from nucleuswatch import cli
from nucleuswatch.zooniverse import add_volunteer_votes, read_classifications, volunteer_result

from .conftest import OBJECT_ID

FIELDS = ["classification_id", "user_name", "user_id", "workflow_id", "created_at", "annotations",
          "subject_data", "subject_ids"]


def _row(cid, user, answer, oid, *, when="2026-10-20 10:00:00 UTC", workflow=1, task="T0"):
    return {
        "classification_id": cid, "user_name": user or "not-logged-in", "user_id": user or "",
        "workflow_id": workflow, "created_at": when,
        "annotations": json.dumps([{"task": task, "task_label": "What is it?", "value": answer}]),
        "subject_data": json.dumps({str(900 + cid): {"retired": None, "!object_id": oid, "#consensus": "agree"}}),
        "subject_ids": 900 + cid,
    }


def _export(tmp_path, rows):
    path = tmp_path / "classifications.csv"
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    return path


def test_tally_counts_each_volunteer_once(tmp_path):
    rows = [
        _row(1, "11", "Supernova", "ZTF26a", when="2026-10-20 10:00:00 UTC"),
        _row(2, "11", "Tidal disruption event", "ZTF26a", when="2026-10-21 10:00:00 UTC"),  # changed mind
        _row(3, "12", "Tidal disruption event", "ZTF26a"),
        _row(4, None, "Supernova", "ZTF26a"),  # anonymous
        _row(5, None, "Supernova", "ZTF26a"),  # another anonymous classification
        _row(6, "13", "AGN", "ZTF26b"),
        _row(7, "14", "AGN", "ZTF26b", workflow=2),
        _row(8, "15", "Supernova", None),  # not one of our subjects
    ]
    tally = read_classifications(_export(tmp_path, rows))
    assert tally["ZTF26a"]["votes"] == {"Tidal disruption event": 2, "Supernova": 2}
    assert tally["ZTF26a"]["n"] == 4
    assert set(tally) == {"ZTF26a", "ZTF26b"}
    assert read_classifications(_export(tmp_path, rows), workflow_id=2)["ZTF26b"]["n"] == 1


def test_volunteers_vote_only_when_decided():
    decided = volunteer_result("x", {"votes": {"Tidal disruption event": 4, "Supernova": 1}, "n": 5,
                                     "label": "Tidal disruption event", "fraction": 0.8})
    assert decided.classifications.iloc[0]["label"] == "Tidal disruption event"
    too_few = volunteer_result("x", {"votes": {"AGN": 3}, "n": 3, "label": "AGN", "fraction": 1.0})
    split = volunteer_result("x", {"votes": {"AGN": 3, "Supernova": 3}, "n": 6, "label": "AGN", "fraction": 0.5})
    assert too_few.classifications.iloc[0]["label"] == "Ambiguous"
    assert split.classifications.iloc[0]["label"] == "Ambiguous"


def test_volunteer_vote_enters_consensus(offline_brokers, tmp_path):
    rows = [_row(i, str(100 + i), "Tidal disruption event", OBJECT_ID) for i in range(6)]
    obj = bm.get_object(OBJECT_ID)
    before = obj.consensus()
    assert add_volunteer_votes({OBJECT_ID: obj}, _export(tmp_path, rows)) == 1
    after = obj.consensus()
    assert {"broker": "zooniverse", "classifier": "volunteers", "label": "Tidal disruption event",
            "category": "TDE"} in after["votes"]
    assert "TDE" in after["categories"] and "TDE" not in before["categories"]


def test_ingest_cli(tmp_path, capsys):
    rows = [_row(i, str(100 + i), "AGN", "ZTF26b") for i in range(5)]
    out = tmp_path / "tally.csv"
    assert cli.main(["zooniverse-ingest", str(_export(tmp_path, rows)), "--csv", str(out)]) == 0
    text = capsys.readouterr().out
    assert "1 object(s) classified; 1 with a decided volunteer vote" in text
    assert out.exists()
