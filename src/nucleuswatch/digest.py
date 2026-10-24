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
from importlib.resources import files
from pathlib import Path
from urllib.parse import urlencode

import pandas as pd

from . import __version__
from ._http import BrokerError
from .brokers import fink
from .core import MergedObject
from .notify import write_feed
from .recipes import CandidateSearch, NuclearCuts, lsst_nuclear_transients, nuclear_transients
from .score import distance_modulus, tde_scores
from .taxonomy import categorize

REPO_URL = "https://github.com/bibinthomas123/nucleuswatch"
TDE_LIKE = 0.5  # tde_score at or above this is called "TDE-like" on the page
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
    lsst: bool = True,
    volunteers: str | Path | None = None,
) -> tuple[dict, CandidateSearch]:
    """Run the search and return (JSON-able record, search).

    With ``lsst=True`` the Rubin/LSST search runs too, into ``record["lsst"]``.
    ``volunteers`` is a Zooniverse classification export; candidates that
    volunteers have classified get the volunteers' vote in their consensus.
    """
    now = now or datetime.now(timezone.utc)
    search = nuclear_transients(
        days, NuclearCuts(mag_max=mag_max), sources=sources, new_within_days=new_within_days,
        brokers=brokers, now=now, use_cache=use_cache,
    )
    n_volunteer = 0
    if volunteers and search.objects:
        from .recipes import _add_consensus
        from .zooniverse import add_volunteer_votes

        n_volunteer = add_volunteer_votes(search.objects, volunteers)
        if n_volunteer:
            cols = ["consensus", "categories", "broker_labels"]
            search.candidates = _add_consensus(search.candidates.drop(columns=cols, errors="ignore"),
                                               search.objects)
    scores, found = tde_scores(search.objects, use_cache=use_cache) if search.objects else ({}, {})
    candidates = []
    for row in search.candidates.to_dict("records"):
        obj = search.objects.get(row["object_id"])
        rec = {**_clean(row), **(_object_record(obj) if obj else {})}
        rec["tier"] = "bright" if (rec.get("mag") or 99) <= bright_mag else "faint"
        score = scores.get(row["object_id"])
        rec["tde_score"] = round(score, 3) if score is not None else None
        rec["host"] = _host_record(found.get(row["object_id"]), rec.get("mag"))
        candidates.append(rec)
    # Most TDE-like first; objects without a score (no ALeRCE data) go last.
    candidates.sort(key=lambda c: -1 if c["tde_score"] is None else c["tde_score"], reverse=True)
    known = [_clean({k: r.get(k) for k in KNOWN_FIELDS}) for r in search.known.to_dict("records")]
    record = {
        "generated": now.isoformat(timespec="seconds"),  # end of the search window
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),  # when this ran
        "backfill": now < datetime.now(timezone.utc) - timedelta(days=1),
        "nucleuswatch_version": __version__,
        "query": {**search.query, "bright_mag": bright_mag, "brokers": brokers or ["alerce", "fink", "lasair", "antares"]},
        "errors": search.errors,
        "funnel": search.funnel,
        "n_candidates": len(candidates),
        "n_disagree": sum(c.get("consensus") == "disagree" for c in candidates),
        "n_agree": sum(c.get("consensus") == "agree" for c in candidates),
        "n_bright": sum(c["tier"] == "bright" for c in candidates),
        "n_tde_like": sum((c["tde_score"] or 0) >= TDE_LIKE for c in candidates),
        "n_known": len(known),
        "n_volunteer": n_volunteer,
        "candidates": candidates,
        "known": known,
    }
    if lsst:
        record["lsst"] = _lsst_record(days, mag_max, now, use_cache)
    return record, search


def _lsst_record(days: float, mag_max: float, now: datetime, use_cache: bool) -> dict:
    search = lsst_nuclear_transients(days, mag_max, now=now, use_cache=use_cache)
    candidates = []
    for row in search.candidates.to_dict("records"):
        obj = search.objects.get(row["object_id"])
        candidates.append({**_clean(row), **(_object_record(obj) if obj else {})})
    return {"query": search.query, "funnel": search.funnel, "errors": search.errors, "candidates": candidates}


