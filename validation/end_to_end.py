"""End-to-end completeness of the nuclear selection.

    python validation/end_to_end.py [--start 2024-01-01] [--stop 2026-01-01]

Replays the real recipe, `nuclear_transients()` with Fink sources, in weekly
windows over the period, exactly as the weekly digest would have run. The
selected objects are compared with the TNS-classified sample from
run_validation.load_truth(). Unlike RESULTS.md, this includes the
stream-selection step (an object must appear in one of the Fink streams the
recipe reads) and measures detection latency.

Writes validation/end_to_end.json, validation/END_TO_END.md and figures in
validation/figures/.
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import plotstyle
from run_validation import load_truth

from nucleuswatch import __version__
from nucleuswatch.brokers.base import JD_TO_MJD
from nucleuswatch.recipes import NuclearCuts, nuclear_transients

HERE = Path(__file__).parent
SUPERSET_MAG = 20.5  # replay once at the faintest limit; brighter limits are subsets
MAG_LIMITS = (19.0, 19.5, 20.0, 20.5)
NEW_WITHIN = 365


def replay(start: date, stop: date) -> dict[str, dict]:
    """Run the weekly recipe over [start, stop); return per-object selection info."""
    selected: dict[str, dict] = {}
    end = datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc) + timedelta(days=7)
    last = datetime.combine(stop, datetime.min.time(), tzinfo=timezone.utc)
    while end <= last:
        s = nuclear_transients(7, NuclearCuts(mag_max=SUPERSET_MAG), sources=("fink",), merge=False,
                               now=end)
        for r in s.candidates.to_dict("records"):
            oid = r["object_id"]
            cur = selected.setdefault(oid, {"mag": math.inf, "first_pass_mjd": math.inf,
                                            "first_seen_mjd": None, "streams": set()})
            cur["mag"] = min(cur["mag"], r["mag"])
            if r.get("first_pass_mjd") == r.get("first_pass_mjd") and r.get("first_pass_mjd"):
                cur["first_pass_mjd"] = min(cur["first_pass_mjd"], r["first_pass_mjd"])
            if r.get("first_seen_mjd") and r["first_seen_mjd"] == r["first_seen_mjd"]:
                cur["first_seen_mjd"] = min(filter(None, [cur["first_seen_mjd"], r["first_seen_mjd"]]))
            cur["streams"].update((r.get("sources") or "").replace("fink:", "").split("; "))
        print(f"  week ending {end.date()}: {len(s.candidates):3d} candidates, {len(selected):5d} total",
              flush=True)
        end += timedelta(days=7)
    return selected


def truth_table(by_group: dict) -> list[dict]:
    rows = []
    for group, objs in by_group.items():
        for oid, alerts in objs.items():
            pos = [a for a in alerts if a.get("i:isdiffpos") in ("t", "1", 1, True) and a.get("i:magpsf")]
            starts = [a["i:jdstarthist"] for a in alerts if a.get("i:jdstarthist")]
            alert_cuts = any(NuclearCuts(mag_max=SUPERSET_MAG).passes(a) for a in alerts)
            rows.append({
                "object_id": oid,
                "group": group,
                "peak_mag": min((a["i:magpsf"] for a in pos), default=None),
                "first_det_mjd": min(starts) - JD_TO_MJD if starts else None,
                "median_distnr": float(np.median([a["i:distnr"] for a in alerts
                                                  if a.get("i:distnr") not in (None, -999)] or [np.nan])),
                "passes_alert_cuts": alert_cuts,
            })
    return rows


def analyse(truth: list[dict], selected: dict[str, dict]) -> dict:
    out: dict = {"by_group": {}, "tde_latency_days": [], "tde_known_at_selection": 0}
    for group in sorted({t["group"] for t in truth}):
        members = [t for t in truth if t["group"] == group]
        entry = {"total": len(members), "passes_alert_cuts": sum(t["passes_alert_cuts"] for t in members)}
        for m in MAG_LIMITS:
            entry[f"selected@{m}"] = sum(
                1 for t in members if t["object_id"] in selected and selected[t["object_id"]]["mag"] <= m
            )
        out["by_group"][group] = entry

    for t in truth:
        sel = selected.get(t["object_id"])
        if t["group"] != "TDE" or sel is None or sel["mag"] > 20.0:
            continue
        if t["first_det_mjd"] and math.isfinite(sel["first_pass_mjd"]):
            out["tde_latency_days"].append(sel["first_pass_mjd"] - t["first_det_mjd"])
        if sel["first_seen_mjd"] and sel["first_pass_mjd"] - sel["first_seen_mjd"] > NEW_WITHIN:
            out["tde_known_at_selection"] += 1
    return out


def figure_completeness(truth: list[dict], selected: dict[str, dict]) -> None:
    import matplotlib.pyplot as plt

    plotstyle.setup()
    fig, ax = plt.subplots(figsize=(plotstyle.SINGLE, 2.6))
    bins = np.arange(16.0, 21.01, 0.5)
    centres = 0.5 * (bins[1:] + bins[:-1])
    for group, offset in (("TDE", 0.0), ("SN Ia", -0.06), ("SN II", 0.06), ("AGN/QSO", 0.12)):
        members = [t for t in truth if t["group"] == group and t["peak_mag"]]
        k, n = np.zeros(len(centres), int), np.zeros(len(centres), int)
        for t in members:
            i = np.searchsorted(bins, t["peak_mag"]) - 1
            if 0 <= i < len(centres):
                n[i] += 1
                sel = selected.get(t["object_id"])
                k[i] += bool(sel and sel["mag"] <= 20.0)
        good = n >= 3 if group != "TDE" else n >= 1
        stats = [plotstyle.wilson(a, b) for a, b in zip(k, n)]
        p = np.array([s[0] for s in stats])
        lo = np.array([s[1] for s in stats])
        hi = np.array([s[2] for s in stats])
        # Wilson bounds can land a rounding error past p when k = 0 or k = n.
        yerr = np.clip([p[good] - lo[good], hi[good] - p[good]], 0, None)
        ax.errorbar(centres[good] + offset, p[good], yerr=yerr,
                    fmt="o" if group == "TDE" else "s", ms=4 if group == "TDE" else 3,
                    color=plotstyle.COLOURS[group], lw=0.8, capsize=0,
                    mfc=plotstyle.COLOURS[group] if group == "TDE" else "white",
                    label=f"{group} ({len(members)})", zorder=3 if group == "TDE" else 2)
    ax.axvline(20.0, color="0.6", lw=0.6, ls="--")
    ax.set_xlabel("Peak ZTF PSF magnitude")
    ax.set_ylabel("Fraction selected")
    ax.set_ylim(-0.03, 1.03)
    ax.set_xlim(16.0, 21.0)
    ax.legend(loc="lower left", handletextpad=0.3)
    plotstyle.save(fig, "completeness_vs_peak_mag")


def figure_latency(latency: list[float]) -> None:
    import matplotlib.pyplot as plt

    plotstyle.setup()
    fig, ax = plt.subplots(figsize=(plotstyle.SINGLE, 2.3))
    x = np.sort(np.asarray(latency))
    if len(x):
        ax.step(np.concatenate([[0], x]), np.arange(len(x) + 1) / len(x), where="post",
                color=plotstyle.COLOURS["TDE"], lw=1.2)
        med = float(np.median(x))
        ax.axvline(med, color="0.5", lw=0.6, ls="--")
        ax.text(med, 0.05, f"  median {med:.0f} d", fontsize=8, color="0.3")
    ax.set_xlabel("Days from first detection to first selection")
    ax.set_ylabel("Cumulative fraction of TDEs")
    ax.set_xlim(left=0)
    ax.set_ylim(0, 1.02)
    plotstyle.save(fig, "tde_selection_latency")


def render(r: dict) -> str:
    g = r["analysis"]["by_group"]
    t = g["TDE"]
    lat = np.asarray(r["analysis"]["tde_latency_days"])
    pct = lambda a, b: f"{100 * a / b:.0f}%" if b else "–"
    lines = [
        "# End-to-end completeness",
        "",
        (f"The weekly recipe (Fink sources, cuts at `mag_max` ≤ {SUPERSET_MAG}) is replayed from "
        f"**{r['window'][0]} to {r['window'][1]}** ({r['n_weeks']} weekly windows). Truth is the TNS"),
        ("spectroscopic classification as cross-matched by Fink. "
        f"nucleuswatch {r['nucleuswatch_version']}, generated {r['generated']} by `validation/end_to_end.py`."),
        "",
        "## Confirmed TDEs",
        "",
        f"- Pass the alert-level cuts in at least one alert: **{t['passes_alert_cuts']} / {t['total']}**",
        (f"- Selected by the full weekly recipe at mag ≤ 20: **{t['selected@20.0']} / {t['total']} "
        f"({pct(t['selected@20.0'], t['total'])})**. The difference is the stream-selection loss."),
        f"- Median latency from first detection to first selection: **{np.median(lat):.0f} d** "
        f"(16th–84th percentile {np.percentile(lat, 16):.0f}–{np.percentile(lat, 84):.0f} d, N = {len(lat)})"
        if len(lat) else "- Latency: no selected TDEs",
        (f"- Selected TDEs that the digest would file as *known* (first seen > {NEW_WITHIN} d before "
        f"selection): {r['analysis']['tde_known_at_selection']}"),
        "",
        "## Selection by type",
        "",
        "| TNS type | objects | pass alert cuts | " + " | ".join(f"selected ≤ {m}" for m in MAG_LIMITS) + " |",
        "|---|---|---|" + "---|" * len(MAG_LIMITS),
    ]
    for group, e in sorted(g.items(), key=lambda kv: -kv[1]["total"]):
        cells = " | ".join(f"{e[f'selected@{m}']} ({pct(e[f'selected@{m}'], e['total'])})" for m in MAG_LIMITS)
        lines.append(f"| {group} | {e['total']} | {e['passes_alert_cuts']} | {cells} |")
    lines += [
        "",
        "![completeness](figures/completeness_vs_peak_mag.png)",
        "",
        "![latency](figures/tde_selection_latency.png)",
        "",
        "Error bars are 68% Wilson intervals.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="2024-01-01")
    p.add_argument("--stop", default="2026-01-01")
    p.add_argument("--render-only", action="store_true")
    args = p.parse_args()
    if args.render_only:
        (HERE / "END_TO_END.md").write_text(render(json.loads((HERE / "end_to_end.json").read_text())))
        return 0
    start, stop = date.fromisoformat(args.start), date.fromisoformat(args.stop)

    print("loading TNS truth sample ...", flush=True)
    truth = truth_table(load_truth(start, stop, verbose=False))
    print(f"replaying weekly recipe {start} -> {stop} ...", flush=True)
    selected = replay(start, stop)

    analysis = analyse(truth, selected)
    sel_json = {o: {**v, "streams": sorted(v["streams"]),
                    "first_pass_mjd": v["first_pass_mjd"] if math.isfinite(v["first_pass_mjd"]) else None}
                for o, v in selected.items()}
    result = {
        "nucleuswatch_version": __version__,
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "window": [args.start, args.stop],
        "n_weeks": int((stop - start).days // 7),
        "superset_mag": SUPERSET_MAG,
        "analysis": analysis,
        "truth": truth,
        "selected": sel_json,
    }
    # Save before plotting, so a figure problem can't lose an hour-long replay.
    (HERE / "end_to_end.json").write_text(json.dumps(result, indent=1, default=str))
    figure_completeness(truth, selected)
    figure_latency(analysis["tde_latency_days"])
    (HERE / "END_TO_END.md").write_text(render(result))
    print((HERE / "END_TO_END.md").read_text())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
