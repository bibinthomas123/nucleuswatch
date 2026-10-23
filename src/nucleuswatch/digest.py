"""Weekly digest: run the nuclear search, keep a small record, render a site.

    nucleuswatch digest --days 7 --data digest --site _site

- ``digest/<date>.json`` and ``digest/<date>.csv``: one small record per run,
  meant to be committed so the history lives in the repository;
- ``digest/latest.json``: the newest run;
- ``_site/``: a static web page (index.html + plots + stamps) for the newest
  run, with a history chart and an archive of all runs. Meant for GitHub
  Pages; not committed.

The weekly search goes to ``mag_max=20`` (where validation recovers 72% of
confirmed TDEs) and labels each candidate *bright* (<= ``bright_mag``) or
*faint*. New sources are merged across brokers; known nuclear sources that
were active (mostly AGN) are listed separately.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path
from urllib.parse import urlencode

import pandas as pd

from . import __version__
from ._http import BrokerError
from .brokers import fink
from .core import MergedObject
from .recipes import CandidateSearch, NuclearCuts, nuclear_transients
from .taxonomy import categorize

REPO_URL = "https://github.com/bibinthomas123/nucleuswatch"
KNOWN_FIELDS = ["object_id", "ra", "dec", "mag", "band", "mjd", "sources", "age_days"]


def build(
    days: float = 7,
    mag_max: float = 20.0,
    *,
    bright_mag: float = 19.0,
    sources: tuple[str, ...] = ("fink", "lasair"),
    new_within_days: float = 365,
    brokers: list[str] | None = None,
    now: datetime | None = None,
    use_cache: bool = True,
) -> tuple[dict, CandidateSearch]:
    """Run the search and return (JSON-able record, search)."""
    now = now or datetime.now(timezone.utc)
    search = nuclear_transients(
        days, NuclearCuts(mag_max=mag_max), sources=sources, new_within_days=new_within_days,
        brokers=brokers, now=now, use_cache=use_cache,
    )
    candidates = []
    for row in search.candidates.to_dict("records"):
        obj = search.objects.get(row["object_id"])
        rec = {**_clean(row), **(_object_record(obj) if obj else {})}
        rec["tier"] = "bright" if (rec.get("mag") or 99) <= bright_mag else "faint"
        candidates.append(rec)
    known = [_clean({k: r.get(k) for k in KNOWN_FIELDS}) for r in search.known.to_dict("records")]
    record = {
        "generated": now.isoformat(timespec="seconds"),  # end of the search window
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),  # when this ran
        "backfill": now < datetime.now(timezone.utc) - timedelta(days=1),
        "nucleuswatch_version": __version__,
        "query": {**search.query, "bright_mag": bright_mag, "brokers": brokers or ["alerce", "fink", "lasair"]},
        "errors": search.errors,
        "funnel": search.funnel,
        "n_candidates": len(candidates),
        "n_disagree": sum(c.get("consensus") == "disagree" for c in candidates),
        "n_agree": sum(c.get("consensus") == "agree" for c in candidates),
        "n_bright": sum(c["tier"] == "bright" for c in candidates),
        "n_known": len(known),
        "candidates": candidates,
        "known": known,
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
    if not (data_dir / "latest.json").exists() or stamp >= _latest_stamp(data_dir):
        (data_dir / "latest.json").write_text(text)
    table = pd.DataFrame([{k: v for k, v in c.items() if not isinstance(v, (list, dict))}
                          for c in record["candidates"]])
    table.to_csv(data_dir / f"{stamp}.csv", index=False)
    if record.get("known"):
        pd.DataFrame(record["known"]).to_csv(data_dir / f"{stamp}_known.csv", index=False)
    return path


def render_site(record: dict, data_dir: str | Path, site_dir: str | Path,
                search: CandidateSearch | None = None, *, use_cache: bool = True,
                validation: str | Path | None = "validation/results.json") -> Path:
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
        for f in (path, path.with_suffix(".csv"), path.with_name(f"{path.stem}_known.csv")):
            if f.exists():
                (site / "data" / f.name).write_text(f.read_text())
        archive.append({"date": path.stem, "n": r.get("n_candidates", 0), "disagree": r.get("n_disagree", 0),
                        "known": r.get("n_known"), "days": r["query"].get("days"),
                        "sources": r["query"].get("sources", ["fink"])})

    val = None
    if validation and Path(validation).exists():
        val = json.loads(Path(validation).read_text())

    (site / "index.html").write_text(_page(record, archive, val))
    (site / ".nojekyll").write_text("")
    return site / "index.html"


# ------------------------------------------------------------------ internals

def _latest_stamp(data_dir: Path) -> str:
    try:
        return json.loads((data_dir / "latest.json").read_text())["generated"][:10]
    except (OSError, ValueError, KeyError):
        return ""


def _clean(row: dict) -> dict:
    out = {}
    for k, v in row.items():
        if hasattr(v, "item"):
            v = v.item()
        if isinstance(v, float) and math.isnan(v):
            v = None
        out[k] = v
    return out


def _object_record(obj: MergedObject) -> dict:
    c = obj.consensus()

    def first(key):
        return next((r.extra[key] for r in obj.results.values() if r.extra.get(key)), None)

    return {
        "votes": c["votes"],
        "links": {name: r.url for name, r in obj.results.items() if r.url},
        "broker_errors": obj.errors,
        "tns_name": first("tns"),
        "tns_type": first("tns_type"),
        "sherlock": first("sherlock_description"),
        "n_detections": int(obj.lightcurve["candid"].nunique()) if len(obj.lightcurve) else 0,
        "first_mjd": _num(obj.lightcurve["mjd"].min()) if len(obj.lightcurve) else None,
        "peak_mag": _num(obj.lightcurve["mag"].min()) if len(obj.lightcurve) else None,
    }


def _num(v):
    return None if v is None or pd.isna(v) else float(v)


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


def _legacy_survey(ra, dec, size=160) -> str | None:
    if ra is None or dec is None:
        return None
    q = urlencode({"ra": f"{ra:.5f}", "dec": f"{dec:.5f}", "layer": "ls-dr10", "pixscale": 0.262, "size": size})
    return f"https://www.legacysurvey.org/viewer/cutout.jpg?{q}"


def _legacy_viewer(ra, dec) -> str | None:
    if ra is None or dec is None:
        return None
    return f"https://www.legacysurvey.org/viewer?ra={ra:.5f}&dec={dec:.5f}&layer=ls-dr10&zoom=15&mark={ra:.5f},{dec:.5f}"


# ------------------------------------------------------------------ HTML

_CSS = """
:root { --bg:#ffffff; --fg:#1f2328; --muted:#59636e; --line:#d1d9e0; --card:#f6f8fa; --bar:#d0d7de;
        --agree:#1a7f37; --disagree:#bc4c00; --insufficient:#9a6700; --link:#0969da; --known:#8c959f; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#0d1117; --fg:#e6edf3; --muted:#9198a1; --line:#30363d; --card:#151b23; --bar:#30363d;
          --agree:#3fb950; --disagree:#f0883e; --insufficient:#d29922; --link:#4493f8; --known:#6e7681; } }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--fg);
       font:15px/1.55 -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
main { max-width:1120px; margin:0 auto; padding:24px 16px 64px; }
a { color:var(--link); text-decoration:none; } a:hover { text-decoration:underline; }
h1 { font-size:30px; margin:0 0 4px; } h2 { font-size:21px; margin:44px 0 12px; padding-top:8px; }
h3 { font-size:17px; margin:0 0 6px; }
nav { display:flex; gap:14px; flex-wrap:wrap; margin:14px 0 4px; font-size:14px; }
.muted { color:var(--muted); } .small { font-size:13px; }
.stats { display:grid; grid-template-columns:repeat(auto-fit, minmax(150px, 1fr)); gap:12px; margin:20px 0; }
.stat { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:12px 16px; }
.stat b { display:block; font-size:26px; line-height:1.2; }
.grid2 { display:grid; grid-template-columns:repeat(auto-fit, minmax(300px, 1fr)); gap:16px; }
.panel { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:14px 16px; }
.bars div { display:grid; grid-template-columns: 9em 1fr 3em; gap:8px; align-items:center; margin:4px 0; font-size:14px; }
.bars span.b { height:12px; border-radius:6px; background:var(--link); display:block; }
.bars span.n { text-align:right; font-variant-numeric:tabular-nums; }
.table-wrap { overflow-x:auto; }
table { border-collapse:collapse; width:100%; font-size:14px; }
th, td { border-bottom:1px solid var(--line); padding:7px 10px; text-align:left; white-space:nowrap; }
th { color:var(--muted); font-weight:600; }
td.num { text-align:right; font-variant-numeric:tabular-nums; }
.badge { display:inline-block; padding:0 8px; border-radius:999px; font-size:12px; font-weight:600; border:1px solid currentColor; }
.agree { color:var(--agree); } .disagree { color:var(--disagree); } .insufficient { color:var(--insufficient); }
.tier { color:var(--muted); }
.card { border:1px solid var(--line); border-radius:10px; padding:16px; margin:16px 0; background:var(--card); }
.card img { max-width:100%; height:auto; border-radius:6px; background:#fff; display:block; }
.imgs { display:grid; grid-template-columns: 160px minmax(0,1.5fr) minmax(0,1fr); gap:12px; align-items:start; margin-top:12px; }
.host { width:160px; } .host img { width:160px; height:160px; background:#000; }
@media (max-width: 860px) { .imgs { grid-template-columns: 1fr; } .host img { width:160px; } }
ul.votes { margin:6px 0; padding-left:18px; }
svg text { fill:var(--muted); font-size:11px; }
svg .grid { stroke:var(--line); stroke-width:1; fill:none; }
details { margin:8px 0; } summary { cursor:pointer; color:var(--link); }
footer { margin-top:56px; font-size:13px; }
code { font-size:13px; }
"""


def _badge(status: str | None) -> str:
    status = status or "insufficient"
    return f'<span class="badge {escape(status)}">{escape(status)}</span>'


def _fmt(v, digits=2) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "–"
    return f"{v:.{digits}f}" if isinstance(v, float) else escape(str(v))


def _bars(counts: dict, colour_classes: dict | None = None, *, keep_order: bool = False) -> str:
    if not counts:
        return "<p class='muted small'>none</p>"
    top = max(counts.values()) or 1
    rows = []
    items = counts.items() if keep_order else sorted(counts.items(), key=lambda kv: -kv[1])
    for label, n in items:
        cls = (colour_classes or {}).get(label, "")
        style = "background:currentColor" if cls else ""
        rows.append(f"<div class='{escape(cls)}'><span>{escape(str(label))}</span>"
                    f"<span class='b' style='width:{100 * n / top:.0f}%;{style}'></span><span class='n'>{n}</span></div>")
    return "<div class='bars'>" + "".join(rows) + "</div>"


def _sky_map(cands: list[dict], known: list[dict]) -> str:
    """Equirectangular RA/Dec map; RA increases to the left, as on the sky."""
    w, h, pad = 720, 340, 30
    x = lambda ra: pad + (360 - ra) / 360 * (w - 2 * pad)
    y = lambda dec: pad + (90 - dec) / 180 * (h - 2 * pad)
    parts = [f'<svg viewBox="0 0 {w} {h}" width="100%" role="img" aria-label="Sky positions of candidates">',
             f'<rect class="grid" x="{pad}" y="{pad}" width="{w - 2 * pad}" height="{h - 2 * pad}"/>']
    for ra in range(0, 361, 60):
        parts.append(f'<line class="grid" x1="{x(ra):.1f}" y1="{pad}" x2="{x(ra):.1f}" y2="{h - pad}"/>'
                     f'<text x="{x(ra):.1f}" y="{h - pad + 14}" text-anchor="middle">{ra}°</text>')
    for dec in range(-60, 91, 30):
        parts.append(f'<line class="grid" x1="{pad}" y1="{y(dec):.1f}" x2="{w - pad}" y2="{y(dec):.1f}"/>'
                     f'<text x="{pad - 4}" y="{y(dec) + 4:.1f}" text-anchor="end">{dec}°</text>')
    for k in known:
        if k.get("ra") is not None and k.get("dec") is not None:
            parts.append(f'<circle cx="{x(k["ra"]):.1f}" cy="{y(k["dec"]):.1f}" r="2" fill="var(--known)" opacity=".6">'
                         f'<title>{escape(k["object_id"])} (known)</title></circle>')
    colour = {"agree": "var(--agree)", "disagree": "var(--disagree)"}
    for c in cands:
        if c.get("ra") is not None and c.get("dec") is not None:
            fill = colour.get(c.get("consensus"), "var(--insufficient)")
            parts.append(f'<a href="#{escape(c["object_id"])}"><circle cx="{x(c["ra"]):.1f}" cy="{y(c["dec"]):.1f}" r="5" '
                         f'fill="{fill}" stroke="var(--bg)" stroke-width="1.5"><title>{escape(c["object_id"])} '
                         f'({escape(str(c.get("consensus")))})</title></circle></a>')
    parts.append("</svg>")
    return "".join(parts)


def _history_chart(archive: list[dict]) -> str:
    runs = sorted(archive, key=lambda a: a["date"])[-26:]
    if len(runs) < 2:
        return "<p class='muted small'>The chart fills in as weekly runs accumulate.</p>"
    w, h, pad = 720, 200, 30
    top = max(max(a["n"] for a in runs), 1)
    bw = (w - 2 * pad) / len(runs)
    parts = [f'<svg viewBox="0 0 {w} {h}" width="100%" role="img" aria-label="Candidates per run">']
    for i, a in enumerate(runs):
        bh = (h - 2 * pad) * a["n"] / top
        dh = (h - 2 * pad) * a["disagree"] / top
        x0 = pad + i * bw + 2
        parts.append(f'<rect x="{x0:.1f}" y="{h - pad - bh:.1f}" width="{bw - 4:.1f}" height="{bh:.1f}" fill="var(--bar)">'
                     f'<title>{escape(a["date"])}: {a["n"]} candidates, {a["disagree"]} disagree</title></rect>'
                     f'<rect x="{x0:.1f}" y="{h - pad - dh:.1f}" width="{bw - 4:.1f}" height="{dh:.1f}" fill="var(--disagree)"/>')
        if i % max(1, len(runs) // 8) == 0:
            parts.append(f'<text x="{x0 + (bw - 4) / 2:.1f}" y="{h - pad + 14}" text-anchor="middle">{escape(a["date"][5:])}</text>')
    parts.append(f'<text x="{pad}" y="{pad - 8}">max {top}</text></svg>')
    return "".join(parts) + "<p class='muted small'>Grey: new candidates per run. Orange: brokers disagree.</p>"


def _validation_panel(val: dict | None) -> str:
    if not val:
        return ""
    t = val["tde"]
    curve = " · ".join(f"mag ≤ {m}: {100 * n / t['total']:.0f}%" for m, n in t["recovered_vs_mag_max"].items())
    comp = val["composition"]
    all_n = sum(c["total"] for c in comp.values()) or 1
    pass_n = sum(c["passed"] for c in comp.values()) or 1
    before, after = 100 * comp["TDE"]["total"] / all_n, 100 * comp["TDE"]["passed"] / pass_n
    return f"""<div class="panel"><h3>How good are the cuts?</h3>
<p>Tested on {t['total']} spectroscopically confirmed TDEs ({escape(val['window'][0])} → {escape(val['window'][1])}):
<b>{t['passed']} ({100 * t['passed'] / t['total']:.0f}%)</b> pass at mag ≤ {val['cuts']['mag_max']}.
Recovery by magnitude limit: {curve}.</p>
<p>The cuts raise the TDE share of classified objects from {before:.1f}% to <b>{after:.1f}%</b>
(×{after / before:.1f}). Nuclear SNe remain the majority. That's expected, and it's what a
nuclear sample costs in follow-up. <a href="{REPO_URL}/blob/main/validation/RESULTS.md">Full results</a>.</p></div>"""


def _page(record: dict, archive: list[dict], val: dict | None) -> str:
    q = record["query"]
    cands = record["candidates"]
    known = record.get("known", [])
    funnel = record.get("funnel", {})

    rows = "".join(
        f"<tr><td><a href='#{escape(c['object_id'])}'>{escape(c['object_id'])}</a></td>"
        f"<td class='num'>{_fmt(c.get('mag'))}</td><td>{_fmt(c.get('band'))}</td>"
        f"<td class='tier'>{escape(c.get('tier', ''))}</td>"
        f"<td class='num'>{_fmt(c.get('age_days'), 0) if c.get('first_seen_mjd') else '–'}</td>"
        f"<td>{_badge(c.get('consensus'))}</td><td>{_fmt(c.get('categories'))}</td>"
        f"<td>{_fmt(c.get('tns_name'))}</td><td>{_fmt(c.get('tns_type'))}</td>"
        f"<td class='small'>{escape(c.get('sources') or '')}</td></tr>"
        for c in cands
    )
    cards = "".join(_card(c) for c in cands)

    consensus_counts = Counter(c.get("consensus") or "insufficient" for c in cands)
    category_counts = Counter(cat.strip() for c in cands for cat in (c.get("categories") or "").split(",") if cat.strip())
    source_counts = Counter(s.split(":")[0] + (":" + s.split(":")[1] if s.startswith("lasair") else "")
                            for c in cands for s in (c.get("sources") or "").split("; ") if s)
    stream_counts = Counter(s.removeprefix("fink:") for c in cands for s in (c.get("sources") or "").split("; ")
                            if s.startswith("fink:"))
    tns_typed = [c for c in cands if c.get("tns_type")]

    known_rows = "".join(
        f"<tr><td><a href='https://lasair-ztf.lsst.ac.uk/objects/{escape(k['object_id'])}/'>{escape(k['object_id'])}</a></td>"
        f"<td class='num'>{_fmt(k.get('mag'))}</td><td>{_fmt(k.get('band'))}</td>"
        f"<td class='num'>{_fmt(k.get('ra'), 4)}</td><td class='num'>{_fmt(k.get('dec'), 4)}</td>"
        f"<td class='small'>{escape(k.get('sources') or '')}</td></tr>"
        for k in known[:100]
    )
    stamp = record["generated"][:10]
    known_more = (f"<p class='muted small'>Showing the 100 brightest of {len(known)}. "
                  f"<a href='data/{escape(stamp)}_known.csv'>Download all (CSV)</a>.</p>") if len(known) > 100 else ""

    archive_rows = "".join(
        f"<tr><td>{escape(a['date'])}</td><td class='num'>{a['n']}</td><td class='num'>{a['disagree']}</td>"
        f"<td class='num'>{_fmt(a.get('known'))}</td><td class='small'>{escape(', '.join(a['sources']))}</td>"
        f"<td><a href='data/{escape(a['date'])}.json'>json</a> · <a href='data/{escape(a['date'])}.csv'>csv</a></td></tr>"
        for a in archive
    )
    cuts = q.get("cuts", {})
    errors = "".join(f"<li>{escape(e)}</li>" for e in record.get("errors", []))
    empty = "<p class='muted'>No new candidates passed the cuts in this window.</p>" if not cands else ""

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>nucleuswatch weekly</title>
<meta name="description" content="Nuclear transients from the ZTF alert stream, with ALeRCE / Fink / Lasair consensus.">
<style>{_CSS}</style></head>
<body><main>
<h1>nucleuswatch weekly</h1>
<p class="muted">New transients at the centres of galaxies (tidal disruption events, AGN flares, nuclear
supernovae) found in the ZTF alert stream, and whether the ALeRCE, Fink and Lasair brokers agree on what
they are. Window <b>{escape(q.get('startdate', ''))} → {escape(q.get('stopdate', ''))}</b>, generated
{escape(record['generated'])} with nucleuswatch {escape(record['nucleuswatch_version'])}.</p>
<nav><a href="#candidates">Candidates</a><a href="#details">Details</a><a href="#known">Known sources</a>
<a href="#history">History</a><a href="#method">Method</a><a href="#archive">Archive</a><a href="{REPO_URL}">Code</a></nav>

<div class="stats">
 <div class="stat"><b>{record['n_candidates']}</b>new nuclear candidates</div>
 <div class="stat"><b>{record.get('n_bright', 0)}</b>bright (mag ≤ {_fmt(q.get('bright_mag'), 0)})</div>
 <div class="stat"><b class="disagree">{record['n_disagree']}</b>brokers disagree</div>
 <div class="stat"><b class="agree">{record.get('n_agree', 0)}</b>brokers agree</div>
 <div class="stat"><b>{len(tns_typed)}</b>with a spectroscopic type</div>
 <div class="stat"><b>{record.get('n_known', 0)}</b>known nuclear sources active</div>
</div>

<div class="grid2">
 <div class="panel"><h3>Funnel</h3>{_bars({
     "alerts scanned": funnel.get("alerts_scanned", 0),
     "objects scanned": funnel.get("objects_scanned", 0),
     "passed cuts": funnel.get("passed_cuts", 0),
     "known sources": funnel.get("known", 0),
     "new candidates": funnel.get("new", 0)}, keep_order=True)}
  <p class="muted small">Alerts come from Fink's streams; objects also include Lasair's Sherlock "nuclear transient" list.</p></div>
 <div class="panel"><h3>Consensus</h3>{_bars(dict(consensus_counts), {"agree": "agree", "disagree": "disagree", "insufficient": "insufficient"})}
  <h3 style="margin-top:12px">Broker categories</h3>{_bars(dict(category_counts))}</div>
 <div class="panel"><h3>Where candidates came from</h3>{_bars(dict(source_counts))}
  <h3 style="margin-top:12px">Fink streams</h3>{_bars(dict(stream_counts))}</div>
</div>

<h2>Sky positions</h2>
<div class="panel">{_sky_map(cands, known)}
<p class="muted small">Large dots: new candidates (green agree, orange disagree, yellow insufficient). Small grey dots:
known nuclear sources active this week. Click a dot to jump to its details.</p></div>

<h2 id="candidates">New candidates</h2>{empty}
<div class="table-wrap"><table>
<thead><tr><th>object</th><th>mag</th><th>band</th><th>tier</th><th>age (d)</th><th>consensus</th><th>categories</th>
<th>TNS</th><th>TNS type</th><th>found via</th></tr></thead><tbody>{rows}</tbody></table></div>
<p class="muted small">Age: days since first detection, where Fink reports it. Bright: mag ≤ {_fmt(q.get('bright_mag'), 0)};
faint: up to {_fmt(cuts.get('mag_max'), 0)}. <a href="data/{escape(stamp)}.csv">Download (CSV)</a>.</p>

<h2 id="details">Candidate details</h2>{cards}

<h2 id="known">Known nuclear sources active this window</h2>
<p class="muted">Objects that pass the nuclear cuts but were first detected more than {_fmt(q.get('new_within_days'), 0)}
days ago, mostly AGN that vary all the time. A sudden flare in one of these can still be interesting, but they are
too many to compare across brokers every week, so they're listed here without consensus.</p>
<div class="table-wrap"><table><thead><tr><th>object</th><th>mag</th><th>band</th><th>RA</th><th>Dec</th><th>found via</th></tr></thead>
<tbody>{known_rows}</tbody></table></div>{known_more}

<h2 id="history">History</h2>
<div class="panel">{_history_chart(archive)}</div>

<h2 id="method">How this works</h2>
<div class="grid2">
 <div class="panel"><h3>Selection</h3>
 <p>Alerts from Fink's classifier streams ({escape(', '.join(q.get('fink_classes', [])))}) must be brighter than
 mag {_fmt(cuts.get('mag_max'), 1)}, lie within {_fmt(cuts.get('distnr_max'), 1)}″ of a source in the ZTF reference
 image and within {_fmt(cuts.get('distpsnr1_max'), 1)}″ of a galaxy-like Pan-STARRS1 source (star/galaxy score ≤
 {_fmt(cuts.get('sgscore1_max'), 1)}), be detected at least {_fmt(cuts.get('ndethist_min'))} times and be brightening.
 Lasair objects that Sherlock classifies as nuclear transients (within 1″ of the host core) are added too.</p></div>
 <div class="panel"><h3>Consensus</h3>
 <p>Each new candidate is looked up in ALeRCE, Fink and Lasair, and each broker casts <b>one vote</b>: ALeRCE's
 light-curve classifier (or its stamp classifier early on), Fink's classification, and Lasair's Sherlock context. Votes
 are mapped to coarse categories such as SN, AGN or variable star. Sherlock's "nuclear transient" label abstains,
 because every candidate here is nuclear by selection. <b>agree</b>: all votes land in one category.
 <b>disagree</b>: they don't. <b>insufficient</b>: fewer than two brokers voted. Disagreements are often the most
 informative cases. One example is a broker calling the source an artefact ("Bogus") while another calls it a supernova.</p></div>
 {_validation_panel(val)}
</div>

<h2 id="archive">Archive</h2>
<div class="table-wrap"><table><thead><tr><th>date</th><th>new</th><th>disagree</th><th>known</th><th>sources</th><th>data</th></tr></thead>
<tbody>{archive_rows}</tbody></table></div>
{"<h2>Notes from this run</h2><ul class='small'>" + errors + "</ul>" if errors else ""}
<footer class="muted">Data: <a href="https://alerce.online">ALeRCE</a>, <a href="https://fink-portal.org">Fink</a>,
<a href="https://lasair-ztf.lsst.ac.uk">Lasair</a>, ZTF; host images from the
<a href="https://www.legacysurvey.org">DESI Legacy Imaging Surveys</a>. Broker data belongs to the broker teams; please cite
them. Built with <a href="{REPO_URL}">nucleuswatch</a>.</footer>
</main></body></html>
"""


def _card(c: dict) -> str:
    oid = escape(c["object_id"])
    votes = "".join(f"<li><b>{escape(v['broker'])}</b> {escape(v['classifier'])}: {escape(str(v['label']))}"
                    f" → {escape(str(v['category']))}</li>" for v in c.get("votes", []))
    links = [f"<a href='{escape(u)}'>{escape(b)}</a>" for b, u in c.get("links", {}).items()]
    viewer = _legacy_viewer(c.get("ra"), c.get("dec"))
    if viewer:
        links.append(f"<a href='{escape(viewer)}'>sky viewer</a>")
    if c.get("tns_name"):
        name = str(c["tns_name"]).removeprefix("AT ").removeprefix("SN ").strip()
        links.append(f"<a href='https://www.wis-tns.org/object/{escape(name)}'>TNS {escape(str(c['tns_name']))}</a>")
    host = _legacy_survey(c.get("ra"), c.get("dec"))
    host_html = (f"<div class='host'><img src='{escape(host)}' alt='Legacy Survey image around {oid}' loading='lazy' "
                 f"width='160' height='160' onerror=\"this.parentNode.style.display='none'\">"
                 f"<div class='muted small'>host, 42″ wide</div></div>") if host else ""
    imgs = "".join(f"<img src='{escape(src)}' alt='{oid} {escape(kind)}' loading='lazy'>"
                   for kind, src in c.get("_images", {}).items())
    spec = ""
    if c.get("tns_type"):
        spec = (f"<p><b>Spectroscopic (TNS): {escape(str(c['tns_type']))}</b> → "
                f"{escape(str(categorize(c['tns_type'])))}</p>")
    sherlock = f"<p class='muted small'>Sherlock: {escape(c['sherlock'])}</p>" if c.get("sherlock") else ""
    skipped = "".join(f"<li>{escape(b)}: {escape(e)}</li>" for b, e in (c.get("broker_errors") or {}).items())
    skipped = f"<details class='small'><summary>brokers skipped</summary><ul>{skipped}</ul></details>" if skipped else ""
    age = f" · first seen {_fmt(c.get('age_days'), 0)} d ago" if c.get("first_seen_mjd") else ""
    return f"""<div class="card" id="{oid}">
<h3>{oid} {_badge(c.get('consensus'))} <span class="badge tier">{escape(c.get('tier', ''))}</span></h3>
<div class="muted small">mag {_fmt(c.get('mag'))} ({_fmt(c.get('band'))}) · distnr {_fmt(c.get('distnr'))}″ ·
sgscore1 {_fmt(c.get('sgscore1'))} · {c.get('n_detections', 0)} detections{age} · RA {_fmt(c.get('ra'), 5)}
Dec {_fmt(c.get('dec'), 5)} · found via {escape(c.get('sources') or '')}</div>
<div class="small">{' · '.join(links)}</div>
{spec}<ul class="votes">{votes}</ul>{sherlock}{skipped}
<div class="imgs">{host_html}{imgs}</div></div>"""
