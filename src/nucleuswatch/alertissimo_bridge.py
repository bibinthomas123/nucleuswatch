"""Use Alertissimo (https://github.com/sambolino/alertissimo) as a data source.

Alertissimo plans and runs multi-broker workflows from a DSL and returns
normalised "portfolio" records. This module converts such a result into a
:class:`~nucleuswatch.core.MergedObject`, so the consensus view, recipes and
Zooniverse export work on data fetched by Alertissimo too::

    from alertissimo.api import execute_dsl
    from nucleuswatch.alertissimo_bridge import from_alertissimo

    result = execute_dsl('objects ZTF21abfmbix from ztf via alerce\\nwith lightcurve via fink\\n')
    obj = from_alertissimo(result)

Alertissimo is a research prototype whose interfaces may change; this bridge
only reads record types it knows and ignores the rest. Tested against
Alertissimo 0.1.0 (main branch, October 2026). Single-object workflows only:
portfolio records don't say which object a detection belongs to.
"""

from __future__ import annotations

import re
from collections import defaultdict

from .brokers.base import BrokerResult, classification_frame, lightcurve_frame
from .core import MergedObject

# semantic_type looks like "detection@ztf:fink", "classification@fink",
# "summary@ztf:alerce": product @ [origin:] broker
_TYPE = re.compile(r"^(?P<product>[a-z_]+)@(?:(?P<origin>[a-z0-9_]+):)?(?P<broker>[a-z_]+)$")
_PSF_MAG = re.compile(r"^photometry\.(?P<band>[a-z])\.psf\.mag$")

# Alertissimo's "best.class" for each broker maps to our voting classifier.
_BEST_CLASSIFIER = {"fink": "fink_classification", "lasair": "sherlock", "alerce": "lc_classifier"}

_URLS = {
    "alerce": "https://alerce.online/object/{}",
    "fink": "https://fink-portal.org/{}",
    "lasair": "https://lasair-ztf.lsst.ac.uk/objects/{}/",
}


def from_alertissimo(result, object_id: str | None = None) -> MergedObject:
    """Convert an Alertissimo ``DSLExecutionResult`` (or its ``to_dict()``)."""
    data = result.to_dict() if hasattr(result, "to_dict") else result
    by_broker: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for portfolio in data.get("portfolios", []):
        for rec in portfolio.get("records", []):
            m = _TYPE.match(rec.get("semantic_type", ""))
            if m and m["origin"] in (None, "ztf"):
                by_broker[m["broker"]][m["product"]].append(rec.get("fields", {}))

    ids = {
        f.get("identity.object_id")
        for products in by_broker.values()
        for f in products.get("summary", [])
        if f.get("identity.object_id")
    }
    if object_id is None:
        if len(ids) != 1:
            raise ValueError(f"expected one object in the result, found {sorted(ids) or 'none'}; "
                             "pass object_id= or use a single-object workflow")
        object_id = ids.pop()

    merged = MergedObject(object_id)
    for broker, products in by_broker.items():
        result_ = _broker_result(broker, object_id, products)
        if result_.ndet or not result_.classifications.empty or result_.ra is not None:
            merged.results[broker] = result_
    return merged


def _broker_result(broker: str, object_id: str, products: dict[str, list[dict]]) -> BrokerResult:
    detections = []
    for f in products.get("detection", []):
        for key, mag in f.items():
            m = _PSF_MAG.match(key)
            if m and mag is not None:
                detections.append({
                    "mjd": f.get("time.mjd"),
                    "band": m["band"],
                    "mag": mag,
                    "mag_err": f.get(f"{key}.error"),
                    "candid": f.get("identity.source_id"),
                })

    classes = []
    # Alertissimo emits one classification record per alert; brokers return
    # the newest alert first, so the first record is the current view.
    for f in products.get("classification", [])[:1]:
        if f.get("best.class"):
            classes.append({"classifier": _BEST_CLASSIFIER.get(broker, f"{broker}_best"),
                            "label": f["best.class"], "probability": f.get("best.probability"),
                            "version": "via alertissimo"})

    ra = dec = None
    for f in products.get("summary", []) + products.get("detection", []):
        if f.get("position.ra") is not None:
            ra, dec = f["position.ra"], f.get("position.dec")
            break

    url = _URLS.get(broker)
    return BrokerResult(
        broker=broker,
        object_id=object_id,
        ra=ra,
        dec=dec,
        lightcurve=lightcurve_frame(detections, broker),
        classifications=classification_frame(classes, broker),
        extra={"source": "alertissimo"},
        url=url.format(object_id) if url else None,
    )
