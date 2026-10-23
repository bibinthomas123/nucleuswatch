import json
from pathlib import Path

import pytest

DATA = Path(__file__).parent / "data"
OBJECT_ID = "ZTF21abfmbix"


def load(name):
    return json.loads((DATA / name).read_text())


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("NUCLEUSWATCH_CACHE", str(tmp_path / "cache"))


@pytest.fixture
def offline_brokers(monkeypatch):
    """Serve recorded API responses instead of calling the brokers."""
    from nucleuswatch.brokers import alerce, fink, lasair

    monkeypatch.setattr(
        alerce, "fetch",
        lambda oid, **kw: alerce.parse(
            oid, load("alerce_object.json"), load("alerce_probabilities.json"),
            load("alerce_lightcurve.json"),
        ),
    )
    monkeypatch.setattr(fink, "fetch", lambda oid, **kw: fink.parse(oid, load("fink_objects.json")))
    monkeypatch.setattr(lasair, "fetch", lambda oid, **kw: lasair.parse(oid, load("lasair_object.json")))
