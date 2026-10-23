"""The fixture is a real Alertissimo 0.1.0 result for

    objects ZTF21abfmbix from ztf via alerce
    with lightcurve via fink
"""

import pytest

from nucleuswatch.alertissimo_bridge import from_alertissimo
from nucleuswatch.brokers import fink

from .conftest import OBJECT_ID, load


def test_converts_portfolio_records():
    obj = from_alertissimo(load("alertissimo_result.json"))
    assert obj.object_id == OBJECT_ID
    assert set(obj.brokers) == {"alerce", "fink"}
    assert obj.results["fink"].ndet == 14
    assert obj.results["alerce"].ra == pytest.approx(193.8217, rel=1e-5)


def test_matches_direct_fink_adapter():
    via_alt = from_alertissimo(load("alertissimo_result.json")).results["fink"].lightcurve
    direct = fink.parse(OBJECT_ID, load("fink_objects.json")).lightcurve
    assert set(via_alt["candid"]) == set(direct["candid"])
    assert sorted(via_alt["mag"].round(4)) == sorted(direct["mag"].round(4))
    assert set(via_alt["band"]) == set(direct["band"])


def test_fink_classification_votes():
    c = from_alertissimo(load("alertissimo_result.json")).consensus()
    assert {"broker": "fink", "classifier": "fink_classification",
            "label": "SN candidate", "category": "SN"} in c["votes"]


def test_unknown_record_types_are_ignored():
    data = load("alertissimo_result.json")
    data["portfolios"][0]["records"].append(
        {"semantic_type": "something_new@ztf:fink", "fields": {"x": 1}})
    assert from_alertissimo(data).results["fink"].ndet == 14


def test_multi_object_result_needs_object_id():
    data = load("alertissimo_result.json")
    data["portfolios"][0]["records"].append(
        {"semantic_type": "summary@ztf:fink", "fields": {"identity.object_id": "ZTF20aaaaaaa"}})
    with pytest.raises(ValueError, match="expected one object"):
        from_alertissimo(data)
