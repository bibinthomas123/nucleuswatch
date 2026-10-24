# Weekly digest and alerts

[← Documentation](README.md)

Every Monday at 06:00 UTC, a GitHub Actions workflow
([`weekly.yml`](../.github/workflows/weekly.yml)) runs these steps:

1. `nucleuswatch digest --days 7 --data digest --site _site` runs the ZTF
   and Rubin searches, merges and scores the candidates, and builds the page.
2. `python validation/prospective.py` checks earlier picks against TNS
   classifications that have arrived since. If a broker is down, this step
   fails without blocking the rest.
3. The bot commits `digest/` and the prospective results to `main`.
4. The page is deployed to GitHub Pages:
   **https://bibinthomas123.github.io/nucleuswatch/**
5. `nucleuswatch notify` posts the TDE-like candidates to chat, if a webhook
   is configured.

The workflow also runs when code under `src/nucleuswatch/` or the workflow
file changes on `main`, so the page never lags behind the code. Those runs
don't post to chat. You can also start it by hand: **Actions → Weekly digest
→ Run workflow**, with an optional look-back in days.

## One-time setup (forks)

1. **Settings → Pages → Build and deployment → Source: GitHub Actions.**
2. **Settings → Secrets and variables → Actions → New repository secret**
   for each of these, all optional:

   | Secret | Effect |
   |---|---|
   | `LASAIR_TOKEN` | Adds Lasair's Sherlock nuclear transients as a candidate source and Sherlock's vote to the consensus. Without it, the digest uses Fink and ALeRCE only. |
   | `NUCLEUSWATCH_WEBHOOK` | Posts each Monday's TDE-like candidates to Slack or Discord (next section). |

3. **Actions → Weekly digest → Run workflow** to build the first page.

## Chat alerts (Slack or Discord)

The Monday run can post a message like this:

```
Nucleus Watch, week ending 2026-10-26: 54 new nuclear candidates, 4 TDE-like. https://bibinthomas123.github.io/nucleuswatch/
• ZTF26acakche (mag 17.6): TDE score 0.63; brokers disagree https://…/#ZTF26acakche
• ZTF26acbvbhf (mag 19.8): TDE score 0.49; TDE vote from alerce; brokers disagree https://…/#ZTF26acbvbhf
```

A candidate is listed when its TDE score is at least 0.5, any broker votes
TDE, or TNS gives it a TDE type. Weeks with no such candidate post nothing.

### 1. Create the webhook

**Slack**
1. Go to <https://api.slack.com/apps> → **Create New App → From scratch**.
   Name it (e.g. "Nucleus Watch") and pick your workspace.
2. In the app's settings, open **Incoming Webhooks** and switch **Activate
   Incoming Webhooks** on.
3. Click **Add New Webhook to Workspace**, choose the channel, and **Allow**.
4. Copy the URL. It looks like `https://hooks.slack.com/services/T…/B…/…`.

**Discord**
1. Open the channel's settings (gear icon) → **Integrations → Webhooks →
   New Webhook**.
2. Name it, then **Copy Webhook URL**. It looks like
   `https://discord.com/api/webhooks/…/…`.

nucleuswatch tells the two apart by the URL's host and sends the payload
each one expects.

### 2. Test it from your machine

```bash
pip install -U nucleuswatch
git clone https://github.com/bibinthomas123/nucleuswatch && cd nucleuswatch
nucleuswatch notify --data digest                                  # print only
NUCLEUSWATCH_WEBHOOK='https://hooks.slack.com/services/…' \
  nucleuswatch notify --data digest --always                       # really post
```

`--always` posts even if this week has nothing TDE-like, which is handy for
a test.

### 3. Add it to GitHub

**Settings → Secrets and variables → Actions → New repository secret**:
name `NUCLEUSWATCH_WEBHOOK`, value the URL. From the next Monday, the
scheduled run posts automatically.

A webhook URL lets anyone post to your channel, so treat it like a password:
keep it in the secret, never in the repository. If it leaks, delete the
webhook in Slack or Discord and create a new one.