def mark_returning(record: dict, data_dir: str | Path) -> dict:
    """Tag each candidate with the week it was first listed, from earlier records.

    Sets ``first_listed`` (a date) and ``returning`` on every candidate, and
    ``n_new_this_week`` on the record. Most weekly candidates are still
    active from earlier weeks; this lets the page, the feed and the chat
    alerts point out the ones that are new.
    """
    stamp = record["generated"][:10]
    first: dict[str, str] = {}
    for path in sorted(Path(data_dir).glob("20*.json")):
        if path.stem >= stamp:
            continue
        try:
            earlier = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        for c in earlier.get("candidates", []):
            first.setdefault(c["object_id"], path.stem)
    for c in record["candidates"]:
        c["first_listed"] = first.get(c["object_id"], stamp)
        c["returning"] = c["object_id"] in first
    record["n_new_this_week"] = sum(not c["returning"] for c in record["candidates"])
    return record


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

    sky = files("nucleuswatch").joinpath("assets", SKY_IMAGE)
    if sky.is_file():
        (site / "img" / SKY_IMAGE).write_bytes(sky.read_bytes())
    (site / "index.html").write_text(_page(record, archive, val))
    write_feed(data_dir, site)
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


def _host_record(host: dict | None, mag: float | None) -> dict | None:
    """The Legacy Surveys host, and the transient's absolute magnitude if there's a redshift."""
    if not host:
        return None
    out = {k: host.get(k) for k in ("sep", "type", "shape_r", "mag_g", "mag_r", "mag_z", "z", "z_err", "z_is_spec")}
    if host.get("z") and mag is not None:
        out["abs_mag"] = round(mag - distance_modulus(host["z"]), 2)
    return out


def _num(v):
    return None if v is None or pd.isna(v) else float(v)


# Plots on the page match its typography: sans-serif text, quiet axes.
_PLOT_STYLE = {
    "font.family": "sans-serif", "font.sans-serif": ["Inter", "Helvetica Neue", "Arial", "DejaVu Sans"],
    "font.size": 9.5, "axes.edgecolor": "#55514a", "axes.linewidth": 0.7,
    "xtick.color": "#55514a", "ytick.color": "#55514a", "xtick.direction": "in", "ytick.direction": "in",
    "axes.titlesize": 9.5, "legend.frameon": False,
}


def _write_images(obj: MergedObject, img_dir: Path, *, use_cache: bool) -> dict:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from .zooniverse import draw_stamps

    images = {}
    with plt.rc_context(_PLOT_STYLE):
        ax = obj.plot()
        ax.figure.set_size_inches(7.5, 4)
        ax.figure.tight_layout()
        name = f"{obj.object_id}_lc.png"
        ax.figure.savefig(img_dir / name, dpi=150)
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
                fig.savefig(img_dir / name, dpi=150)
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

_FONTS = ("https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500"
          "&family=Inter:wght@400;500;600&display=swap")

