import sys
import types

import numpy as np
import pytest

import nucleuswatch as bm
from nucleuswatch import zooniverse
from nucleuswatch.brokers import fink

from .conftest import OBJECT_ID


@pytest.fixture
def stamps(monkeypatch):
    rng = np.random.default_rng(0)
    monkeypatch.setattr(fink, "cutout", lambda oid, kind, **kw: rng.normal(size=(63, 63)).tolist())


def test_export_writes_images_and_manifest(offline_brokers, stamps, tmp_path):
    obj = bm.get_object(OBJECT_ID)
    path = zooniverse.export_subjects([obj], tmp_path)
    rows = zooniverse.read_manifest(path)
    assert len(rows) == 1
    row = rows[0]
    assert (tmp_path / row["image1"]).exists()
    assert (tmp_path / row["image2"]).exists()
    assert row["!object_id"] == OBJECT_ID
    assert row["#consensus"] == "agree"


def test_classifications_are_hidden_from_volunteers(offline_brokers, stamps, tmp_path):
    path = zooniverse.export_subjects([bm.get_object(OBJECT_ID)], tmp_path)
    header = path.read_text().splitlines()[0].split(",")
    visible = [h for h in header if not h.startswith(("#", "!", "//", "image"))]
    assert visible == []
    # anything that reveals a broker's opinion must never reach volunteers or Talk
    for h in header:
        if any(w in h for w in ("consensus", "categor", "classif")):
            assert h.startswith("#"), h


def test_export_without_cutouts(offline_brokers, tmp_path):
    path = zooniverse.export_subjects([bm.get_object(OBJECT_ID)], tmp_path, cutouts=False)
    assert "image2" not in zooniverse.read_manifest(path)[0]


def test_cutout_failure_keeps_subject(offline_brokers, monkeypatch, tmp_path):
    def boom(*a, **kw):
        raise bm.BrokerError("no stamps")

    monkeypatch.setattr(fink, "cutout", boom)
    rows = zooniverse.read_manifest(zooniverse.export_subjects([bm.get_object(OBJECT_ID)], tmp_path))
    assert rows[0]["image1"] and not rows[0].get("image2")


def test_upload_uses_panoptes_client(offline_brokers, stamps, tmp_path, monkeypatch):
    path = zooniverse.export_subjects([bm.get_object(OBJECT_ID)], tmp_path)
    log = {}

    class Links:
        pass

    class Resource:
        def __init__(self):
            self.links = Links()
            self.metadata = {}
            self.locations = []

        def save(self):
            self.id = 42

    class Subject(Resource):
        def add_location(self, p):
            self.locations.append(p)

        @staticmethod
        def async_saves():
            import contextlib
            return contextlib.nullcontext()

    class SubjectSet(Resource):
        def add(self, subjects):
            log["subjects"] = subjects

    fake = types.SimpleNamespace(
        Panoptes=types.SimpleNamespace(connect=lambda **kw: log.update(login=kw)),
        Project=types.SimpleNamespace(find=lambda pid: log.setdefault("project", pid)),
        Subject=Subject, SubjectSet=SubjectSet,
    )
    monkeypatch.setitem(sys.modules, "panoptes_client", fake)
    monkeypatch.setenv("ZOONIVERSE_USERNAME", "u")
    monkeypatch.setenv("ZOONIVERSE_PASSWORD", "p")

    set_id = zooniverse.upload_subjects(path, 1234, "test set")
    assert set_id == 42
    assert log["project"] == 1234
    (subject,) = log["subjects"]
    assert len(subject.locations) == 2
    assert subject.metadata["!object_id"] == OBJECT_ID
    assert not any(k.startswith("image") for k in subject.metadata)


def test_upload_requires_credentials(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "panoptes_client", types.SimpleNamespace(
        Panoptes=None, Project=None, Subject=None, SubjectSet=None))
    monkeypatch.delenv("ZOONIVERSE_USERNAME", raising=False)
    monkeypatch.delenv("ZOONIVERSE_PASSWORD", raising=False)
    with pytest.raises(ValueError, match="ZOONIVERSE_USERNAME"):
        zooniverse.upload_subjects(tmp_path / "manifest.csv", 1, "x")
