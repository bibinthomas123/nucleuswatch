# nucleuswatch

[![CI](https://github.com/bibinthomas123/nucleuswatch/actions/workflows/ci.yml/badge.svg)](https://github.com/bibinthomas123/nucleuswatch/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.10%20%7C%203.12%20%7C%203.13-blue)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](https://github.com/bibinthomas123/nucleuswatch/blob/main/LICENSE)
![Data: ZTF | Rubin](https://img.shields.io/badge/data-ZTF%20%7C%20Rubin%2FLSST-orange)

**Find nuclear transients in the alert stream, see whether ALeRCE, Fink and
Lasair agree on what they are, and send them to Zooniverse.**

![nucleuswatch nuclear search output](https://raw.githubusercontent.com/bibinthomas123/nucleuswatch/main/docs/images/cli_nuclear.svg)

<sub>Real output from 23 Oct 2026. Two of six candidates are flagged
<b>disagree</b>: Pan-STARRS calls the host a galaxy, but Lasair's Sherlock
matches it to a star in SDSS or Gaia. A single-broker filter would let
these through without comment.</sub>

---

## Why

Rubin/LSST alerts reach astronomers only through community brokers, each with
its own API, taxonomy and classifiers. If you care about **what a transient
actually is**, you end up querying each broker by hand and reconciling the
answers yourself.

nucleuswatch does that for one science case, **nuclear transients** (TDEs, AGN
flares, nuclear supernovae):

| | |
|---|---|
| 🔭 **Search** | recent ZTF transients sitting on galaxy nuclei, using the standard positional cuts |
| ⚖️ **Consensus** | every candidate is looked up in ALeRCE, Fink and Lasair, and the result is **agree / disagree / insufficient** |
| ✅ **Validated** | recovers 51% of spectroscopically confirmed TDEs at mag ≤ 19 (72% at ≤ 20) and raises their share 4.9× ([results](https://github.com/bibinthomas123/nucleuswatch/blob/main/validation/RESULTS.md)) |
| 🌌 **ZTF + Rubin** | ZTF names and Rubin/LSST `diaObjectId`s both work, through the same API |
| 📅 **Weekly** | a scheduled job publishes a digest page and keeps the history in the repo |
| 🧑‍🔬 **Zooniverse** | one flag writes a subject set ready to upload; broker opinions stay out of the volunteers' view |
| 🔗 **Alertissimo** | works with results from the [Alertissimo](https://github.com/sambolino/alertissimo) orchestration layer |

General multi-broker orchestration is Alertissimo's job. nucleuswatch is
the narrow, science-first layer that sits next to it.

## Quick start

```bash
git clone https://github.com/bibinthomas123/nucleuswatch.git
cd nucleuswatch
pip install -e ".[plot]"                 # ".[zooniverse]" adds direct upload
cp .env_example .env                     # optional: add your Lasair token
```

```bash
nucleuswatch nuclear --days 7 --mag-max 19            # this week's candidates
nucleuswatch object ZTF21abfmbix --plot lc.png        # one object, all brokers
nucleuswatch object 170587117485817955                # a Rubin/LSST object
nucleuswatch nuclear --days 7 --zooniverse week43/    # + Zooniverse subject set
nucleuswatch digest --days 7                          # weekly record + web page
```

Lasair needs a free token: make an account, copy it from your profile, and
put `LASAIR_TOKEN=...` in `.env` (or export it). Lasair's Rubin server has
separate accounts, so LSST objects need `LASAIR_LSST_TOKEN` as well. Without a
token, Lasair is skipped and the other two brokers still work.

## Screenshots

### One object, three brokers

`nucleuswatch object` shows what each broker reports and which classifiers
vote (`*`). The position offsets between brokers, the TNS name and Lasair's
Sherlock context all appear on one screen.

![nucleuswatch object output](https://raw.githubusercontent.com/bibinthomas123/nucleuswatch/main/docs/images/cli_object.svg)

### The merged light curve

`obj.plot()` overlays every broker's photometry. Marker shape shows the
broker and colour shows the band, and the title records each broker's vote.
Brokers are nudged a few hours apart so points don't hide each other. Here
you can also see that **Fink is missing some alerts** (the first g-band point
and all i-band points) that ALeRCE and Lasair have. `obj.coverage()` lists
exactly which ones.

![annotated light curve](https://raw.githubusercontent.com/bibinthomas123/nucleuswatch/main/docs/images/lightcurve_annotated.png)

### What Zooniverse volunteers see

`--zooniverse DIR` writes two images per subject: a **blind** light curve
with no ID, no broker labels and time relative to first detection, and
**New / Before / Difference** stamps of the latest alert.

![Zooniverse subject preview](https://raw.githubusercontent.com/bibinthomas123/nucleuswatch/main/docs/images/zooniverse_subject.png)

The manifest uses Zooniverse's column prefixes to control what volunteers can see:

| Manifest column | Visibility | Contents |
|---|---|---|
| `image1`, `image2` | shown | the two images above |
| `!object_id`, `!ra`, `!dec`, `!<broker>_url` | Talk only, after classifying | for discussion and lookup |
| `#consensus`, `#categories`, `#<broker>_<classifier>`, … | never | broker opinions, kept to compare with volunteer votes |

Upload the folder through the Project Builder, with
`panoptes subject-set upload-subjects <set_id> week43/manifest.csv`, or:

```bash
export ZOONIVERSE_USERNAME=... ZOONIVERSE_PASSWORD=...
nucleuswatch zooniverse-upload week43/ --project 1234 --subject-set "Nuclear, week 43"
```

## How it works

```mermaid
flowchart LR
    F[Fink class streams<br/>SN / Early Ia / SLSN / Ambiguous] --> C{nuclear cuts<br/>distnr · distpsnr1 · sgscore1}
    C --> M[look up each candidate]
    M --> A1[ALeRCE]
    M --> A2[Fink]
    M --> A3[Lasair]
    A1 & A2 & A3 --> N[normalise labels<br/>→ coarse taxonomy]
    N --> V{consensus<br/>agree · disagree · insufficient}
    V --> T[candidates table / CSV]
    V --> Z[Zooniverse subject set]
    AL[Alertissimo result] -. from_alertissimo .-> N
```

## Python API

```python
import nucleuswatch as nw

search = nw.nuclear_transients(days=7, cuts=nw.NuclearCuts(mag_max=19, distnr_max=0.5))
search.candidates            # one row per object: mag, distnr, sgscore1, consensus, ...
search.query                 # exact window + cuts used (store it with your results)

obj = search.objects["ZTF26acakche"]       # or nw.get_object("ZTF21abfmbix")
obj.summary()                # per broker: position, offset, ndet, time span, link
obj.classifications          # every classifier output + normalised category
obj.consensus()              # {'status': 'disagree', 'categories': ['SN', 'VarStar'], ...}
obj.coverage()               # which broker has which alert, and magnitude spread
obj.plot()                   # annotated; obj.plot(annotate=False) is the volunteer view

from nucleuswatch.zooniverse import export_subjects
export_subjects(search, "week43/")
```

From Alertissimo:

```python
from alertissimo.api import execute_dsl
from nucleuswatch.alertissimo_bridge import from_alertissimo

obj = from_alertissimo(execute_dsl("objects ZTF26acakche from ztf via alerce\nwith lightcurve via fink\n"))
```

## The nuclear cuts

Each alert is tested against these cuts, and the brightest passing alert per object is kept. Every threshold is a parameter.

| Cut | Default | Why |
|---|---|---|
| `magpsf <= mag_max` | 19.0 | bright enough for follow-up |
| `distnr <= distnr_max` | 0.5″ | on the source in the ZTF reference image (the nucleus) |
| `distpsnr1 <= distpsnr1_max` | 1.0″ | a Pan-STARRS1 source is right there |
| `sgscore1 <= sgscore1_max` | 0.5 | and it is galaxy-like (removes variable stars) |
| `ndethist >= ndethist_min` | 2 | not a one-off detection |
| `isdiffpos` | positive | brightening, not fading |

## Validation

How well do the cuts do on objects whose nature is known? The answer comes from
`validation/run_validation.py`, run against every object Fink cross-matched to a
TNS spectroscopic classification in 2024–2025
([full results](https://github.com/bibinthomas123/nucleuswatch/blob/main/validation/RESULTS.md)):

- **24 / 47 confirmed TDEs (51%)** pass the default cuts. The nucleus cuts
  remove only 2; the magnitude limit removes 21. Recovery rises to 64% at
  `mag_max=19.5` and 72% at `mag_max=20`.
- Among all spectroscopically classified objects, the cuts raise the TDE
  share **4.9×** (1.2% → 6.0%). Nuclear SNe Ia and SNe II remain the majority.
  That's expected, and it tells you how much follow-up a nuclear sample costs.
- This measures the cuts, not the selection from Fink's classifier streams.

## Rubin/LSST

Pass a Rubin `diaObjectId` anywhere a ZTF name works, for example
`nw.get_object(170587117485817955)`. The survey is detected from the ID.

- ALeRCE (Rubin stamp classifier; its newest model version votes), Fink
  (CATS broad classes, SuperNNova) and Lasair (Sherlock; needs
  `LASAIR_LSST_TOKEN`).
- Rubin difference fluxes can be negative, so light curves keep `flux` and
  `flux_err` in nJy. `mag` is filled only where flux > 0, and plots use flux.
- The nuclear search and image stamps are ZTF-only for now.

## Weekly digest

`nucleuswatch digest` runs the search and writes a small record
(`digest/<date>.json` and `.csv`, committed so the history lives in the repo).
It also writes a static page with plots, stamps, broker votes, TNS
spectroscopic types and an archive. The
[weekly workflow](https://github.com/bibinthomas123/nucleuswatch/blob/main/.github/workflows/weekly.yml) runs it every
Monday and publishes the page to GitHub Pages.

## How consensus works

<details>
<summary>Details</summary>

Only classifiers that give a whole-object best guess vote (see
`taxonomy.VOTING_CLASSIFIERS`): ALeRCE `stamp_classifier` and
`lc_classifier`, Fink `v:classification` (ZTF) or CATS (Rubin), and Lasair Sherlock. Labels map to
coarse categories (`SN`, `AGN`, `VarStar`, `CV/Nova`, `TDE`, `Nuclear`, …)
through `nucleuswatch.categorize`. "Unknown" and "Ambiguous" abstain.

- **agree**: every vote lands in one category
- **disagree**: votes land in more than one category
- **insufficient**: fewer than two brokers voted

Binary scores (e.g. Fink's SNIa-vs-non-Ia) are shown but get no category and
no vote. When ALeRCE serves several versions of one model, it still gets one
vote. The mapping is deliberately simple; please open an issue if a label is
mapped wrongly.
</details>

## Brokers

| Broker | Access | Used for |
|---|---|---|
| [ALeRCE](https://alerce.online) | public | light curve, `stamp_classifier`, `lc_classifier` |
| [Fink](https://fink-portal.org) | public | candidate streams, light curve, classification, cutouts |
| [Lasair](https://lasair-ztf.lsst.ac.uk) | free token (separate one for [Rubin](https://lasair.lsst.ac.uk)) | light curve, Sherlock context, TNS name |

A broker that is down, rate-limited or missing a token is reported in
`obj.errors` and doesn't fail the call. Responses are cached on disk (1 h
for objects, 10 min for streams) to respect rate limits.

## Known limitations

- The nuclear search and Zooniverse stamps are ZTF-only; Rubin objects work
  for lookups, consensus and plots.
- Lasair's Rubin adapter is built against recorded responses and hasn't been
  run live yet (it needs an LSST token).
- Candidates come only from four Fink classes, so objects Fink files
  elsewhere (e.g. SIMBAD-matched AGN) are not searched yet.
- Direct Zooniverse upload is tested against a mocked client, not a live project.
- The Alertissimo bridge handles single-object results.
  [docs/alertissimo.md](https://github.com/bibinthomas123/nucleuswatch/blob/main/docs/alertissimo.md) lists what Alertissimo can't do
  yet and the bugs found.

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest                       # offline tests on recorded broker responses
pytest -m live               # also hit the real APIs
python docs/make_images.py   # regenerate the screenshots in this README
python validation/run_validation.py   # rerun the validation (~10 min, cached)
```

Releases: bump `__version__`, then publish a GitHub release tagged
`v<version>`. The [release workflow](https://github.com/bibinthomas123/nucleuswatch/blob/main/.github/workflows/release.yml)
builds the package and uploads it to PyPI.

## Roadmap

- [ ] Lasair Sherlock `NT` (nuclear transient) as a second candidate source
- [ ] Fink `latests` recipe upstream in Alertissimo ([notes](https://github.com/bibinthomas123/nucleuswatch/blob/main/docs/alertissimo.md))
- [ ] Merge volunteer classifications back into the consensus table
- [x] Weekly digest web page
- [x] Rubin/LSST object IDs
- [x] Validation against spectroscopically classified objects
- [ ] Nuclear search on the Rubin stream; ANTARES adapter
- [ ] Publish on PyPI

## Acknowledgements

nucleuswatch only consumes data that the broker teams produce. If you use it in
a paper, please cite the brokers whose results you use
([ALeRCE](https://alerce.online), [Fink](https://fink-broker.org),
[Lasair](https://lasair.lsst.ac.uk)), and
[Alertissimo](https://arxiv.org/abs/2601.10454) if you used the bridge.

## License

[MIT](https://github.com/bibinthomas123/nucleuswatch/blob/main/LICENSE)
