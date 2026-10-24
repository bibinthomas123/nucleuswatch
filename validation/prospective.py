"""Score the weekly picks against spectra that arrived after we made them.

    python validation/prospective.py

The other validation scripts use today's broker outputs, which have seen the
whole light curve. This one uses only what the digest recorded at the time
(``digest/<date>.json``): the consensus, each broker's vote and the TDE score.
An object counts once, at its first appearance. It enters the test when it
had no TNS type then and has one now, so the answer can't have influenced the
pick. Current TNS types come from Fink.

Writes validation/prospective.json and validation/PROSPECTIVE.md. The weekly
workflow runs this after each digest, so the sample grows every week.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from nucleuswatch import BrokerError
from nucleuswatch.brokers import fink
from nucleuswatch.taxonomy import categorize

HERE = Path(__file__).parent
DIGEST = HERE.parent / "digest"
SCORE_BINS = [(0.5, 1.01, "≥ 0.5"), (0.1, 0.5, "0.1–0.5"), (0.0, 0.1, "< 0.1")]


def first_records() -> dict[str, dict]:
    """Each candidate as the digest first recorded it."""
    out: dict[str, dict] = {}
    for path in sorted(DIGEST.glob("20*.json")):
        rec = json.loads(path.read_text())
        for c in rec.get("candidates", []):
            if c["object_id"] in out:
                continue
            alerce = next((v for v in c.get("votes", []) if v["broker"] == "alerce"), None)
            out[c["object_id"]] = {
                "week": path.stem,
                "recorded": rec.get("created", rec.get("generated")),
                "backfill": bool(rec.get("backfill")),
                "consensus": c.get("consensus") or "insufficient",
                "alerce_vote": alerce["category"] if alerce else None,
                "tde_score": c.get("tde_score"),
                "tns_then": c.get("tns_type"),
                "mag": c.get("mag"),
            }
    return out


def tns_now(object_id: str) -> str | None:
    try:
        return fink.fetch(object_id).extra.get("tns_type")
    except (BrokerError, TypeError):
        return None


def summarise(rows: list[dict]) -> dict:
    def tally(key):
        out = {}
        for r in rows:
            k = key(r)
            n, t = out.get(k, (0, 0))
            out[k] = (n + 1, t + (r["class_now"] == "TDE"))
        return out

    def score_bin(r):
        s = r["tde_score"]
        return "not scored" if s is None else next(lab for lo, hi, lab in SCORE_BINS if lo <= s < hi)

    return {
        "by_consensus": tally(lambda r: r["consensus"]),
        "by_alerce_vote": tally(lambda r: "TDE" if r["alerce_vote"] == "TDE" else "other / none"),
        "by_score": tally(score_bin),
        "classes": dict(Counter(r["class_now"] or "other" for r in rows)),
    }


def _score(s: float | None) -> str:
    return "–" if s is None else f"{s:.2f}"


def render(r: dict) -> str:
    s = r["summary"]

    def table(title, d, order=None):
        keys = order or sorted(d)
        lines = [f"| {title} | classified | TDEs |", "|---|---|---|"]
        lines += [f"| {k} | {d[k][0]} | {d[k][1]} |" for k in keys if k in d]
        return lines

    rows = r["rows"]
    lines = [
        "# Prospective test: weekly picks vs later spectra",
        "",
        (f"Generated {r['generated']} by `validation/prospective.py`. {r['n_tracked']} candidates tracked across "
         f"{r['n_weeks']} digests; {len(rows)} had no TNS type when picked and have one now."),
        "",
        "Only what the digest recorded at the time is used. Weeks before 2026-10-23 were backfilled on that",
        "date, so for those the broker outputs date from 2026-10-23, not from the week itself.",
        "",
    ]
    if not rows:
        lines += ["No candidate has been classified since it was picked yet. Check back in a few weeks.", ""]
        return "\n".join(lines)
    lines += [
        f"Classified as: {', '.join(f'{k} {v}' for k, v in sorted(s['classes'].items(), key=lambda kv: -kv[1]))}.",
        "",
        *table("consensus when picked", s["by_consensus"], ["agree", "disagree", "insufficient"]),
        "",
        *table("ALeRCE vote when picked", s["by_alerce_vote"], ["TDE", "other / none"]),
        "",
        *table("TDE score when picked", s["by_score"], [b[2] for b in SCORE_BINS] + ["not scored"]),
        "",
        "## Objects",
        "",
        "| object | week | consensus | ALeRCE vote | TDE score | TNS type now |",
        "|---|---|---|---|---|---|",
        *(f"| {x['object_id']} | {x['week']} | {x['consensus']} | {x['alerce_vote'] or '–'} | "
          f"{_score(x['tde_score'])} | {x['tns_now']} |"
          for x in sorted(rows, key=lambda x: x["week"])),
        "",
        "The numbers are small and will stay so for months; read them as a running check, not a result.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    argparse.ArgumentParser(description=__doc__.splitlines()[0]).parse_args()
    picks = first_records()
    rows = []
    for oid, p in picks.items():
        if p["tns_then"]:
            continue
        now = tns_now(oid)
        if now:
            rows.append({"object_id": oid, **p, "tns_now": now, "class_now": categorize(now)})
    result = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "n_weeks": len(list(DIGEST.glob("20*.json"))),
        "n_tracked": len(picks),
        "rows": rows,
        "summary": summarise(rows),
    }
    (HERE / "prospective.json").write_text(json.dumps(result, indent=1))
    (HERE / "PROSPECTIVE.md").write_text(render(result))
    print((HERE / "PROSPECTIVE.md").read_text())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
