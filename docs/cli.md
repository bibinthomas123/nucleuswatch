# Command line

[← Documentation](README.md)

Installing the package adds a `nucleuswatch` command. Run
`nucleuswatch <command> --help` for the same information in the terminal.

| Command | What it does |
|---|---|
| [`object`](#nucleuswatch-object) | Merged view of one object across brokers |
| [`nuclear`](#nucleuswatch-nuclear) | Search for recent nuclear transients |
| [`digest`](#nucleuswatch-digest) | Build the weekly record and web page |
| [`notify`](#nucleuswatch-notify) | Post the latest digest's TDE-like candidates to Slack or Discord |
| [`zooniverse-upload`](#nucleuswatch-zooniverse-upload) | Upload an exported subject set to Zooniverse |

---

## `nucleuswatch object`

```bash
nucleuswatch object OBJECT_ID [--brokers alerce fink lasair] [--no-cache] [--plot FILE]
```

| Argument | Default | Meaning |
|---|---|---|
| `OBJECT_ID` | | A ZTF name (`ZTF21abfmbix`) or a Rubin `diaObjectId` (`170587117485817955`). |
| `--brokers` | all | Brokers to query (`alerce fink lasair antares`). |
| `--no-cache` | off | Refetch everything instead of using cached responses. |
| `--plot FILE` | | Save the merged light curve to `FILE` (`.png` or `.pdf`). Needs `nucleuswatch[plot]`. |

It prints, in order:

- a per-broker summary (position, offset, detections, time span, TNS and
  SIMBAD facts);
- Sherlock's host description;
- every classifier output, with `*` marking the ones that vote;
- the consensus;
- any broker that couldn't be reached.

The exit code is 1 if no broker answered.

## `nucleuswatch nuclear`

```bash
nucleuswatch nuclear [--survey ztf|lsst] [--days 7] [--mag-max 19] [--distnr-max 0.5]
                     [--no-merge] [--csv FILE] [--zooniverse DIR] [--no-cache]
```

| Flag | Default | Meaning |
|---|---|---|
| `--survey` | `ztf` | `ztf` uses Fink's class streams and the ZTF nuclear cuts. `lsst` uses Fink's Rubin `extragalactic_lt20mag_candidate` stream and a Legacy Surveys DR10 host match. |
| `--days` | `7` | Look back this many days. |
| `--mag-max` | `19` | Faintest magnitude. |
| `--distnr-max` | `0.5` | ZTF: arcsec to the nearest reference-image source. LSST: arcsec to the nearest DR10 galaxy. |
| `--no-merge` | off | Skip the per-object broker lookups and consensus (much faster). |
| `--csv FILE` | | Write the candidate table to a CSV file. |
| `--zooniverse DIR` | | Export a Zooniverse subject set for the candidates to `DIR` (ZTF). |
| `--no-cache` | off | Refetch instead of using cached responses. |

Examples:

```bash
nucleuswatch nuclear --days 14 --mag-max 20 --csv two_weeks.csv
nucleuswatch nuclear --survey lsst --mag-max 20
nucleuswatch nuclear --zooniverse week43/   # then: nucleuswatch zooniverse-upload week43/ ...
```

The command line exposes only the most-used cuts. The full set
(`distpsnr1_max`, `sgscore1_max`, `ndethist_min`, the new/known split) is
available from Python: see [`nuclear_transients`](python-api.md#searches).

## `nucleuswatch digest`

```bash
nucleuswatch digest [--days 7] [--mag-max 20] [--until YYYY-MM-DD] [--brokers ...]
                    [--data digest] [--site _site] [--no-cache]
```

| Flag | Default | Meaning |
|---|---|---|
| `--days` | `7` | Window length. |
| `--mag-max` | `20` | Faintest magnitude. Candidates at mag 19 or brighter are tagged `bright`. |
| `--until` | now | End of the window, for backfilling a past week. Lasair can't search the past, so backfilled weeks use Fink only. |
| `--brokers` | all | Brokers used for consensus. |
| `--data` | `digest` | Folder for the records (`<date>.json`, `.csv`, `_known.csv`, `latest.json`). |
| `--site` | `_site` | Folder for the generated web page and `feed.xml`. |
| `--no-cache` | off | Refetch instead of using cached responses. |

The run includes the ZTF search with the new/known split, consensus, TDE
scores and the Rubin search. To preview the page locally:

```bash
nucleuswatch digest --data /tmp/dg --site /tmp/site
python -m http.server -d /tmp/site 8000     # open http://localhost:8000
```

## `nucleuswatch notify`

```bash
nucleuswatch notify [--data digest] [--webhook URL] [--always]
```

| Flag | Default | Meaning |
|---|---|---|
| `--data` | `digest` | Folder containing `latest.json`. |
| `--webhook` | `$NUCLEUSWATCH_WEBHOOK` | A Slack or Discord incoming-webhook URL. If there is none, the message is printed instead of posted. |
| `--always` | off | Post even when no candidate is TDE-like. Without it, a quiet week posts nothing. |

The message is a headline plus one line per notable candidate: TDE score ≥
0.5, a TDE vote from any broker, or a TDE type on TNS. Setup is in
[weekly-digest.md → Chat alerts](weekly-digest.md#chat-alerts-slack-or-discord).

## `nucleuswatch zooniverse-upload`

```bash
nucleuswatch zooniverse-upload DIR --project ID --subject-set "Week 43"
```

| Argument | Meaning |
|---|---|
| `DIR` | A folder written by `nuclear --zooniverse` (contains `manifest.csv`). |
| `--project` | Your Zooniverse project ID. |
| `--subject-set` | Name for the new subject set. |

Needs `pip install "nucleuswatch[zooniverse]"` and the `ZOONIVERSE_USERNAME`
and `ZOONIVERSE_PASSWORD` environment variables.

---

## Environment variables

| Variable | Used by | Meaning |
|---|---|---|
| `LASAIR_TOKEN` | Lasair (ZTF) | Your Lasair API token. Get it free from your profile at <https://lasair-ztf.lsst.ac.uk>. Without it, Lasair is skipped and the reason is recorded. |
| `LASAIR_LSST_TOKEN` | Lasair (Rubin) | A separate token from <https://lasair.lsst.ac.uk>. The ZTF token is rejected there. |
| `NUCLEUSWATCH_WEBHOOK` | `notify` | Slack or Discord incoming-webhook URL. |
| `NUCLEUSWATCH_CACHE` | everything | Cache directory (default `~/.cache/nucleuswatch`). |
| `ZOONIVERSE_USERNAME`, `ZOONIVERSE_PASSWORD` | `zooniverse-upload` | Zooniverse credentials. |

The two Lasair tokens can also be put in a `.env` file in the working
directory, one `NAME=value` per line. Keep `.env` out of git; the
repository's `.gitignore` already excludes it.