_CSS = """
:root {
  --bg:#f7f5ef; --fg:#1c1a16; --muted:#6a645a; --line:#ddd6c8; --rule:#1c1a16; --link:#1d5872;
  --bar:#3d4f5c; --agree:#3b6e3f; --disagree:#b4501f; --insufficient:#9b7a1b; --known:#a39d91; --plate:#ffffff;
  --sans:"Inter", -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  --mono:"IBM Plex Mono", ui-monospace, "SF Mono", Menlo, Consolas, monospace;
}
@media (prefers-color-scheme: dark) { :root {
  --bg:#10141c; --fg:#e6e1d6; --muted:#9a958a; --line:#2a313d; --rule:#e6e1d6; --link:#8ec3d6;
  --bar:#8ea4b3; --agree:#84b98a; --disagree:#e3895a; --insufficient:#d6b65a; --known:#59606b; --plate:#f2efe8; } }
* { box-sizing:border-box; }
html { -webkit-text-size-adjust:100%; }
body { margin:0; background:var(--bg); color:var(--fg); font:16px/1.6 var(--sans); }
main { max-width:1080px; margin:0 auto; padding:40px 20px 80px; }
a { color:var(--link); text-decoration:underline; text-decoration-thickness:1px; text-underline-offset:2px; }
a:hover { text-decoration-thickness:2px; }
.masthead { border-bottom:1px solid var(--line); padding:0 0 18px; }
.kicker { font:500 12px/1.4 var(--mono); letter-spacing:.06em; text-transform:uppercase; color:var(--muted); }
h1 { font-size:32px; line-height:1.15; font-weight:600; letter-spacing:-.01em; margin:6px 0 8px; }
.dek { font-size:17px; line-height:1.55; margin:0 0 10px; max-width:48em; }
.meta { font:13px/1.5 var(--mono); color:var(--muted); }
nav { display:flex; gap:22px; flex-wrap:wrap; padding:10px 0; border-bottom:1px solid var(--line);
      font:500 14px/1.4 var(--sans); }
nav a { color:var(--fg); text-decoration:none; } nav a:hover { color:var(--link); }
h2 { font-size:22px; font-weight:600; margin:52px 0 14px; padding-top:12px; border-top:1px solid var(--line);
     display:flex; gap:12px; align-items:baseline; }
h2 .num { font:500 13px var(--mono); color:var(--muted); }
h3 { font-size:15px; font-weight:600; margin:0 0 8px; }
p { margin:0 0 12px; }
.muted { color:var(--muted); } .small { font-size:14px; line-height:1.5; }
.mono, td.num, .id { font-family:var(--mono); font-feature-settings:"tnum" 1; }
.figures { display:grid; grid-template-columns:repeat(auto-fit, minmax(150px, 1fr)); margin:26px 0 8px; }
.figures div { padding:4px 18px 8px; border-left:1px solid var(--line); }
.figures div:first-child { border-left:0; padding-left:0; }
.figures dd { margin:0; font-size:36px; line-height:1.15; font-weight:600; font-feature-settings:"tnum" 1; }
.figures dt { font-size:14px; color:var(--muted); line-height:1.35; }
.cols { display:grid; grid-template-columns:repeat(auto-fit, minmax(290px, 1fr)); gap:28px; }
.col { border-top:1px solid var(--line); padding-top:12px; }
.bars div { display:grid; grid-template-columns:10.5em 1fr 3.2em; gap:10px; align-items:center; margin:5px 0;
            font-size:14px; }
.bars span.b { height:6px; background:var(--bar); display:block; }
.bars span.n { text-align:right; font:13px var(--mono); }
.table-wrap { overflow-x:auto; border-top:1px solid var(--line); border-bottom:1px solid var(--line); }
table { border-collapse:collapse; width:100%; font-size:14.5px; }
th, td { padding:7px 12px 7px 0; text-align:left; white-space:nowrap; border-bottom:1px solid var(--line); }
tbody tr:last-child td { border-bottom:0; }
th { font:500 11.5px/1.3 var(--mono); text-transform:uppercase; letter-spacing:.07em; color:var(--muted); }
td.num, th.num { text-align:right; }
td a.id { text-decoration:none; }
.status { white-space:nowrap; font-size:14px; }
.status::before { content:""; display:inline-block; width:8px; height:8px; border-radius:50%; margin-right:6px;
                  background:currentColor; vertical-align:1px; }
.agree { color:var(--agree); } .disagree { color:var(--disagree); } .insufficient { color:var(--insufficient); }
.fresh { font:600 11px var(--mono); color:var(--plate); background:var(--disagree); border-radius:3px;
  padding:1px 5px; margin-left:6px; text-transform:uppercase; letter-spacing:.05em; vertical-align:1px; }
.obs-bar { margin:8px 0 10px; font-size:14px; display:flex; flex-wrap:wrap; gap:8px; align-items:center; }
.obs-bar select { font:inherit; color:var(--fg); background:var(--bg); border:1px solid var(--line); border-radius:4px;
  padding:3px 6px; max-width:100%; }
.obs-up { color:var(--agree); } .obs-low { color:var(--muted); }
.tier { font:12px var(--mono); color:var(--muted); text-transform:uppercase; letter-spacing:.06em; }
.entry { border-top:1px solid var(--line); padding:22px 0 8px; }
.entry h3 { font:600 18px var(--mono); display:flex; gap:14px;
            align-items:baseline; flex-wrap:wrap; margin-bottom:4px; }
.entry .facts { font:13px/1.6 var(--mono); color:var(--muted); }
.entry .links { font-size:14px; margin:4px 0 8px; }
.entry .spec { font-weight:500; }
ul.votes { margin:6px 0 10px; padding-left:20px; font-size:15px; }
.imgs { display:grid; grid-template-columns:160px minmax(0,1.5fr) minmax(0,1fr); gap:14px; align-items:start; margin-top:12px; }
.imgs img { max-width:100%; height:auto; display:block; background:var(--plate); padding:4px; }
.host img { width:160px; height:160px; background:#000; padding:0; }
/* fixed columns, so a host image that fails to load leaves a gap instead of reflowing */
.imgs .host { grid-column:1; } .imgs img.lightcurve { grid-column:2; } .imgs img.stamps { grid-column:3; }
.host .small { margin-top:4px; font:12px var(--mono); }
@media (max-width:860px) {
  .imgs { grid-template-columns:1fr; } .imgs .host, .imgs img.lightcurve, .imgs img.stamps { grid-column:1; }
  h1 { font-size:36px; } .dek { font-size:18px; } }
figure { margin:0; }
figcaption { font-size:14px; color:var(--muted); margin-top:6px; }
svg text { fill:var(--muted); font:11px var(--mono); }
svg .grid { stroke:var(--line); stroke-width:1; fill:none; }
details { margin:6px 0; font-size:14px; } summary { cursor:pointer; color:var(--link); }
footer { margin-top:64px; padding-top:12px; border-top:1px solid var(--line); font-size:14px; color:var(--muted); }
code { font:14px var(--mono); }
"""


