"""Checks against the real broker APIs. Run with: pytest -m live

These catch API changes on the broker side, which the offline tests can't.
"""

from datetime import datetime, timedelta, timezone

import pytest

import nucleuswatch as bm
from nucleuswatch.brokers import fink

pytestmark = pytest.mark.live


def test_live_alerce_and_fink():
    obj = bm.get_object("ZTF21abfmbix", brokers=["alerce", "fink"], use_cache=False)
    assert not obj.errors, obj.errors
    assert obj.results["alerce"].ndet > 0
    assert obj.results["fink"].ndet > 0


def test_live_fink_latests_has_cut_fields():
    stop = datetime.now(timezone.utc).date()
    rows = fink.latests("SN candidate", (stop - timedelta(days=14)).isoformat(),
                        stop.isoformat(), n=5, use_cache=False)
    assert rows, "no SN candidates in two weeks is suspicious"
    assert {"i:distnr", "i:distpsnr1", "i:sgscore1", "i:magpsf", "i:isdiffpos"} <= set(rows[0])


def test_live_fink_cutout():
    pixels = fink.cutout("ZTF21abfmbix", "Template", use_cache=False)
    assert len(pixels) == 63 and len(pixels[0]) == 63


def test_live_alertissimo_bridge():
    api = pytest.importorskip("alertissimo.api")
    from nucleuswatch.alertissimo_bridge import from_alertissimo

    obj = from_alertissimo(api.execute_dsl("objects ZTF21abfmbix from ztf via alerce\n"
                                           "with lightcurve via fink\n"))
    assert obj.results["fink"].ndet > 0
