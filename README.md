# nucleuswatch

**Find nuclear transients in the alert stream, see whether ALeRCE, Fink and
Lasair agree on what they are, and send them to Zooniverse.**

Rubin/LSST alerts reach astronomers only through community brokers, each with
its own API, taxonomy and classifiers. General multi-broker orchestration is
what [Alertissimo](https://github.com/sambolino/alertissimo) does.
nucleuswatch is the narrow, science-first layer next to it:

1. **Recipe:** `nuclear_transients()` finds recent transients on galaxy
   nuclei (TDEs, AGN flares, nuclear SNe) using the standard ZTF positional
   cuts.
2. **Consensus:** every candidate is looked up in every broker. Class labels
   are mapped to one coarse taxonomy, and you see whether the brokers
   **agree, disagree, or there's not enough to tell**.
3. **Zooniverse export:** one command writes a ready-to-upload subject set.
   Volunteers get a blind light curve and new/before/difference stamps.
   Broker opinions are kept out of their view.

Data can come from nucleuswatch's own small adapters or from an Alertissimo
workflow result (see [docs/alertissimo.md](docs/alertissimo.md)).

Status: **v0.2, ZTF only.** LSST support will follow as broker LSST APIs
stabilise.

## Install

```bash
git clone https://github.com/bibinthomas123/nucleuswatch.git
cd nucleuswatch
pip install -e ".[plot]"          # until it's on PyPI
pip install -e ".[zooniverse]"    # + direct upload to Zooniverse
```

## Nuclear transients → Zooniverse in one line

```bash
nucleuswatch nuclear --days 7 --mag-max 19 --csv candidates.csv --zooniverse week43/
```
```
6 nuclear transient candidate(s), last 14 days, mag <= 19

   object_id   mag band  distnr  sgscore1            fink_class    consensus categories
ZTF26acakche 17.57    g    0.42      0.00          SN candidate        agree         SN
ZTF18abxnydk 17.62    r    0.34      0.00             Ambiguous insufficient         SN
...
```

Then upload `week43/` with the Project Builder, with
`panoptes subject-set upload-subjects <set_id> week43/manifest.csv`, or:

```bash
export ZOONIVERSE_USERNAME=... ZOONIVERSE_PASSWORD=...
nucleuswatch zooniverse-upload week43/ --project 1234 --subject-set "Nuclear, week 43"
```

### What volunteers see, and what they don't

| Manifest column | Visibility | Contents |
|---|---|---|
| `image1` | shown | light curve vs. days since first detection, no id or labels |
| `image2` | shown | New / Before / Difference stamps (Fink, latest alert) |
| `!object_id`, `!ra`, `!dec`, `!<broker>_url` | Talk only, after classifying | for discussion and lookup |
| `#consensus`, `#categories`, `#<broker>_<classifier>`, … | never | broker opinions, kept for comparing with volunteer votes |

## Python

```python
import nucleuswatch as bm

search = bm.nuclear_transients(days=7, cuts=bm.NuclearCuts(mag_max=19, distnr_max=0.5))
search.candidates            # one row per object: mag, distnr, sgscore1, consensus, ...
search.query                 # exact window + cuts used (store it with your results)

obj = search.objects["ZTF26acakche"]       # or bm.get_object("ZTF21abfmbix")
obj.summary()                # per broker: position, offset, ndet, time span, link
obj.classifications          # every classifier output + normalised category
obj.consensus()              # {'status': 'agree', 'categories': ['SN'], 'votes': [...]}
obj.coverage()               # which broker has which alert
obj.plot()                   # annotated; obj.plot(annotate=False) is the volunteer view

from nucleuswatch.zooniverse import export_subjects
export_subjects(search, "week43/")
```

Single objects from the shell: `nucleuswatch object ZTF21abfmbix --plot lc.png`.

## The nuclear cuts

Applied to each alert. The brightest passing alert per object is kept:

| Cut | Default | Why |
|---|---|---|
| `magpsf <= mag_max` | 19.0 | bright enough for follow-up |
| `distnr <= distnr_max` | 0.5″ | on top of the source in the ZTF reference image (the nucleus) |
| `distpsnr1 <= distpsnr1_max` | 1.0″ | a Pan-STARRS1 source is right there |
| `sgscore1 <= sgscore1_max` | 0.5 | and that source is galaxy-like (removes variable stars) |
| `ndethist >= ndethist_min` | 2 | not a one-off detection |
| `isdiffpos` | positive | brightening, not fading |

Candidates are drawn from Fink's `SN candidate`, `Early SN Ia candidate`,
`SLSN candidate` and `Ambiguous` streams. TDEs and AGN flares often land in
`Ambiguous`. **Limitation:** objects Fink files under other classes (e.g. a
SIMBAD-matched AGN) are not searched yet. A Lasair Sherlock `NT`
(nuclear transient) source is on the roadmap.

## Brokers

| Broker | Access | Used for |
|---|---|---|
| [ALeRCE](https://alerce.online) | public | light curve, `stamp_classifier`, `lc_classifier` |
| [Fink](https://fink-portal.org) | public | candidate streams, light curve, classification, cutouts |
| [Lasair](https://lasair-ztf.lsst.ac.uk) | free token | light curve, Sherlock context classification |

Lasair: make an account, copy the token from your profile, then
`export LASAIR_TOKEN=...` or put `LASAIR_TOKEN=...` in a `.env` file in the
directory you run from (`.env` is gitignored). Without it Lasair is skipped.

## How consensus works

Only classifiers giving a whole-object best guess vote (see
`taxonomy.VOTING_CLASSIFIERS`). Labels map to coarse categories (`SN`, `AGN`,
`VarStar`, `CV/Nova`, `TDE`, `Nuclear`, …) via `nucleuswatch.categorize`.
"Unknown"/"Ambiguous" abstain. The status is `agree` if every vote lands in
one category, `disagree` if they don't, and `insufficient` if fewer than two
brokers voted. Binary scores (e.g. Fink's SNIa-vs-non-Ia) are shown but get
no category and no vote. The mapping is deliberately simple; please open an
issue if a label is mapped wrongly.

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest              # offline tests against recorded broker responses
pytest -m live      # also hit the real APIs (bridge test needs alertissimo installed)
```

## Roadmap

- [ ] Lasair Sherlock `NT` as a second candidate source (needs token)
- [ ] Fink `latests` recipe translation upstream in Alertissimo ([notes](docs/alertissimo.md))
- [ ] Merge volunteer classifications back (Zooniverse export → consensus table)
- [ ] Minimal web page for browsing a saved search
- [ ] LSST object ids; ANTARES adapter

## Acknowledgements

nucleuswatch only consumes data the broker teams produce. If you use it in a
paper, please cite the brokers whose results you use, and Alertissimo if you
used the bridge.

## License

MIT