def _fresh(c: dict) -> str:
    """A "new" tag for candidates first listed this week (records before 1.2 have no tag)."""
    return "<span class='fresh'>new</span>" if c.get("returning") is False else ""


def _badge(status: str | None) -> str:
    status = status or "insufficient"
    return f'<span class="status {escape(status)}">{escape(status)}</span>'


def _fmt(v, digits=2) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "–"
    return f"{v:.{digits}f}" if isinstance(v, float) else escape(str(v))


def _bars(counts: dict, colour_classes: dict | None = None, *, keep_order: bool = False,
          log: bool = False) -> str:
    """Horizontal bar list. ``log=True`` for quantities spanning decades (a funnel)."""
    if not counts:
        return "<p class='muted small'>none</p>"
    scale = (lambda n: math.log10(n + 1)) if log else (lambda n: n)
    top = scale(max(counts.values())) or 1
    rows = []
    items = counts.items() if keep_order else sorted(counts.items(), key=lambda kv: -kv[1])
    for label, n in items:
        cls = (colour_classes or {}).get(label, "")
        style = "background:currentColor" if cls else ""
        width = max(100 * scale(n) / top, 1.5 if n else 0)
        rows.append(f"<div class='{escape(cls)}'><span>{escape(str(label))}</span>"
                    f"<span class='b' style='width:{width:.0f}%;{style}'></span><span class='n'>{n}</span></div>")
    return "<div class='bars'>" + "".join(rows) + "</div>"


SKY_IMAGE = "sky_equatorial.jpg"
SKY_CREDIT = ("Background: Gaia EDR3 all-sky map, ESA/Gaia/DPAC (CC BY-SA 3.0 IGO), "
              "re-projected to equatorial coordinates.")
# Brighter status colours for points drawn on the dark sky image (both themes).
def _obs_js() -> str:
    return files("nucleuswatch").joinpath("assets", "observability.js").read_text()


_SKY_COLOURS = {"agree": "#7ddc8c", "disagree": "#ff8f52", "insufficient": "#ffd166"}


