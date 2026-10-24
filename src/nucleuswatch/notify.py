"""Tell people when the digest finds something: an Atom feed and a chat webhook.

- ``feed.xml`` on the site: one entry per notable candidate per week, for any
  feed reader (or Slack's /feed command).
- ``nucleuswatch notify``: posts this week's notable candidates to a Slack or
  Discord incoming webhook (``--webhook`` or ``NUCLEUSWATCH_WEBHOOK``).

A candidate is notable when it looks like a TDE by any of the measures the
page shows: TDE score >= digest.TDE_LIKE, a TDE vote from any broker, or a
TDE type on TNS. Objects stay on the weekly list for as long as they are
active, so the feed and the chat message announce each one in the week it
was first listed, and only mention returning ones briefly.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from xml.sax.saxutils import escape

import requests

from .taxonomy import categorize

SITE_URL = "https://bibinthomas123.github.io/nucleuswatch/"
FEED_WEEKS = 12


def is_notable(c: dict, threshold: float) -> bool:
    return ((c.get("tde_score") or 0) >= threshold
            or any(v.get("category") == "TDE" for v in c.get("votes", []))
            or categorize(c.get("tns_type")) == "TDE")


def has_news(record: dict) -> bool:
    """Whether this week has a TDE-like candidate that wasn't listed before."""
    return any(not c.get("returning") for c in notable(record))


def notable(record: dict) -> list[dict]:
    from .digest import TDE_LIKE
    return [c for c in record.get("candidates", []) if is_notable(c, TDE_LIKE)]


def _why(c: dict) -> str:
    reasons = []
    if c.get("tde_score") is not None:
        reasons.append(f"TDE score {c['tde_score']:.2f}")
    tde_votes = [v["broker"] for v in c.get("votes", []) if v.get("category") == "TDE"]
    if tde_votes:
        reasons.append("TDE vote from " + ", ".join(tde_votes))
    if c.get("tns_type"):
        reasons.append(f"TNS: {c['tns_type']}")
    reasons.append(f"brokers {c.get('consensus') or 'insufficient'}")
    return "; ".join(reasons)


def atom_feed(records: list[dict]) -> str:
    """Atom XML for the notable candidates in ``records`` (newest first)."""
    entries = []
    for rec in records:
        week = rec["generated"][:10]
        for c in notable(rec):
            if c.get("returning"):
                continue  # announced in the week it was first listed
            oid = c["object_id"]
            mag = f"{c['mag']:.2f}" if isinstance(c.get("mag"), (int, float)) else "?"
            entries.append(f"""<entry>
 <title>{escape(oid)}: {escape(_why(c))}</title>
 <id>tag:nucleuswatch,{week}:{escape(oid)}</id>
 <link href="{SITE_URL}#{escape(oid)}"/>
 <updated>{escape(rec["generated"])}</updated>
 <summary>Nuclear transient at mag {mag} ({escape(str(c.get("band", "")))}), RA {c.get("ra") or 0:.5f},
 Dec {c.get("dec") or 0:.5f}. Week ending {week}.</summary>
</entry>""")
    updated = records[0]["generated"] if records else datetime.now(timezone.utc).isoformat(timespec="seconds")
    return f"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
<title>Nucleus Watch: TDE-like nuclear transients</title>
<subtitle>Weekly picks from the ZTF alert stream that look like tidal disruption events.</subtitle>
<link href="{SITE_URL}"/>
<link rel="self" href="{SITE_URL}feed.xml"/>
<id>{SITE_URL}</id>
<updated>{escape(updated)}</updated>
{chr(10).join(entries)}
</feed>
"""


def write_feed(data_dir: str | Path, site_dir: str | Path) -> Path:
    paths = sorted(Path(data_dir).glob("20*.json"), reverse=True)[:FEED_WEEKS]
    records = [json.loads(p.read_text()) for p in paths]
    out = Path(site_dir) / "feed.xml"
    out.write_text(atom_feed(records))
    return out


def message(record: dict) -> str:
    """Plain-text chat message for one digest (Slack and Discord both render it)."""
    picks = notable(record)
    fresh = [c for c in picks if not c.get("returning")]
    still = [c for c in picks if c.get("returning")]
    n_new = record.get("n_new_this_week", record["n_candidates"])
    head = (f"Nucleus Watch, week ending {record['generated'][:10]}: {record['n_candidates']} nuclear "
            f"candidates ({n_new} first listed this week), {len(fresh)} new TDE-like. {SITE_URL}")
    lines = [f"• {c['object_id']} (mag {c['mag']:.1f}): {_why(c)} {SITE_URL}#{c['object_id']}"
             for c in fresh if isinstance(c.get("mag"), (int, float))]
    if still:
        lines.append("Still TDE-like from earlier weeks: " + ", ".join(c["object_id"] for c in still))
    return "\n".join([head, *lines])


def post(url: str, text: str, *, timeout: float = 20) -> None:
    host = urlparse(url).hostname or ""
    body = {"content": text[:2000]} if host.endswith(("discord.com", "discordapp.com")) else {"text": text}
    requests.post(url, json=body, timeout=timeout).raise_for_status()