## Atom feed

The site publishes **https://bibinthomas123.github.io/nucleuswatch/feed.xml**.
It has one entry per notable candidate per week, for the last 12 weeks. Ways
to use it:

- **Slack:** `/feed subscribe https://bibinthomas123.github.io/nucleuswatch/feed.xml`
  in any channel (needs the RSS app), with no webhook to set up.
- **Any feed reader:** Feedly, NetNewsWire, Thunderbird and so on.
- **Email:** feed-to-email services such as Blogtrottr.

## The data files

Each run writes to `digest/`, and the bot commits the result:

| File | Contents |
|---|---|
| `<date>.json` | The full record for the week ending `<date>` (below). |
| `<date>.csv` | The new candidates, one row each (the flat fields of the JSON). |
| `<date>_known.csv` | Known nuclear sources active that week. |
| `latest.json` | A copy of the newest record. |

### Record (`<date>.json`)

| Key | Description |
|---|---|
| `generated` | End of the search window (ISO time). |
| `created` | When the run happened. |
| `backfill` | `true` if the window ended more than a day before the run. |
| `nucleuswatch_version` | Package version that produced it. |
| `query` | Window, cuts, sources requested and used, and the new/known parameters. |
| `errors` | Problems that didn't stop the run. |
| `funnel` | Alerts scanned → objects → passed cuts → known / new. |
| `n_candidates`, `n_agree`, `n_disagree`, `n_bright`, `n_tde_like`, `n_known` | Headline counts. |
| `candidates` | New candidates, highest TDE score first (below). |
| `known` | `object_id, ra, dec, mag, band, mjd, sources, age_days` per known source. |
| `lsst` | The Rubin search: `query`, `funnel`, `errors`, `candidates`. |

### Candidate

| Key | Description |
|---|---|
| `object_id`, `ra`, `dec` | Identity and position. |
| `mag`, `band`, `mjd` | Brightest alert that passed the cuts. |
| `distnr`, `distpsnr1`, `sgscore1`, `ndethist` | That alert's cut values. |
| `tier` | `bright` (≤ 19) or `faint`. |
| `tde_score` | 0–1 ranking score ([how it works](how-it-works.md#4-the-tde-score)). |
| `consensus`, `categories` | Broker agreement and the categories voted for. |
| `votes` | `[{broker, classifier, label, category}, …]`. |
| `broker_labels` | The votes as one string. |
| `fink_class`, `sources` | Which streams found it. |
| `first_seen_mjd`, `first_pass_mjd`, `age_days` | When it was first detected and first passed the cuts. |
| `flare_onset_mjd`, `prior_alerts` | Flare history, for objects older than a year. |
| `tns_name`, `tns_type`, `sherlock` | TNS designation and spectroscopic type; Sherlock's host description. |
| `n_detections`, `first_mjd`, `peak_mag` | From the merged light curve. |
| `links` | Each broker's page for the object. |
| `broker_errors` | Brokers that couldn't be reached, and why. |

Load a season of records in Python:

```python
import json, pathlib, pandas as pd
records = [json.loads(p.read_text()) for p in sorted(pathlib.Path("digest").glob("20*.json"))]
df = pd.DataFrame([{**c, "week": r["generated"][:10]} for r in records for c in r["candidates"]])
```

## Volunteer votes

If `zooniverse/classifications.csv` exists in the repository, the digest
reads it with `zooniverse.read_classifications`. Every candidate that
volunteers have classified gets the volunteers' majority answer as an extra
vote, shown on its card as `zooniverse volunteers`. Refresh the file by
downloading a new classification export from the project's *Data Exports*
page and committing it. `record["n_volunteer"]` counts the candidates it
touched.

## Backfilling

```bash
nucleuswatch digest --until 2026-10-16 --data digest --site _site
```

This rebuilds the week ending 16 October. Lasair's query API only searches
up to now, so backfilled weeks use Fink candidates only, and the record says
so in `query.sources`.