def _sky_map(cands: list[dict], known: list[dict], *, background: bool = True) -> str:
    """Equirectangular RA/Dec map over the Gaia sky; RA increases to the left.

    The plot area is exactly 2:1 so the re-projected all-sky image isn't stretched.
    """
    w, pad = 720, 30
    h = pad * 2 + (w - 2 * pad) // 2
    x = lambda ra: pad + (360 - ra) / 360 * (w - 2 * pad)
    y = lambda dec: pad + (90 - dec) / 180 * (h - 2 * pad)
    parts = [f'<svg viewBox="0 0 {w} {h}" width="100%" role="img" aria-label="Sky positions of candidates">']
    if background:
        parts.append(f'<image href="img/{SKY_IMAGE}" x="{pad}" y="{pad}" width="{w - 2 * pad}" '
                     f'height="{h - 2 * pad}" preserveAspectRatio="none"/>')
    grid = 'stroke="#ffffff" stroke-opacity=".18" stroke-width="1" fill="none"' if background else 'class="grid"'
    parts.append(f'<rect {grid} x="{pad}" y="{pad}" width="{w - 2 * pad}" height="{h - 2 * pad}"/>')
    for ra in range(0, 361, 60):
        parts.append(f'<line {grid} x1="{x(ra):.1f}" y1="{pad}" x2="{x(ra):.1f}" y2="{h - pad}"/>'
                     f'<text x="{x(ra):.1f}" y="{h - pad + 14}" text-anchor="middle">{ra}°</text>')
    for dec in range(-60, 91, 30):
        parts.append(f'<line {grid} x1="{pad}" y1="{y(dec):.1f}" x2="{w - pad}" y2="{y(dec):.1f}"/>'
                     f'<text x="{pad - 4}" y="{y(dec) + 4:.1f}" text-anchor="end">{dec}°</text>')
    known_fill = '#e8edf5" opacity=".55' if background else 'var(--known)" opacity=".6'
    for k in known:
        if k.get("ra") is not None and k.get("dec") is not None:
            parts.append(f'<circle cx="{x(k["ra"]):.1f}" cy="{y(k["dec"]):.1f}" r="1.8" fill="{known_fill}">'
                         f'<title>{escape(k["object_id"])} (known)</title></circle>')
    for c in cands:
        if c.get("ra") is not None and c.get("dec") is not None:
            status = c.get("consensus") or "insufficient"
            fill = _SKY_COLOURS.get(status, _SKY_COLOURS["insufficient"]) if background else f"var(--{status})"
            stroke = "#0b0f17" if background else "var(--bg)"
            parts.append(f'<a href="#{escape(c["object_id"])}"><circle cx="{x(c["ra"]):.1f}" cy="{y(c["dec"]):.1f}" r="5" '
                         f'fill="{fill}" stroke="{stroke}" stroke-width="1.5"><title>{escape(c["object_id"])} '
                         f'({escape(status)})</title></circle></a>')
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
    return f"""<div class="col"><h3>How good are the cuts?</h3>
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
        f"<tr><td><a class='id' href='#{escape(c['object_id'])}'>{escape(c['object_id'])}</a>{_fresh(c)}</td>"
        f"<td class='num'>{_fmt(c.get('mag'))}</td><td>{_fmt(c.get('band'))}</td>"
        f"<td class='num'>{_fmt(c.get('tde_score'), 2)}</td>"
        f"<td class='num obs' data-obs data-ra='{_fmt(c.get('ra'), 5)}' data-dec='{_fmt(c.get('dec'), 5)}'>–</td>"
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
        f"<tr><td><a class='id' href='https://lasair-ztf.lsst.ac.uk/objects/{escape(k['object_id'])}/'>{escape(k['object_id'])}</a></td>"
        f"<td class='num'>{_fmt(k.get('mag'))}</td><td>{_fmt(k.get('band'))}</td>"
        f"<td class='num'>{_fmt(k.get('ra'), 4)}</td><td class='num'>{_fmt(k.get('dec'), 4)}</td>"
        f"<td class='small'>{escape(k.get('sources') or '')}</td></tr>"
        for k in known[:100]
    )
    stamp = record["generated"][:10]
    known_more = (f"<p class='muted small'>Showing the 100 brightest of {len(known)}. "
                  f"<a href='data/{escape(stamp)}_known.csv'>Download all (CSV)</a>.</p>") if len(known) > 100 else ""

    archive_rows = "".join(
        f"<tr><td class='mono'>{escape(a['date'])}</td><td class='num'>{a['n']}</td><td class='num'>{a['disagree']}</td>"
        f"<td class='num'>{_fmt(a.get('known'))}</td><td class='small'>{escape(', '.join(a['sources']))}</td>"
        f"<td><a href='data/{escape(a['date'])}.json'>json</a> · <a href='data/{escape(a['date'])}.csv'>csv</a></td></tr>"
        for a in archive
    )
    cuts = q.get("cuts", {})
    errors = "".join(f"<li>{escape(e)}</li>" for e in record.get("errors", []))
    empty = "<p class='muted'>No new candidates passed the cuts in this window.</p>" if not cands else ""

    gen = datetime.fromisoformat(record["generated"])
    iso_year, iso_week, _ = gen.isocalendar()
    sources = ", ".join(q.get("sources", [])) or "fink"

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Nucleus Watch — week {iso_week}, {iso_year}</title>
<meta name="description" content="Nuclear transients in the ZTF alert stream, with ALeRCE, Fink and Lasair classifications compared.">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="{_FONTS}">
<link rel="alternate" type="application/atom+xml" title="Nucleus Watch: TDE-like candidates" href="feed.xml">
<style>{_CSS}</style></head>
<body><main>
<header class="masthead">
 <div class="kicker">Week {iso_week}, {iso_year}</div>
 <h1>Nucleus Watch</h1>
 <p class="dek">New transients at the centres of galaxies in the ZTF alert stream (tidal disruption events,
 AGN flares, nuclear supernovae), and whether the ALeRCE, Fink and Lasair brokers agree on what they are.</p>
 <div class="meta">{escape(q.get('startdate', ''))} – {escape(q.get('stopdate', ''))} · sources: {escape(sources)} ·
 mag ≤ {_fmt(cuts.get('mag_max'), 1)} · nucleuswatch {escape(record['nucleuswatch_version'])}</div>
</header>
<nav><a href="#candidates">Candidates</a><a href="#details">Entries</a><a href="#rubin">Rubin</a><a href="#known">Known sources</a>
<a href="#history">History</a><a href="#method">Method</a><a href="#archive">Archive</a><a href="feed.xml">Feed</a>
<a href="{REPO_URL}">Source code</a></nav>

<dl class="figures">
 <div><dd>{record['n_candidates']}</dd><dt>new nuclear candidates</dt></div>
 <div><dd>{record.get('n_bright', 0)}</dd><dt>brighter than mag {_fmt(q.get('bright_mag'), 0)}</dt></div>
 <div><dd>{record.get('n_new_this_week', record['n_candidates'])}</dd><dt>first listed this week</dt></div>
 <div><dd>{record.get('n_tde_like', 0)}</dd><dt>TDE score ≥ {TDE_LIKE}</dt></div>
 <div><dd class="disagree">{record['n_disagree']}</dd><dt>brokers disagree</dt></div>
 <div><dd class="agree">{record.get('n_agree', 0)}</dd><dt>brokers agree</dt></div>
 <div><dd>{len(tns_typed)}</dd><dt>with a spectroscopic type</dt></div>
 <div><dd>{record.get('n_known', 0)}</dd><dt>known nuclear sources active</dt></div>
</dl>

<h2><span class="num">01</span>The week in numbers</h2>
<div class="cols">
 <div class="col"><h3>Selection funnel</h3>{_bars({
     "alerts scanned": funnel.get("alerts_scanned", 0),
     "objects scanned": funnel.get("objects_scanned", 0),
     "passed cuts": funnel.get("passed_cuts", 0),
     "known sources": funnel.get("known", 0),
     "new candidates": funnel.get("new", 0)}, keep_order=True, log=True)}
  <p class="muted small">Logarithmic bars. Alerts come from Fink's streams; objects also include Lasair's
  list of Sherlock "nuclear transients".</p></div>
 <div class="col"><h3>Consensus</h3>{_bars(dict(consensus_counts), {"agree": "agree", "disagree": "disagree", "insufficient": "insufficient"})}
  <h3 style="margin-top:16px">Broker categories</h3>{_bars(dict(category_counts))}</div>
 <div class="col"><h3>Where candidates came from</h3>{_bars(dict(source_counts))}
  <h3 style="margin-top:16px">Fink streams</h3>{_bars(dict(stream_counts))}</div>
</div>

<h2><span class="num">02</span>Sky positions</h2>
<figure>{_sky_map(cands, known)}
<figcaption>Large points are new candidates, coloured by consensus (green agree, orange disagree, yellow
insufficient). Small white points are known nuclear sources active in the same window. RA increases to the left.
The bright band is the Milky Way, where dust and crowded star fields keep extragalactic searches away. Select a
point to jump to its entry. {escape(SKY_CREDIT)}</figcaption></figure>

<h2 id="candidates"><span class="num">03</span>New candidates</h2>{empty}
<div class="obs-bar"><label for="obs-site">Observability tonight from</label> <select id="obs-site"></select>
<span id="obs-note" class="muted small"></span></div>
<div class="table-wrap"><table>
<thead><tr><th>object</th><th class="num">mag</th><th>band</th><th class="num">TDE score</th>
<th class="num" title="Highest altitude while dark tonight · hours above 30°">tonight</th><th>tier</th><th class="num">age (d)</th><th>consensus</th>
<th>categories</th><th>TNS</th><th>TNS type</th><th>found via</th></tr></thead><tbody>{rows}</tbody></table></div>
<p class="muted small">Sorted by TDE score, a ranking from a small model fitted on spectroscopically classified
nuclear transients (<a href="#method">method</a>). <span class='fresh'>new</span> marks objects first listed
this week; the rest were already on an earlier week's list and are still active. "Tonight" is the highest altitude while the Sun is
below −12° and the hours above 30°, for the site you pick, computed in your browser; hover for the Moon's
distance. Age is the number of days since first detection, where Fink reports it. Bright means
mag ≤ {_fmt(q.get('bright_mag'), 0)}; faint goes down to {_fmt(cuts.get('mag_max'), 0)}.
<a href="data/{escape(stamp)}.csv">Download the table (CSV)</a>.</p>

<h2 id="details"><span class="num">04</span>Entries</h2>{cards}

<h2 id="rubin"><span class="num">05</span>Rubin/LSST</h2>
{_lsst_section(record.get("lsst"))}

<h2 id="known"><span class="num">06</span>Known nuclear sources active this window</h2>
<p>These objects pass the nuclear cuts, were first detected more than {_fmt(q.get('new_within_days'), 0)}
days ago, and their nucleus was already producing alerts in the two years before the current flare began. Most
are AGN, which vary all the time. A source that was quiet for years and has just started flaring is listed under
new candidates instead, even if its ZTF name is old. There are too many known sources to compare across brokers
every week, so they're listed without consensus.</p>
<div class="table-wrap"><table><thead><tr><th>object</th><th class="num">mag</th><th>band</th><th class="num">RA</th>
<th class="num">Dec</th><th>found via</th></tr></thead>
<tbody>{known_rows}</tbody></table></div>{known_more}

<h2 id="history"><span class="num">07</span>History</h2>
<figure>{_history_chart(archive)}</figure>

<h2 id="method"><span class="num">08</span>Method</h2>
<div class="cols">
 <div class="col"><h3>Selection</h3>
 <p>Alerts from Fink's classifier streams ({escape(', '.join(q.get('fink_classes', [])))}) must be brighter than
 mag {_fmt(cuts.get('mag_max'), 1)}, lie within {_fmt(cuts.get('distnr_max'), 1)}″ of a source in the ZTF reference
 image and within {_fmt(cuts.get('distpsnr1_max'), 1)}″ of a galaxy-like Pan-STARRS1 source (star/galaxy score ≤
 {_fmt(cuts.get('sgscore1_max'), 1)}), have at least {_fmt(cuts.get('ndethist_min'))} detections, and be brightening.
 Lasair objects that Sherlock places within 1″ of a galaxy core are added as well.</p></div>
 <div class="col"><h3>Consensus</h3>
 <p>Each new candidate is looked up in ALeRCE, Fink and Lasair, and each broker casts one vote: ALeRCE's
 forced-photometry classifier (the only one with a TDE class; its alert light-curve or stamp classifier until
 forced photometry exists), Fink's classification, and Lasair's Sherlock context.
 Votes are mapped to coarse categories such as SN, AGN or variable star. Sherlock's "nuclear transient" label
 abstains, because every candidate is nuclear by selection. The brokers <i>agree</i> when all votes land in one
 category, <i>disagree</i> when they don't, and the result is <i>insufficient</i> with fewer than two votes. The
 disagreements tend to be the interesting cases, such as one broker calling a source an artefact while another
 calls it a supernova.</p></div>
 <div class="col"><h3>TDE score</h3>
 <p>Candidates are ranked by a logistic model over the light curve so far (colour, colour change, rise, fade)
 and the host galaxy from the Legacy Surveys (colour, size, and the flare's absolute magnitude from the host's
 redshift), fitted on {_score_n()} spectroscopically classified nuclear transients from 2024–2025. Colour carries
 most of the weight: TDEs stay hot and blue, while supernovae redden as they cool. Tested on light curves as they
 looked when the search first picked each object, the top tenth of the ranked list held 56% of the TDEs (32% of
 that tenth were TDEs, against a 6% base rate), rising to three quarters two months later. Broker labels aren't
 inputs. It ranks; it doesn't classify. <a href="{REPO_URL}/blob/main/validation/TDE_SCORE.md">Details</a>.</p></div>
 {_validation_panel(val)}
</div>

<h2 id="archive"><span class="num">09</span>Archive</h2>
<div class="table-wrap"><table><thead><tr><th>week ending</th><th class="num">new</th><th class="num">disagree</th>
<th class="num">known</th><th>sources</th><th>data</th></tr></thead>
<tbody>{archive_rows}</tbody></table></div>
{"<h2><span class='num'>—</span>Notes from this run</h2><ul class='small'>" + errors + "</ul>" if errors else ""}
<footer>Data from <a href="https://alerce.online">ALeRCE</a>, <a href="https://fink-portal.org">Fink</a>,
<a href="https://lasair-ztf.lsst.ac.uk">Lasair</a> and the Zwicky Transient Facility. Host images from the
<a href="https://www.legacysurvey.org">DESI Legacy Imaging Surveys</a>. Sky map background from
<a href="https://www.esa.int/ESA_Multimedia/Images/2020/12/The_colour_of_the_sky_from_Gaia_s_Early_Data_Release_3">ESA/Gaia/DPAC</a>
(<a href="https://creativecommons.org/licenses/by-sa/3.0/igo/">CC BY-SA 3.0 IGO</a>). The broker data belongs to the broker
teams; please cite them if you use it. Compiled with <a href="{REPO_URL}">nucleuswatch</a>.</footer>
</main><script>{_obs_js()}</script></body></html>
"""


