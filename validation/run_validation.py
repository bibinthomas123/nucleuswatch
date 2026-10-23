"""Validate the nuclear cuts against spectroscopically classified transients.

    python validation/run_validation.py [--start 2024-01-01] [--stop 2026-01-01]

Ground truth is the TNS spectroscopic classification as cross-matched by
Fink (its "(TNS) <type>" streams). For every class below, all alerts in the
window are fetched (windows are split automatically when Fink's 1000-alert
cap is hit), the default NuclearCuts are applied per alert, and an object
passes if any of its alerts pass, as in the live recipe.

Writes validation/results.json and validation/RESULTS.md.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import asdict, replace
from datetime import date, datetime, timezone
from pathlib import Path

from nucleuswatch import __version__
from nucleuswatch.brokers import fink
from nucleuswatch.recipes import NuclearCuts

HERE = Path(__file__).parent
CAP = 1000
HISTORICAL_TTL = 30 * 86400  # past windows don't change

# TNS type -> group used in the report
CLASSES = {
    "(TNS) TDE": "TDE",
    "(TNS) SN Ia": "SN Ia",
    "(TNS) SN Ia-91T-like": "SN Ia",
    "(TNS) SN II": "SN II",
    "(TNS) SN IIP": "SN II",
    "(TNS) SN IIn": "SN IIn",
    "(TNS) SN IIb": "SN II",
    "(TNS) SN Ib": "SN Ibc",
    "(TNS) SN Ic": "SN Ibc",
    "(TNS) SN Ic-BL": "SN Ibc",
    "(TNS) SLSN-I": "SLSN",
    "(TNS) SLSN-II": "SLSN",
    "(TNS) AGN": "AGN/QSO",
    "(TNS) QSO": "AGN/QSO",
}


def fetch_all(cls: str, start: date, stop: date) -> list[dict]:
    """All alerts of ``cls`` in [start, stop), splitting windows at the cap."""
    rows = fink.latests(cls, start.isoformat(), stop.isoformat(), n=CAP, ttl=HISTORICAL_TTL)
    if len(rows) < CAP or (stop - start).days <= 1:
        if len(rows) >= CAP:
            print(f"  warning: {cls} {start} still capped at {CAP} alerts/day", flush=True)
        return rows
    mid = start + (stop - start) / 2
    return fetch_all(cls, start, mid) + fetch_all(cls, mid, stop)


def months(start: date, stop: date) -> list[date]:
    out, d = [], start
    while d < stop:
        out.append(d)
        d = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    return out + [stop]


def first_failing_cut(alert: dict, cuts: NuclearCuts) -> str | None:
    """Name of the first cut this alert fails (in the order the recipe applies them)."""
    order = [
        ("mag_max", NuclearCuts(mag_max=cuts.mag_max, distnr_max=99, distpsnr1_max=99,
                                sgscore1_max=1, ndethist_min=0)),
        ("distnr", replace(cuts, distpsnr1_max=99, sgscore1_max=1, ndethist_min=0)),
        ("distpsnr1", replace(cuts, sgscore1_max=1, ndethist_min=0)),
        ("sgscore1", replace(cuts, ndethist_min=0)),
        ("ndethist/isdiffpos", cuts),
    ]
    for name, partial in order:
        if not partial.passes(alert):
            return name
    return None


def evaluate(alerts_by_obj: dict[str, list[dict]], cuts: NuclearCuts) -> tuple[set, Counter]:
    passed, lost = set(), Counter()
    for oid, alerts in alerts_by_obj.items():
        if any(cuts.passes(a) for a in alerts):
            passed.add(oid)
        else:
            # Attribute the loss to the cut that removes the object's "best" alert,
            # i.e. the alert that survives the most cuts.
            fails = [first_failing_cut(a, cuts) for a in alerts]
            rank = ["mag_max", "distnr", "distpsnr1", "sgscore1", "ndethist/isdiffpos"]
            lost[max(fails, key=rank.index)] += 1
    return passed, lost


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--render-only", action="store_true", help="re-render RESULTS.md from results.json")
    p.add_argument("--start", default="2024-01-01")
    p.add_argument("--stop", default="2026-01-01")
    args = p.parse_args()
    if args.render_only:
        (HERE / "RESULTS.md").write_text(render(json.loads((HERE / "results.json").read_text())))
        return 0
    start, stop = date.fromisoformat(args.start), date.fromisoformat(args.stop)
    cuts = NuclearCuts()

    by_group: dict[str, dict[str, list[dict]]] = {}
    for cls, group in CLASSES.items():
        rows = []
        for a, b in zip(months(start, stop), months(start, stop)[1:]):
            rows += fetch_all(cls, a, b)
        objs = by_group.setdefault(group, {})
        for r in rows:
            objs.setdefault(r["i:objectId"], []).append(r)
        print(f"{cls:24s} {len(rows):6d} alerts", flush=True)

    # An object can carry only one TNS type; if it shows up twice, keep the first group.
    seen: set[str] = set()
    for group in list(by_group):
        by_group[group] = {o: a for o, a in by_group[group].items() if o not in seen}
        seen |= set(by_group[group])

    tde = by_group["TDE"]
    passed_tde, lost_tde = evaluate(tde, cuts)
    mag_curve = {}
    for m in (18.5, 19.0, 19.5, 20.0, 20.5):
        mag_curve[m] = len(evaluate(tde, replace(cuts, mag_max=m))[0])

    composition = {}
    for group, objs in by_group.items():
        composition[group] = {"total": len(objs), "passed": len(evaluate(objs, cuts)[0])}

    results = {
        "nucleuswatch_version": __version__,
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "window": [args.start, args.stop],
        "cuts": asdict(cuts),
        "tde": {
            "total": len(tde),
            "passed": len(passed_tde),
            "lost_by_cut": dict(lost_tde),
            "recovered_vs_mag_max": mag_curve,
            "passed_ids": sorted(passed_tde),
            "missed_ids": sorted(set(tde) - passed_tde),
        },
        "composition": composition,
    }
    (HERE / "results.json").write_text(json.dumps(results, indent=1))
    (HERE / "RESULTS.md").write_text(render(results))
    print((HERE / "RESULTS.md").read_text())
    return 0


def pct(a: int, b: int) -> str:
    return f"{100 * a / b:.0f}%" if b else "–"


def render(r: dict) -> str:
    t = r["tde"]
    lines = [
        "# Validation of the nuclear cuts",
        "",
        f"Window **{r['window'][0]} → {r['window'][1]}**, ZTF alerts via Fink, ground truth = TNS",
        f"spectroscopic classification. nucleuswatch {r['nucleuswatch_version']}, generated",
        f"{r['generated']} by `validation/run_validation.py`. Default cuts: "
        + ", ".join(f"`{k}={v}`" for k, v in r["cuts"].items()) + ".",
        "",
        "## Recovery of spectroscopically confirmed TDEs",
        "",
        f"**{t['passed']} / {t['total']} ({pct(t['passed'], t['total'])})** confirmed TDEs have at least one",
        "alert passing the default cuts.",
        "",
        "| Removed by | TDEs |",
        "|---|---|",
        *(f"| `{k}` | {v} |" for k, v in sorted(t["lost_by_cut"].items(), key=lambda kv: -kv[1])),
        "",
        "Recovery as a function of the magnitude limit (all other cuts at default):",
        "",
        "| `mag_max` | recovered | fraction |",
        "|---|---|---|",
        *(f"| {m} | {n} | {pct(n, t['total'])} |" for m, n in t["recovered_vs_mag_max"].items()),
        "",
        "## What else passes the cuts",
        "",
        "Spectroscopically classified objects of each type in the same window, and how many pass:",
        "",
        "| TNS type | objects | pass cuts | pass rate | share before cuts | share after cuts |",
        "|---|---|---|---|---|---|",
    ]
    comp = r["composition"]
    total_all = sum(c["total"] for c in comp.values())
    total_passed = sum(c["passed"] for c in comp.values())
    for group, c in sorted(comp.items(), key=lambda kv: -kv[1]["passed"]):
        lines.append(f"| {group} | {c['total']} | {c['passed']} | {pct(c['passed'], c['total'])} | "
                     f"{100 * c['total'] / total_all:.1f}% | {100 * c['passed'] / total_passed:.1f}% |")
    tde = comp.get("TDE", {"total": 0, "passed": 0})
    if tde["total"] and total_passed:
        enrich = (tde["passed"] / total_passed) / (tde["total"] / total_all)
        before, after = 100 * tde["total"] / total_all, 100 * tde["passed"] / total_passed
        summary = f"The cuts raise the TDE share of the classified sample **{enrich:.1f}×**"
        lines += ["", f"{summary} (from {before:.1f}% to {after:.1f}%)."]
    lines += [
        "",
        "## Caveats",
        "",
        "- This measures the **positional/brightness cuts** only. The live recipe additionally",
        "  draws candidates from four Fink classifier streams; that stream-selection step is not",
        "  measured here.",
        "- Ground truth is limited to objects that were spectroscopically classified on TNS and",
        "  cross-matched by Fink, which favours bright, well-observed events.",
        "- Nuclear SNe are genuinely nuclear and are expected to pass; their share is a measure of",
        "  how much non-TDE follow-up a nuclear sample costs, not an error of the cuts.",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
