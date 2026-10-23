# Working with Alertissimo

[Alertissimo](https://github.com/sambolino/alertissimo) (BSD-3, Vujčić,
Srećković & Babarogić; arXiv:2601.10454) is the general orchestration layer
for LSST/ZTF brokers: a DSL, a capability registry and normalised
"portfolios". nucleuswatch is deliberately narrower: science recipes,
a cross-broker **consensus** view, and **Zooniverse export**. Neither exists
in Alertissimo today.

## What works now

`nucleuswatch.alertissimo_bridge.from_alertissimo(result)` turns a
single-object Alertissimo result into a `MergedObject`, so consensus, plots
and Zooniverse export run on data Alertissimo fetched. Tested live against
Alertissimo 0.1.0 (main, Oct 2026) with:

```
objects ZTF26acakche from ztf via alerce
with lightcurve via fink
```

It reads `summary@`, `detection@` and `classification@` records and ignores
other record types.

## Notes for the Alertissimo authors

Observed on main as of 2026-10-23 (Python 3.11, alerce client from
`requirements`):

1. **The nuclear recipe can't be expressed yet.** A Fink class + time-window
   search fails at lowering:

   ```
   objects from ztf via fink
   within 7d
   where classification.best.class = "SN candidate"
   ```
   → `SurfaceLoweringError: semantic search inputs have no declared recipe
   translation: criteria/time_context [deferred_capability]`

   Fink's `POST /api/v1/latests` (`class`, `startdate`, `stopdate`, `n`)
   covers exactly this, and `fink/ztf/mappings.yaml` already maps the
   `latests#i:distnr`, `latests#i:sgscore1` and `latests#i:distpsnr1` fields
   the nuclear cuts need. A recipe translation for it would let
   `nucleuswatch.recipes.nuclear_transients` be written as one DSL script:

   ```
   objects from ztf via fink
   within 7d
   where classification.best.class = "SN candidate"
   filter crossmatch@panstarrs:fink.separation.total <= 1.0
   filter crossmatch@panstarrs:fink.classification.assessment.star_galaxy.score < 0.5
   with lightcurve via fink
   ```
   This is a good candidate for a first upstream PR.

2. **`validate_dsl` passes, `execute_dsl` fails (ALeRCE classification).**

   ```
   objects ZTF21abfmbix from ztf via alerce
   with classification via alerce
   ```
   `validate_dsl(...).is_runnable` is `True`, but execution raises
   `PlanningAmbiguityError: ambiguous provider endpoint
   (operation='get_classification', source=broker=alerce, origin=ztf);
   candidates: alerce/ztf/query_probabilities, alerce/ztf/query_object`.

3. **`with classification from <name>`** on ZTF raises
   `TypeError: AlerceCommonSearch.query_probabilities() got an unexpected
   keyword argument 'classifier'`. This is probably a mismatch with the
   installed `alerce` client version.

Until (1) lands, nucleuswatch sources candidates from Fink directly and uses
Alertissimo only through the bridge.