def _lsst_section(lsst: dict | None) -> str:
    if not lsst:
        return "<p class='muted'>The Rubin search didn't run for this week.</p>"
    q, f = lsst["query"], lsst["funnel"]
    intro = (f"<p>The same search for the Rubin alert stream: alerts in Fink's "
             f"<code>{escape(q.get('tag', ''))}</code> stream brighter than mag {_fmt(q.get('mag_max'), 0)}, "
             f"on a galaxy nucleus. Rubin alerts don't carry host information yet, so the nearest source in the "
             f"Legacy Surveys DR10 catalogue must be a galaxy within {_fmt(q.get('max_offset'), 1)}″. Votes come "
             f"from ALeRCE's Rubin stamp classifier and Fink's CATS classes.</p>")
    errors = "".join(f"<li>{escape(e)}</li>" for e in lsst.get("errors", []))
    errors = f"<ul class='small muted'>{errors}</ul>" if errors else ""
    if not f.get("alerts_scanned"):
        last = q.get("latest_alert_mjd")
        when = ""
        if last:
            date = datetime(1858, 11, 17, tzinfo=timezone.utc) + timedelta(days=last)
            when = f" The most recent alert in that stream is from {date:%d %B %Y}."
        return intro + f"<p><b>No Rubin alerts reached the brokers in this window.</b>{when}</p>" + errors
    rows = "".join(
        f"<tr><td><a class='id' href='{escape(c.get('links', {}).get('fink', '#'))}'>{escape(c['object_id'])}</a></td>"
        f"<td class='num'>{_fmt(c.get('mag'))}</td><td>{_fmt(c.get('band'))}</td>"
        f"<td class='num'>{_fmt(c.get('host_sep'))}</td><td>{_fmt(c.get('host_type'))}</td>"
        f"<td>{_badge(c.get('consensus'))}</td><td>{_fmt(c.get('categories'))}</td></tr>"
        for c in lsst["candidates"])
    summary = (f"<p class='muted small'>{f['alerts_scanned']} alerts from {f['objects_scanned']} objects; "
               f"{f.get('bright', 0)} bright enough; {f['passed_cuts']} on a galaxy nucleus.</p>")
    if not rows:
        return intro + summary + errors
    return intro + f"""<div class="table-wrap"><table><thead><tr><th>diaObjectId</th><th class="num">mag</th>
<th>band</th><th class="num">host offset (″)</th><th>host model</th><th>consensus</th><th>categories</th></tr></thead>
<tbody>{rows}</tbody></table></div>""" + summary + errors


