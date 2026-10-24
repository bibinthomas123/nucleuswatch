# Python API reference

[← Documentation](README.md)

```python
import nucleuswatch as nw
```

The top-level package exports `get_object`, `MergedObject`,
`nuclear_transients`, `lsst_nuclear_transients`, `NuclearCuts`,
`categorize`, `BrokerError` and `__version__`. Everything else is imported
from its module, shown in each heading.

Every function that talks to a broker takes `use_cache` (default `True`).
Set it to `False` to ignore the disk cache and refetch
([caching](how-it-works.md#caching-and-rate-limits)).

**Contents:**
[Objects](#objects) ·
[Searches](#searches) ·
[TDE score](#tde-score) ·
[Weekly digest](#weekly-digest) ·
[Alerts](#alerts) ·
[Host galaxies](#host-galaxies) ·
[Taxonomy](#taxonomy) ·
[Zooniverse](#zooniverse) ·
[Alertissimo](#alertissimo) ·
[Broker adapters](#broker-adapters) ·
[Errors and HTTP](#errors-and-http)

---

## Objects

### `nw.get_object(object_id, brokers=None, *, use_cache=True, lasair_token=None)`

Ask every broker about one object, in parallel, and merge the answers.

| Parameter | Type | Default | Meaning |
|---|---|---|---|
| `object_id` | `str` or `int` | | A ZTF name (`"ZTF21abfmbix"`) or a Rubin `diaObjectId` (`170587117485817955`). The survey is detected from the ID. |
| `brokers` | `list[str]` | all | Any of `"alerce"`, `"fink"`, `"lasair"`. |
| `use_cache` | `bool` | `True` | Reuse cached responses. |
| `lasair_token` | `str` | `None` | Overrides `LASAIR_TOKEN` (ZTF) or `LASAIR_LSST_TOKEN` (Rubin). |

Returns a [`MergedObject`](#class-mergedobject). It raises `ValueError` for
an unrecognised ID or broker name. A broker that fails doesn't raise: its
reason is stored in `.errors`.

```python
obj = nw.get_object("ZTF21abfmbix")
obj.errors          # {} or e.g. {'lasair': 'no Lasair token; set LASAIR_TOKEN ...'}
```

### `class MergedObject`

What every queried broker knows about one object, side by side.

| Attribute / method | Returns | Description |
|---|---|---|
| `object_id` | `str` | The ID that was queried. |
| `survey` | `str` | `"ztf"` or `"lsst"`. |
| `results` | `dict[str, BrokerResult]` | One [`BrokerResult`](#class-brokerresult) per broker that answered. |
| `errors` | `dict[str, str]` | Why a broker didn't answer. |
| `brokers` | `list[str]` | Names of the brokers that answered. |
| `lightcurve` | `DataFrame` | Every detection from every broker: `mjd, band, mag, mag_err, flux, flux_err, candid, broker`. One row per broker per detection, sorted by time. |
| `classifications` | `DataFrame` | Every classifier output: `broker, classifier, label, probability, version`, plus `votes` (whether this classifier can vote) and `category` (the coarse category, set only for voting rows). |
| `summary()` | `DataFrame` | One row per broker: `ra, dec, offset_arcsec` (from the median position), `ndet, first_mjd, last_mjd, url`, and broker extras such as `tns`, `tns_type`, `simbad`, `sherlock`. |
| `coverage()` | `DataFrame` | One row per ZTF alert (`candid`), with a True/False column per broker saying whether that broker has the alert, and `mag_spread` (how much the brokers' magnitudes differ). |
| `consensus()` | `dict` | `{"status": "agree" \| "disagree" \| "insufficient", "categories": [...], "votes": [{"broker", "classifier", "label", "category"}, ...]}`. See [how consensus works](how-it-works.md#2-merged-objects-and-consensus). |
| `plot(ax=None, *, annotate=True)` | matplotlib `Axes` | Merged light curve: colour = band, marker = broker. Rubin objects are plotted in flux. `annotate=False` gives the blind volunteer view (no IDs or labels, time from first detection). Needs `nucleuswatch[plot]`. |

---

## Searches

### `nw.nuclear_transients(days=7, cuts=None, *, sources=("fink",), fink_classes=NUCLEAR_FINK_CLASSES, new_within_days=None, flare_gap=30, flare_lookback=730, merge=True, brokers=None, use_cache=True, now=None)`

ZTF transients on a galaxy nucleus with an alert in the `days` days before
`now`. The steps are in [how it works](how-it-works.md#3-the-nuclear-search-ztf).

| Parameter | Type | Default | Meaning |
|---|---|---|---|
| `days` | `float` | `7` | Length of the search window. |
| `cuts` | [`NuclearCuts`](#class-nuclearcuts) | `NuclearCuts()` | Alert-level cuts. |
| `sources` | `tuple[str]` | `("fink",)` | `"fink"` (class streams) and/or `"lasair"` (Sherlock `NT` objects; needs a token, live windows only). |
| `fink_classes` | `tuple[str]` | 9 streams | Fink classes to read: SN candidate, Early SN Ia candidate, SLSN candidate, Ambiguous, (TNS) TDE, (TNS) AGN, (SIMBAD) AGN, (SIMBAD) QSO, (SIMBAD) Seyfert1. |
| `new_within_days` | `float` | `None` | If set, split candidates into new and known (see below). `None` keeps everything as new. |
| `flare_gap` | `float` | `30` | Days without a positive alert that end a flare. |
| `flare_lookback` | `float` | `730` | Days before the flare's start in which earlier activity makes an object "known". |
| `merge` | `bool` | `True` | Look each new candidate up in every broker and add consensus columns. `False` is much faster. |
| `brokers` | `list[str]` | all | Brokers used when merging. |
| `now` | `datetime` | now (UTC) | End of the window, for re-running past weeks. |

An object is **known** if it was first detected more than `new_within_days`
ago **and** its nucleus was already active before the current flare.
Everything else is **new**.

Returns a [`CandidateSearch`](#class-candidatesearch). Its `candidates`
table has these columns:

- `object_id, ra, dec, mag, band, mjd`: the brightest passing alert.
- `distnr, distpsnr1, sgscore1, ndethist`: that alert's cut values.
- `fink_class, sources`: where the object came from.
- `first_seen_mjd, first_pass_mjd, age_days`: when it was first detected and first passed the cuts.
- `flare_onset_mjd, prior_alerts`: the flare history, filled for old objects.
- With `merge=True`, also `consensus, categories, broker_labels`.

```python
search = nw.nuclear_transients(days=7, cuts=nw.NuclearCuts(mag_max=20), new_within_days=365)
search.candidates.head()
search.known            # old, already-active nuclei (mostly AGN), not merged
search.funnel           # {'alerts_scanned': ..., 'passed_cuts': ..., 'new': ..., 'known': ...}
```

### `class NuclearCuts`

A dataclass of alert-level cuts. Set any field by keyword:
`NuclearCuts(mag_max=20, distnr_max=0.3)`.

| Field | Default | Alert passes if |
|---|---|---|
| `mag_max` | `19.0` | `magpsf ≤ mag_max` |
| `distnr_max` | `0.5` | `distnr ≤ distnr_max` (arcsec to the nearest ZTF reference source) |
| `distpsnr1_max` | `1.0` | `distpsnr1 ≤ distpsnr1_max` (arcsec to the nearest Pan-STARRS1 source) |
| `sgscore1_max` | `0.5` | `0 ≤ sgscore1 ≤ sgscore1_max` (0 = galaxy, 1 = star) |
| `ndethist_min` | `2` | `ndethist ≥ ndethist_min` |

Alerts must also be positive subtractions. `passes(alert)` applies the cuts
to one Fink alert dict (`i:`-prefixed keys). ZTF's −999 "no value" counts as
failing.

### `class CandidateSearch`

The result of a search.

| Field | Type | Description |
|---|---|---|
| `candidates` | `DataFrame` | New candidates, one row per object. |
| `objects` | `dict[str, MergedObject]` | Merged objects for the candidates (empty when `merge=False`). Iterating over a `CandidateSearch` yields these objects. |
| `query` | `dict` | Everything needed to reproduce the run: window, cuts, sources requested and actually used, and the new/known parameters. Store it with your results. |
| `errors` | `list[str]` | Problems that didn't stop the search, such as a stream that failed or a skipped source. |
| `known` | `DataFrame` | Objects that passed the cuts but are known (ZTF search with `new_within_days`). |
| `funnel` | `dict` | How many alerts and objects survived each step. |

`len(search)` is the number of candidates.

### `nw.lsst_nuclear_transients(days=7, mag_max=20.0, *, max_offset=0.5, tag="extragalactic_lt20mag_candidate", merge=True, use_cache=True, now=None)`

The same search for the Rubin/LSST stream. The steps are in
[how it works](how-it-works.md#5-rubinlsst).

| Parameter | Type | Default | Meaning |
|---|---|---|---|
| `days` | `float` | `7` | Length of the window. |
| `mag_max` | `float` | `20.0` | Brightest alert (from positive difference flux) must be at least this bright. |
| `max_offset` | `float` | `0.5` | Maximum distance (arcsec) to the nearest Legacy Surveys DR10 source, which must be a galaxy. |
| `tag` | `str` | `"extragalactic_lt20mag_candidate"` | Fink Rubin tag to read. The list is at <https://api.lsst.fink-portal.org/api/v1/tags>. |
| `merge` | `bool` | `True` | Merge candidates with ALeRCE and Fink. |
| `now` | `datetime` | now | End of the window. |

Returns a [`CandidateSearch`](#class-candidatesearch). The `candidates`
columns are `object_id, ra, dec, mag, band, mjd, host_sep, host_type,
host_r50, sources`, plus the consensus columns when merged. If no alerts
arrived in the window, `query["latest_alert_mjd"]` gives the time of the
stream's last alert.

### `recipes.merge_objects(object_ids, brokers=None, *, use_cache=True)`

Look up many ZTF objects in every broker with the fewest calls: ALeRCE and
Fink per object (4 in parallel), Lasair in one batch. Returns
`dict[object_id, MergedObject]`. If the Lasair batch fails, the reason is
recorded on every object.

### `recipes.flare_onset(mjds, at, gap)`

Start (MJD) of the run of alerts that ends at the latest alert before `at`.
The run breaks at any gap longer than `gap` days. Returns `None` without
alerts.

---

## TDE score

`from nucleuswatch.score import tde_score, tde_scores, features, FEATURES`

| Function | Returns | Description |
|---|---|---|
| `tde_score(obj, host=None, *, lookup_host=True)` | `float` or `None` | The model's output, 0–1, for a ZTF `MergedObject`. Higher means more TDE-like. Looks up the host unless you pass `host` or `lookup_host=False`. `None` for Rubin objects or an empty light curve. |
| `tde_scores(objects, *, use_cache=True)` | `(scores, hosts)` | Scores many objects (`{object_id: MergedObject}`) with one host-catalogue query per 25 objects. Returns two dicts keyed by object ID. |
| `features(obj, host=None, at=None)` | `dict[str, float]` | The twelve inputs ([list](how-it-works.md#4-the-tde-score)) from detections up to MJD `at` (default: all); `NaN` where unavailable. |
| `hosts_for(objects, *, use_cache=True)` | `dict` | Legacy Surveys host per object (DR9 for ZTF, DR10 for Rubin), or `None`. |
| `distance_modulus(z)` | `float` | Flat ΛCDM (H0 = 70, Ωm = 0.3) distance modulus. |
| `FEATURES` | `list[str]` | Feature names, in model order. |
| `load_model()` | `dict` | The stored model (`features, fill, mean, scale, coef, intercept, n_train, n_tde, fitted`). |
| `apply(model, rows)` | `ndarray` | Scores for a list of feature dicts with a given model. Useful for refitted models. |

To refit after changing features: `python validation/tde_score.py`. It
rewrites `assets/tde_score.json` and `validation/TDE_SCORE.md`.

---

## Weekly digest

`from nucleuswatch import digest`

### `digest.build(days=7, mag_max=20.0, *, bright_mag=19.0, sources=("fink", "lasair"), new_within_days=365, brokers=None, now=None, use_cache=True, lsst=True)`

Runs the weekly search and returns `(record, search)`. `record` is a
JSON-ready dict, laid out in
[weekly-digest.md](weekly-digest.md#the-data-files). `search` is the ZTF
[`CandidateSearch`](#class-candidatesearch).

| Parameter | Default | Meaning |
|---|---|---|
| `days` | `7` | Window length. |
| `mag_max` | `20.0` | Faintest magnitude. |
| `bright_mag` | `19.0` | Candidates at or brighter than this are tagged `bright`, the rest `faint`. |
| `sources` | `("fink", "lasair")` | Candidate sources. Lasair is skipped for past windows. |
| `new_within_days` | `365` | New/known split (see `nuclear_transients`). |
| `brokers` | all | Brokers for consensus. |
| `now` | now | End of the window. A window ending more than a day ago is marked `backfill`. |
| `lsst` | `True` | Also run the Rubin search into `record["lsst"]`. |

Candidates are scored with `tde_score` and sorted by it, highest first.

### `digest.write_data(record, data_dir)`

Writes `<date>.json`, `<date>.csv` and `<date>_known.csv` to `data_dir`, and
updates `latest.json` when this is the newest record. Returns the JSON path.

### `digest.render_site(record, data_dir, site_dir, search=None, *, use_cache=True, validation="validation/results.json")`

Writes the static site to `site_dir`: `index.html`, plots and stamps in
`img/` (only when `search` is given), copies of every record in `data/`,
`feed.xml` and `.nojekyll`. The archive and history chart come from the
records in `data_dir`. The validation panel is drawn only if the
`validation` file exists. Returns the path of `index.html`.

---

## Alerts

`from nucleuswatch import notify`

A candidate is **notable** if any of these holds:
- its `tde_score` is at least `digest.TDE_LIKE` (0.5);
- any broker voted TDE;
- TNS gives a TDE type.

| Function | Description |
|---|---|
| `notable(record)` | The notable candidates of a digest record. |
| `is_notable(candidate, threshold)` | The test above for one candidate dict. |
| `message(record)` | A plain-text chat message: a headline plus one line per notable candidate, with links. |
| `post(url, text, *, timeout=20)` | Sends `text` to a Slack or Discord incoming webhook. The payload format is chosen from the URL's host. Raises `requests.HTTPError` on failure. |
| `atom_feed(records)` | Atom XML with one entry per notable candidate per record. |
| `write_feed(data_dir, site_dir)` | Writes `feed.xml` for the newest 12 records (`FEED_WEEKS`). |

Setup is in [weekly-digest.md → Chat alerts](weekly-digest.md#chat-alerts-slack-or-discord).

---

## Host galaxies

`from nucleuswatch import hosts`

| Function | Description |
|---|---|
| `nearest_sources(positions, *, use_cache=True, release="dr10")` | `positions` maps any key to `(ra, dec)` in degrees; `release` is `"dr10"` (Rubin, deeper, DECam only) or `"dr9"` (ZTF, whole Legacy Surveys sky). Returns, per key, the nearest source within 2″ as `{"sep", "type", "shape_r", "mag_g", "mag_r", "mag_z", "z", "z_err", "z_is_spec"}` (magnitudes dereddened; `z` spectroscopic where known, else photometric), or `None`. Queries 25 positions at a time; results are cached for 30 days. |
| `is_nuclear(host, max_offset)` | True when `host` is a galaxy model (REX, EXP, DEV, SER) within `max_offset` arcsec. |

---

## Taxonomy

`from nucleuswatch.taxonomy import categorize, VOTE_PRIORITY`

| Name | Description |
|---|---|
| `categorize(label)` | Maps any broker label to `SN, TDE, Kilonova, AGN, CV/Nova, VarStar, Star, Microlensing, SolarSystem, Bogus`, or `None` to abstain. Case-insensitive, rule-based. |
| `VOTE_PRIORITY` | `{broker: (classifier, ...)}`. Which classifier casts each broker's vote, most preferred first. Edit a copy to change the voting. |
| `VOTING_CLASSIFIERS` | The set of `(broker, classifier)` pairs that may vote. |

---

## Zooniverse

`from nucleuswatch.zooniverse import export_subjects, upload_subjects`

### `export_subjects(objects, outdir, *, cutouts=True, use_cache=True)`

Writes one subject per `MergedObject` to `outdir`. `image1` is the blind
light curve. `image2` holds Fink's science, template and difference stamps
of the latest alert (ZTF only, when `cutouts=True`). Writes `manifest.csv`
and returns its path.

Manifest columns starting with `#` are hidden from volunteers: consensus,
categories, broker votes, peak magnitude and so on. Columns starting with
`!` appear only in Talk: the object ID, coordinates and broker links. Accepts a
`CandidateSearch` directly, because iterating over one yields its objects.

### `upload_subjects(manifest, project_id, subject_set_name, *, username=None, password=None)`

Creates a subject set in your Zooniverse project and uploads every subject
in the manifest. Returns the new subject-set ID. Credentials default to
`ZOONIVERSE_USERNAME` and `ZOONIVERSE_PASSWORD`. Needs
`pip install "nucleuswatch[zooniverse]"`, and you must own or collaborate on
the project.

Also available: `subject_metadata(obj)` (one manifest row without images),
`read_manifest(path)` and `draw_stamps(axes, stamps)`.

---

## Alertissimo

### `alertissimo_bridge.from_alertissimo(result, object_id=None)`

Converts an Alertissimo `DSLExecutionResult`, or its `to_dict()`, into a
`MergedObject`. ZTF records only, one object per call (pass `object_id` if
the result holds several).

```python
from alertissimo.api import execute_dsl
from nucleuswatch.alertissimo_bridge import from_alertissimo
obj = from_alertissimo(execute_dsl("objects ZTF26acakche from ztf via alerce\nwith lightcurve via fink\n"))
```

---

## Broker adapters

`from nucleuswatch.brokers import alerce, fink, lasair, alerce_lsst, fink_lsst, lasair_lsst`

Every adapter has `fetch(object_id, *, use_cache=True) -> BrokerResult` and
`parse(object_id, ...raw responses) -> BrokerResult`. The Lasair adapters
also take `token=`. Extra functions:

| Function | Description |
|---|---|
| `alerce.positive_alert_mjds(object_id, *, use_cache=True, ttl=86400)` | MJDs of every positive ZTF detection. This history is complete, because ALeRCE keeps every public alert. |
| `fink.latests(fink_class, startdate, stopdate, n=1000, *, use_cache=True, ttl=600)` | Up to `n` (at most 1000) alerts that Fink put in a class between two ISO dates. |
| `fink.latests_all(fink_class, start, stop, *, use_cache=True, ttl=600)` | Every alert in the class over `[start, stop)`, splitting the window until each piece is under the 1000 cap. |
| `fink.positive_alert_mjds(object_id, ...)` | Like ALeRCE's, but only alerts that passed Fink's quality cuts. |
| `fink.cutout(object_id, kind="Science", *, use_cache=True)` | Pixel array (list of lists) of the latest alert's stamp. `kind` is `"Science"`, `"Template"` or `"Difference"`. |
| `lasair.nuclear_candidates(days=7, mag_max=20.0, max_separation=1.0, *, token=None, use_cache=True, limit=1000)` | Objects Sherlock classes as `NT` within `max_separation`″ of a host core and active in the last `days` days. One Lasair call. |
| `lasair.batch_results(object_ids, *, token=None, use_cache=True)` | Sherlock class, description and TNS name for many ZTF objects in 2 calls per 100 objects. Returns `dict[object_id, BrokerResult]` for the objects Lasair knows. |
| `fink_lsst.tags(tag, startdate=None, stopdate=None, n=20000, columns=None, *, use_cache=True, ttl=600)` | Rubin alerts with a Fink tag, newest first. With no dates, returns the latest `n`. |

### `class BrokerResult`

`from nucleuswatch.brokers.base import BrokerResult`

| Field | Description |
|---|---|
| `broker`, `object_id` | Which broker and object. |
| `ra`, `dec` | Position from this broker. |
| `lightcurve` | DataFrame, as in `MergedObject.lightcurve`. |
| `classifications` | DataFrame `broker, classifier, label, probability, version`. |
| `extra` | Broker-specific facts: `tns`, `tns_type`, `simbad`, `sherlock`, `sherlock_description`, `stellar`, … |
| `url` | The broker's page for the object. |
| `ndet` | Number of detections (property). |

Helpers in the same module: `flux_to_mag(flux, flux_err)` converts nJy to an
AB magnitude, or `(None, None)` when flux ≤ 0. `lightcurve_frame(rows,
broker)` and `classification_frame(rows, broker)` build the standard
DataFrames; use them when writing a new adapter.

---

## Errors and HTTP

| Name | Description |
|---|---|
| `nw.BrokerError` | Raised for broker problems: unauthorised (401), not found (404), rate limited (429), other HTTP errors, non-JSON responses, network failures. `get_object` and the searches catch it and record the message instead. |
| `_http.request_json(method, url, *, params=None, json_body=None, data=None, headers=None, use_cache=True, ttl=3600, timeout=60)` | The one HTTP function every adapter uses: retries, caching, error translation. |
| `_http.cache_dir()` | The cache directory: `$NUCLEUSWATCH_CACHE`, else `$XDG_CACHE_HOME/nucleuswatch`, else `~/.cache/nucleuswatch`. Delete it to clear the cache. |
