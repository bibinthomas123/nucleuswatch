# nucleuswatch documentation

nucleuswatch finds transients on the nuclei of galaxies (tidal disruption
events, AGN flares, nuclear supernovae) in the ZTF and Rubin/LSST alert
streams. It asks the ALeRCE, Fink and Lasair brokers what each one is, and
reports whether they agree.

| Page | What it covers |
|---|---|
| [How it works](how-it-works.md) | The pipeline end to end: broker adapters, merging, consensus, the nuclear search, new vs known, the TDE score, Rubin, caching and rate limits |
| [Python API](python-api.md) | Every public function and class, with parameters, defaults and return values |
| [Command line](cli.md) | `nucleuswatch object / nuclear / digest / notify / zooniverse-upload`, every flag, and the environment variables |
| [Weekly digest and alerts](weekly-digest.md) | The GitHub Actions workflow, repository secrets, Slack/Discord alerts, the Atom feed, and the format of the files in `digest/` |
| [Sherlock note](sherlock-note.md) | Why Lasair's Sherlock calls some galaxy nuclei variable stars, with examples and a suggested fix |
| [Alertissimo notes](alertissimo.md) | How nucleuswatch relates to Alertissimo, and what we found while integrating it |

Validation results live next to the scripts that produce them:

| Report | Question it answers |
|---|---|
| [RESULTS.md](../validation/RESULTS.md) | What fraction of confirmed TDEs pass the nuclear cuts? |
| [END_TO_END.md](../validation/END_TO_END.md) | Replaying the weekly search over 2024–2025, how many TDEs does it select, and how fast? |
| [BROKER_TRUTH.md](../validation/BROKER_TRUTH.md) | How do broker classifications compare with spectroscopic types? |
| [TDE_SCORE.md](../validation/TDE_SCORE.md) | How well does the TDE score rank TDEs above everything else? |
| [PROSPECTIVE.md](../validation/PROSPECTIVE.md) | Of the objects we picked each week, which turned out to be TDEs once spectra arrived? |

## Install

```bash
pip install nucleuswatch            # core
pip install "nucleuswatch[plot]"    # + matplotlib, for plots and the weekly site
pip install "nucleuswatch[zooniverse]"  # + panoptes-client, for uploading subject sets
```

Python 3.10 or newer. ALeRCE and Fink need no account. Lasair needs a free
token. See [Command line → Environment variables](cli.md#environment-variables).

## Thirty-second tour

```python
import nucleuswatch as nw

obj = nw.get_object("ZTF21abfmbix")     # ask ALeRCE, Fink and Lasair at once
obj.consensus()                          # {'status': 'agree', 'categories': ['SN'], 'votes': [...]}

search = nw.nuclear_transients(days=7)   # this week's nuclear transients, with consensus
search.candidates

from nucleuswatch.score import tde_score
tde_score(search.objects[search.candidates.object_id[0]])   # 0–1, higher = more TDE-like
```