def _score_n() -> str:
    from .score import load_model
    return str(load_model().get("n_train", ""))


def _card(c: dict) -> str:
    oid = escape(c["object_id"])
    votes = "".join(f"<li><b>{escape(v['broker'])}</b> <span class='muted'>{escape(v['classifier'])}</span>"
                    f" {escape(str(v['label']))} → {escape(str(v['category']))}</li>" for v in c.get("votes", []))
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
                 f"<div class='muted small'>host galaxy, 42″</div></div>") if host else ""
    imgs = "".join(f"<img class='{escape(kind)}' src='{escape(src)}' alt='{oid} {escape(kind)}' loading='lazy'>"
                   for kind, src in c.get("_images", {}).items())
    spec = ""
    if c.get("tns_type"):
        spec = (f"<p class='spec'>Spectroscopically classified as {escape(str(c['tns_type']))} on TNS"
                f" ({escape(str(categorize(c['tns_type'])))}).</p>")
    sherlock = f"<p class='muted small'>Sherlock: {escape(c['sherlock'])}</p>" if c.get("sherlock") else ""
    skipped = "".join(f"<li>{escape(b)}: {escape(e)}</li>" for b, e in (c.get("broker_errors") or {}).items())
    skipped = f"<details class='small'><summary>brokers skipped</summary><ul>{skipped}</ul></details>" if skipped else ""
    age = f" · first seen {_fmt(c.get('age_days'), 0)} d ago" if c.get("first_seen_mjd") else ""
    listed = f" · on this list since {escape(c['first_listed'])}" if c.get("returning") else ""
    h = c.get("host") or {}
    host_fact = ""
    if h.get("z"):
        kind = "spectroscopic" if h.get("z_is_spec") else f"photometric ±{_fmt(h.get('z_err'))}"
        host_fact = (f"<br>Host z = {_fmt(h['z'], 3)} ({kind}) · peak M ≈ {_fmt(h.get('abs_mag'), 1)} · "
                     f"host g−r {_fmt((h['mag_g'] - h['mag_r']) if h.get('mag_g') and h.get('mag_r') else None)}")
    score = (f" <span class='tier'>TDE score {_fmt(c['tde_score'], 2)}</span>"
             if c.get("tde_score") is not None else "")
    return f"""<article class="entry" id="{oid}">
<h3>{oid}{_fresh(c)} {_badge(c.get('consensus'))} <span class="tier">{escape(c.get('tier', ''))}</span>{score}</h3>
<div class="facts">{_fmt(c.get('mag'))} mag ({_fmt(c.get('band'))}) · distnr {_fmt(c.get('distnr'))}″ ·
sgscore1 {_fmt(c.get('sgscore1'))} · {c.get('n_detections', 0)} detections{age}{listed}<br>
RA {_fmt(c.get('ra'), 5)} · Dec {_fmt(c.get('dec'), 5)} · via {escape(c.get('sources') or '')}{host_fact}</div>
<div class="links">{' · '.join(links)}</div>
{spec}<ul class="votes">{votes}</ul>{sherlock}{skipped}
<div class="imgs">{host_html}{imgs}</div></article>"""
