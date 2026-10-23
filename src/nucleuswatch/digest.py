"""Weekly digest: run the nuclear search, keep a small record, render a site.

    nucleuswatch digest --days 7 --data digest --site _site

- ``digest/<date>.json`` and ``digest/<date>.csv``: one small record per run,
  meant to be committed so the history lives in the repository;
- ``digest/latest.json``: the newest run;
- ``_site/``: a static web page (index.html + plots + stamps) for the newest
  run and an archive table of all runs. Meant for GitHub Pages; not committed.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from html import escape
from pathlib import Path

import pandas as pd

from . import __version__
from ._http import BrokerError
from .brokers import fink
from .core import MergedObject
from .recipes import CandidateSearch, NuclearCuts, nuclear_transients
from .taxonomy import categorize

REPO_URL = "https://github.com/bibinthomas123/nucleuswatch"


def build(days: float = 7, cuts: NuclearCuts | None = None, *, now: datetime | None = None,
          use_cache: bool = True) -> tuple[dict, CandidateSearch]:
    """Run the search and return (JSON-able record, search)."""
    now = now or datetime.now(timezone.utc)
    search = nuclear_transients(days, cuts, now=now, use_cache=use_cache)
    candidates = []
    for row in search.candidates.to_dict("records"):
        obj = search.objects.get(row["object_id"])
        candidates.append({**_clean(row), **(_object_record(obj) if obj else {})})
    record = {
        "generated": now.isoformat(timespec="seconds"),
        "nucleuswatch_version": __version__,
        "query": search.query,
        "errors": search.errors,
        "n_candidates": len(candidates),
        "n_disagree": sum(c.get("consensus") == "disagree" for c in candidates),
        "candidates": candidates,
    }
    return record, search


def write_data(record: dict, data_dir: str | Path) -> Path:
    """Write <date>.json, <date>.csv and latest.json; return the dated JSON path."""
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    stamp = record["generated"][:10]
    path = data_dir / f"{stamp}.json"
    text = json.dumps(record, indent=1, default=str)
    path.write_text(text)
    (data_dir / "latest.json").write_text(text)
    table = pd.DataFrame([{k: v for k, v in c.items() if not isinstance(v, (list, dict))}
                          for c in record["candidates"]])
    table.to_csv(data_dir / f"{stamp}.csv", index=False)
    return path


def render_site(record: dict, data_dir: str | Path, site_dir: str | Path,
                search: CandidateSearch | None = None, *, use_cache: bool = True) -> Path:
    """Write the static site for ``record``; plots/stamps come from ``search``."""
    site = Path(site_dir)
    (site / "img").mkdir(parents=True, exist_ok=True)
    (site / "data").mkdir(exist_ok=True)

    for c in record["candidates"]:
        obj = search.objects.get(c["object_id"]) if search else None
        if obj is not None:
            c["_images"] = _write_images(obj, site / "img", use_cache=use_cache)

    archive = []
    for path in sorted(Path(data_dir).glob("20*.json"), reverse=True):
        r = json.loads(path.read_text())
        (site / "data" / path.name).write_text(path.read_text())
        csv = path.with_suffix(".csv")
        if csv.exists():
            (site / "data" / csv.name).write_text(csv.read_text())
        archive.append({"date": path.stem, "n": r.get("n_candidates", 0),
                        "disagree": r.get("n_disagree", 0), "days": r["query"].get("days")})

    (site / "index.html").write_text(_page(record, archive))
    (site / ".nojekyll").write_text("")
    return site / "index.html"


# ------------------------------------------------------------------ internals

def _clean(row: dict) -> dict:
    out = {}
    for k, v in row.items():
        if isinstance(v, float) and math.isnan(v):
            v = None
        elif hasattr(v, "item"):
            v = v.item()
        out[k] = v
    return out


def _object_record(obj: MergedObject) -> dict:
    c = obj.consensus()
    def first(key):
        return next((r.extra[key] for r in obj.results.values() if r.extra.get(key)), None)

    sherlock = next((r.extra.get("sherlock_description") for r in obj.results.values()
                     if r.extra.get("sherlock_description")), None)
    return {
        "votes": c["votes"],
        "links": {name: r.url for name, r in obj.results.items() if r.url},
        "broker_errors": obj.errors,
        "tns_name": first("tns"),
        "tns_type": first("tns_type"),
        "sherlock": sherlock,
        "n_detections": int(obj.lightcurve["candid"].nunique()) if len(obj.lightcurve) else 0,
    }


def _write_images(obj: MergedObject, img_dir: Path, *, use_cache: bool) -> dict:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from .zooniverse import draw_stamps

    images = {}
    ax = obj.plot()
    ax.figure.set_size_inches(7.5, 4)
    ax.figure.tight_layout()
    name = f"{obj.object_id}_lc.png"
    ax.figure.savefig(img_dir / name, dpi=100)
    plt.close(ax.figure)
    images["lightcurve"] = f"img/{name}"

    if obj.survey == "ztf":
        try:
            stamps = {k: fink.cutout(obj.object_id, k, use_cache=use_cache) for k in fink.CUTOUT_KINDS}
        except BrokerError:
            stamps = None
        if stamps:
            fig, axes = plt.subplots(1, 3, figsize=(6.6, 2.5))
            draw_stamps(axes, stamps)
            fig.tight_layout()
            name = f"{obj.object_id}_stamps.png"
            fig.savefig(img_dir / name, dpi=100)
            plt.close(fig)
            images["stamps"] = f"img/{name}"
    return images


_CSS = """
:root { --bg:#ffffff; --fg:#1f2328; --muted:#59636e; --line:#d1d9e0; --card:#f6f8fa;
        --agree:#1a7f37; --disagree:#bc4c00; --insufficient:#9a6700; --link:#0969da; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#0d1117; --fg:#e6edf3; --muted:#9198a1; --line:#30363d; --card:#151b23;
          --agree:#3fb950; --disagree:#f0883e; --insufficient:#d29922; --link:#4493f8; } }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--fg);
       font:15px/1.5 -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
main { max-width:1100px; margin:0 auto; padding:24px 16px 64px; }
a { color:var(--link); text-decoration:none; } a:hover { text-decoration:underline; }
h1 { font-size:28px; margin:0 0 4px; } h2 { font-size:20px; margin:40px 0 12px; }
.muted { color:var(--muted); }
.stats { display:flex; gap:12px; flex-wrap:wrap; margin:20px 0; }
.stat { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:12px 16px; min-width:140px; }
.stat b { display:block; font-size:24px; }
.table-wrap { overflow-x:auto; }
table { border-collapse:collapse; width:100%; font-size:14px; }
th, td { border-bottom:1px solid var(--line); padding:8px 10px; text-align:left; white-space:nowrap; }
th { color:var(--muted); font-weight:600; }
td.num { text-align:right; font-variant-numeric:tabular-nums; }
.badge { display:inline-block; padding:1px 8px; border-radius:999px; font-size:12px; font-weight:600;
         border:1px solid currentColor; }
.agree { color:var(--agree); } .disagree { color:var(--disagree); } .insufficient { color:var(--insufficient); }
.card { border:1px solid var(--line); border-radius:10px; padding:16px; margin:16px 0; background:var(--card); }
.card h3 { margin:0 0 8px; font-size:18px; }
.card img { max-width:100%; height:auto; border-radius:6px; background:#fff; }
.imgs { display:grid; grid-template-columns: minmax(0,1.4fr) minmax(0,1fr); gap:12px; align-items:start; margin-top:12px; }
@media (max-width: 760px) { .imgs { grid-template-columns: 1fr; } }
ul.votes { margin:6px 0; padding-left:18px; }
footer { margin-top:48px; font-size:13px; }
"""


def _badge(status: str | None) -> str:
    status = status or "insufficient"
    return f'<span class="badge {escape(status)}">{escape(status)}</span>'


def _fmt(v, digits=2) -> str:
    return "–" if v is None else (f"{v:.{digits}f}" if isinstance(v, float) else escape(str(v)))


def _page(record: dict, archive: list[dict]) -> str:
    q = record["query"]
    cands = record["candidates"]
    rows = "".join(
        f"<tr><td><a href='#{escape(c['object_id'])}'>{escape(c['object_id'])}</a></td>"
        f"<td class='num'>{_fmt(c.get('mag'))}</td><td>{_fmt(c.get('band'))}</td>"
        f"<td class='num'>{_fmt(c.get('distnr'))}</td><td class='num'>{_fmt(c.get('sgscore1'))}</td>"
        f"<td>{_fmt(c.get('fink_class'))}</td><td>{_badge(c.get('consensus'))}</td>"
        f"<td>{_fmt(c.get('categories'))}</td><td>{_fmt(c.get('tns_name'))}</td>"
        f"<td>{_fmt(c.get('tns_type'))}</td></tr>"
        for c in cands
    )
    cards = "".join(_card(c) for c in cands)
    archive_rows = "".join(
        f"<tr><td>{escape(a['date'])}</td><td class='num'>{a['n']}</td><td class='num'>{a['disagree']}</td>"
        f"<td><a href='data/{escape(a['date'])}.json'>json</a> · <a href='data/{escape(a['date'])}.csv'>csv</a></td></tr>"
        for a in archive
    )
    cuts = ", ".join(f"<code>{escape(k)}={escape(str(v))}</code>" for k, v in q.get("cuts", {}).items())
    errors = "".join(f"<li>{escape(e)}</li>" for e in record.get("errors", []))
    empty = "<p class='muted'>No candidates passed the cuts in this window.</p>" if not cands else ""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>nucleuswatch weekly</title>
<meta name="description" content="Nuclear transients from the ZTF alert stream, with ALeRCE / Fink / Lasair consensus.">
<style>{_CSS}</style></head>
<body><main>
<h1>nucleuswatch weekly</h1>
<p class="muted">Nuclear transients (TDEs, AGN flares, nuclear SNe) in the ZTF alert stream, and whether
ALeRCE, Fink and Lasair agree on what they are. Window {escape(q.get('startdate', ''))} → {escape(q.get('stopdate', ''))},
generated {escape(record['generated'])} with nucleuswatch {escape(record['nucleuswatch_version'])}.</p>
<div class="stats">
 <div class="stat"><b>{record['n_candidates']}</b>candidates</div>
 <div class="stat"><b class="disagree">{record['n_disagree']}</b>brokers disagree</div>
 <div class="stat"><b>{sum(c.get('consensus') == 'agree' for c in cands)}</b>brokers agree</div>
</div>
<h2>Candidates</h2>{empty}
<div class="table-wrap"><table>
<thead><tr><th>object</th><th>mag</th><th>band</th><th>distnr ″</th><th>sgscore1</th><th>Fink class</th>
<th>consensus</th><th>categories</th><th>TNS</th><th>TNS type</th></tr></thead><tbody>{rows}</tbody></table></div>
{cards}
<h2>Archive</h2>
<div class="table-wrap"><table><thead><tr><th>date</th><th>candidates</th><th>disagree</th><th>data</th></tr></thead>
<tbody>{archive_rows}</tbody></table></div>
{"<h2>Search errors</h2><ul>" + errors + "</ul>" if errors else ""}
<footer class="muted">Cuts: {cuts}. Candidates come from Fink class streams; consensus uses ALeRCE, Fink and Lasair.
Validation and method: <a href="{REPO_URL}">{REPO_URL}</a>. Broker data belongs to the broker teams; please cite them.</footer>
</main></body></html>
"""


def _card(c: dict) -> str:
    oid = escape(c["object_id"])
    votes = "".join(f"<li><b>{escape(v['broker'])}</b> {escape(v['classifier'])}: {escape(str(v['label']))}"
                    f" → {escape(str(v['category']))}</li>" for v in c.get("votes", []))
    links = " · ".join(f"<a href='{escape(u)}'>{escape(b)}</a>" for b, u in c.get("links", {}).items())
    imgs = c.get("_images", {})
    img_html = "".join(f"<img src='{escape(src)}' alt='{oid} {escape(kind)}' loading='lazy'>"
                       for kind, src in imgs.items())
    sherlock = f"<p class='muted'>Sherlock: {escape(c['sherlock'])}</p>" if c.get("sherlock") else ""
    if c.get("tns_type"):
        sherlock = (f"<p><b>Spectroscopic (TNS): {escape(str(c['tns_type']))}</b>"
                    f" → {escape(str(categorize(c['tns_type'])))}</p>") + sherlock
    return f"""<div class="card" id="{oid}">
<h3>{oid} {_badge(c.get('consensus'))}</h3>
<div class="muted">mag {_fmt(c.get('mag'))} ({_fmt(c.get('band'))}) · distnr {_fmt(c.get('distnr'))}″ ·
sgscore1 {_fmt(c.get('sgscore1'))} · {c.get('n_detections', 0)} detections · {links}</div>
<ul class="votes">{votes}</ul>{sherlock}
<div class="imgs">{img_html}</div></div>"""
