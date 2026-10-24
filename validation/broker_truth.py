"""How do broker classifications compare with spectroscopic truth?

    python validation/broker_truth.py [--no-lasair] [--start ...] [--stop ...]

Sample: TNS-classified objects (via Fink's "(TNS) ..." streams) with at least
one alert passing the nuclear cuts at mag <= 20. All TDEs and AGN are kept.
SNe Ia and SNe II are randomly subsampled (fixed seed), because they would
otherwise dominate the run time.

Votes are made leakage-free, because a classifier that has already read the
TNS answer can't be scored against it:

- ALeRCE: the vote nucleuswatch uses (forced-photometry classifier, else
  lc_classifier, else stamp_classifier).
  ALeRCE's classifiers don't use TNS.
- Fink: the served `v:classification` copies the TNS type once one exists.
  It is reported for reference, but scored separately ("Fink, science
  modules"), using only alert-time outputs: the SIMBAD crossmatch made when
  the alert was processed (`d:cdsxmatch`) and SuperNNova (`d:snn_sn_vs_all`).
- ANTARES: votes AGN when the locus matches a quasar/AGN catalogue
  (Milliquas, Véron-Cetty), otherwise abstains. Catalogue matches don't use TNS.
- Lasair: Sherlock uses host context only. Its API allows 100 calls per hour,
  so results are cached in broker_truth_lasair.json and the run resumes
  where it stopped. Lasair is queried for all TDEs and AGN and a subsample
  of SNe.

Writes validation/broker_truth.json, validation/BROKER_TRUTH.md and figures.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import plotstyle
from run_validation import load_truth

import nucleuswatch as nw
from nucleuswatch import __version__
from nucleuswatch.brokers import antares, lasair
from nucleuswatch.recipes import NuclearCuts
from nucleuswatch.taxonomy import categorize

HERE = Path(__file__).parent
LASAIR_CACHE = HERE / "broker_truth_lasair.json"
TRUTH = {"TDE": "TDE", "AGN/QSO": "AGN"}  # every SN subtype -> "SN"
TRUTH_ORDER = ["TDE", "SN", "AGN"]
PRED_ORDER = ["TDE", "SN", "AGN", "other", "no vote"]
SUBSAMPLE = {"SN Ia": 150, "SN II": 100}
LASAIR_SN = 40
SEED = 20261024


def truth_class(group: str) -> str:
    return TRUTH.get(group, "SN")


def predicted(category: str | None) -> str:
    if category is None:
        return "no vote"
    return category if category in ("TDE", "SN", "AGN") else "other"


def build_sample(start: date, stop: date) -> tuple[list[dict], dict[str, int]]:
    """The sample, and the size of each group before subsampling."""
    rng = random.Random(SEED)
    by_group = load_truth(start, stop, verbose=False)
    cuts = NuclearCuts(mag_max=20.0)
    sample, pool = [], {}
    for group, objs in by_group.items():
        ids = sorted(o for o, alerts in objs.items() if any(cuts.passes(a) for a in alerts))
        pool[group] = len(ids)
        if group in SUBSAMPLE and len(ids) > SUBSAMPLE[group]:
            ids = sorted(rng.sample(ids, SUBSAMPLE[group]))
        sample += [{"object_id": o, "group": group, "truth": truth_class(group)} for o in ids]
    return sample, pool


def weighted(r: dict) -> dict:
    """Undo the SN subsampling: each object counts as pool/sample of its group.

    Returns P(TDE | status) and the purity of ALeRCE's TDE votes, as they
    would be in the full nuclear stream rather than in the subsample.
    """
    w = {g: r["pool_counts"][g] / n for g, n in r["sample_counts"].items()}
    status, tde_votes = {}, [0.0, 0.0]
    for v in r["votes"].values():
        wt = w[v["group"]]
        n, k = status.get(v["status"], (0.0, 0.0))
        status[v["status"]] = (n + wt, k + wt * (v["truth"] == "TDE"))
        if v["alerce"] == "TDE":
            tde_votes[0] += wt
            tde_votes[1] += wt * (v["truth"] == "TDE")
    pool = r["pool_counts"]
    return {"status": status, "alerce_tde_votes": tde_votes,
            "base_rate": pool.get("TDE", 0) / sum(pool.values())}


def fink_science_vote(obj: nw.MergedObject) -> str | None:
    """Fink label rebuilt from alert-time science modules only (no TNS)."""
    r = obj.results.get("fink")
    if r is None:
        return None
    if categorize(r.extra.get("simbad")) == "AGN":
        return "AGN"
    scores = r.classifications.set_index("classifier")["probability"]
    snn = scores.get("supernnova_sn_vs_all")
    if snn is not None and not math.isnan(snn) and snn >= 0.5:
        return "SN"
    return None


def alerce_vote(obj: nw.MergedObject) -> str | None:
    return next((v["category"] for v in obj.consensus()["votes"] if v["broker"] == "alerce"), None)


def alerce_bhrf_vote(obj: nw.MergedObject) -> str | None:
    """ALeRCE's forced-photometry light-curve classifier, which has a TDE class."""
    cls = obj.classifications
    row = cls[(cls["broker"] == "alerce") & (cls["classifier"] == "lc_classifier_BHRF_forced_phot")]
    return categorize(row["label"].iloc[0]) if len(row) else None


def fink_served_vote(obj: nw.MergedObject) -> str | None:
    cls = obj.classifications
    row = cls[(cls["broker"] == "fink") & (cls["classifier"] == "fink_classification")]
    return categorize(row["label"].iloc[0]) if len(row) else None


def lookup(sample: list[dict]) -> dict[str, nw.MergedObject]:
    def one(oid):
        return oid, nw.get_object(oid, brokers=["alerce", "fink"])

    with ThreadPoolExecutor(max_workers=4) as pool:
        out = {}
        for i, (oid, obj) in enumerate(pool.map(one, [s["object_id"] for s in sample]), 1):
            out[oid] = obj
            if i % 50 == 0:
                print(f"  {i}/{len(sample)} looked up", flush=True)
    return out


def lasair_labels(sample: list[dict], pace: float) -> dict[str, str | None]:
    """Sherlock label per object, resumable, paced for the 100/hour limit."""
    cache = json.loads(LASAIR_CACHE.read_text()) if LASAIR_CACHE.exists() else {}
    rng = random.Random(SEED)
    sn = [s["object_id"] for s in sample if s["truth"] == "SN"]
    wanted = [s["object_id"] for s in sample if s["truth"] != "SN"] + sorted(rng.sample(sn, min(LASAIR_SN, len(sn))))
    todo = [o for o in wanted if o not in cache]
    print(f"  lasair: {len(wanted) - len(todo)} cached, {len(todo)} to fetch (~{len(todo) * pace / 60:.0f} min)",
          flush=True)
    for i, oid in enumerate(todo, 1):
        try:
            r = lasair.fetch(oid)
            cache[oid] = r.classifications["label"].iloc[0] if len(r.classifications) else None
        except nw.BrokerError as exc:
            if "rate limit" in str(exc):
                print("  lasair rate limit hit; stopping, rerun later to resume", flush=True)
                break
            cache[oid] = None
        LASAIR_CACHE.write_text(json.dumps(cache, indent=0))
        if i % 10 == 0:
            print(f"  lasair {i}/{len(todo)}", flush=True)
        time.sleep(pace)
    return {o: cache[o] for o in wanted if o in cache}


def antares_votes(sample: list[dict]) -> dict[str, str]:
    def one(oid):
        try:
            r = antares.fetch(oid)
        except nw.BrokerError:
            return oid, "no vote"
        return oid, ("AGN" if len(r.classifications) else "no vote")

    with ThreadPoolExecutor(max_workers=4) as pool:
        return dict(pool.map(one, [s["object_id"] for s in sample]))


def confusion(sample: list[dict], votes: dict[str, str | None], columns: list[str]) -> dict:
    m = {t: Counter() for t in TRUTH_ORDER}
    for s in sample:
        if s["object_id"] in votes:
            m[s["truth"]][votes[s["object_id"]]] += 1
    return {t: {c: m[t][c] for c in columns} for t in TRUTH_ORDER}


def consensus_status(vs: list[str]) -> str:
    cats = {v for v in vs if v not in ("no vote",)}
    if len([v for v in vs if v != "no vote"]) < 2:
        return "insufficient"
    return "agree" if len(cats) == 1 else "disagree"


SHORT_TITLES = {
    "ALeRCE": "ALeRCE\n(nucleuswatch vote)",
    "ALeRCE (forced phot.)": "ALeRCE\nforced phot.",
    "Fink (science modules)": "Fink\nscience modules",
    "ANTARES (AGN catalogues)": "ANTARES\nAGN catalogues",
    "Lasair Sherlock": "Lasair Sherlock\n(subsample)",
}


def figure_confusion(matrices: dict[str, dict], columns: dict[str, list[str]]) -> None:
    import matplotlib.pyplot as plt

    plotstyle.setup()
    names = list(matrices)
    fig, axes = plt.subplots(1, len(names), figsize=(plotstyle.DOUBLE, 2.6),
                             gridspec_kw={"width_ratios": [len(columns[n]) for n in names], "wspace": 0.12})
    for k, (ax, name) in enumerate(zip(np.atleast_1d(axes), names)):
        cols = columns[name]
        counts = np.array([[matrices[name][t][c] for c in cols] for t in TRUTH_ORDER], float)
        frac = counts / np.maximum(counts.sum(axis=1, keepdims=True), 1)
        ax.imshow(frac, cmap="Blues", vmin=0, vmax=1, aspect="auto")
        for i in range(len(TRUTH_ORDER)):
            for j in range(len(cols)):
                if counts[i, j]:
                    ax.text(j, i, f"{int(counts[i, j])}", ha="center", va="center", fontsize=6,
                            color="white" if frac[i, j] > 0.55 else "black")
        ax.set_xticks(range(len(cols)), cols, rotation=55, ha="right", fontsize=7)
        if k == 0:
            ax.set_yticks(range(len(TRUTH_ORDER)),
                          [f"{t} ({int(counts[i].sum())})" for i, t in enumerate(TRUTH_ORDER)])
            ax.set_ylabel("Spectroscopic class (TNS)")
        else:
            ax.set_yticks([])
        ax.minorticks_off()
        ax.tick_params(top=False, right=False, length=0)
        ax.set_title(SHORT_TITLES.get(name, name), fontsize=7.5)
    fig.subplots_adjust(left=0.1, right=0.995, top=0.84, bottom=0.27)
    fig.text(0.55, 0.0, "Broker vote (colour: fraction of each class)", ha="center", va="bottom", fontsize=8)
    plotstyle.save(fig, "broker_confusion")


def figure_status(status_table: dict) -> None:
    import matplotlib.pyplot as plt

    plotstyle.setup()
    fig, ax = plt.subplots(figsize=(plotstyle.SINGLE, 2.2))
    statuses = ["agree", "disagree", "insufficient"]
    left = np.zeros(len(statuses))
    for t in TRUTH_ORDER:
        n = np.array([status_table[s].get(t, 0) for s in statuses], float)
        tot = np.array([max(sum(status_table[s].values()), 1) for s in statuses], float)
        colour = plotstyle.COLOURS["TDE" if t == "TDE" else ("AGN/QSO" if t == "AGN" else "SN Ia")]
        ax.barh(statuses, n / tot, left=left, color=colour, label=t, height=0.6)
        left += n / tot
    for i, s in enumerate(statuses):
        ax.text(1.01, i, f"N={sum(status_table[s].values())}", va="center", fontsize=7.5)
    ax.set_xlim(0, 1)
    ax.set_xlabel("Fraction of objects by spectroscopic class")
    ax.invert_yaxis()
    ax.minorticks_off()
    ax.legend(ncol=3, loc="lower center", bbox_to_anchor=(0.5, 1.0))
    plotstyle.save(fig, "consensus_vs_truth")


def kappa(a: list[str], b: list[str]) -> float:
    """Cohen's kappa over objects where both voted."""
    pairs = [(x, y) for x, y in zip(a, b) if x != "no vote" and y != "no vote"]
    if not pairs:
        return float("nan")
    labels = sorted({x for p in pairs for x in p})
    n = len(pairs)
    po = sum(x == y for x, y in pairs) / n
    pa = Counter(x for x, _ in pairs)
    pb = Counter(y for _, y in pairs)
    pe = sum(pa[k] * pb[k] for k in labels) / (n * n)
    return (po - pe) / (1 - pe) if pe < 1 else float("nan")


def _pint(k: int, n: int) -> str:
    p, lo, hi = plotstyle.wilson(k, n)
    return f"{100 * p:.0f}% ({100 * lo:.0f}–{100 * hi:.0f}%)"


def _weighted_lines(r: dict) -> list[str]:
    wt = weighted(r)
    n, k = wt["alerce_tde_votes"]
    return [
        "### Weighted to the full nuclear stream",
        "",
        "The sample keeps fewer SNe than the stream contains. Weighting each object by",
        "(group size before subsampling) / (group size in the sample):",
        "",
        "| status | P(TDE \\| status) |",
        "|---|---|",
        *(f"| {s} | {100 * kk / nn:.1f}% |" for s, (nn, kk) in wt["status"].items() if nn),
        "",
        (f"An ALeRCE TDE vote is right {100 * k / n:.0f}% of the time ({k:.0f} of {n:.1f} weighted objects), "
         f"against a TDE base rate of {100 * wt['base_rate']:.1f}%."),
        "",
    ]


def render(r: dict) -> str:
    def table(name, cols):
        m = r["confusion"][name]
        rows = [f"| truth \\ vote | {' | '.join(cols)} | correct |", "|---|" + "---|" * (len(cols) + 1)]
        for t in TRUTH_ORDER:
            n = sum(m[t].values())
            ok = m[t].get(t, 0)
            voted = n - m[t].get("no vote", 0)
            rows.append(f"| {t} ({n}) | " + " | ".join(str(m[t][c]) for c in cols)
                        + f" | {ok}/{voted} of those voting |")
        return "\n".join(rows)

    st = r["status_by_truth"]
    p_tde = {s: (st[s].get("TDE", 0), sum(st[s].values())) for s in st}
    lines = [
        "# Broker classifications vs spectroscopic truth",
        "",
        (f"Sample: {r['n_sample']} TNS-classified objects passing the nuclear cuts at mag ≤ 20, "
        f"{r['window'][0]} → {r['window'][1]}"),
        f"({', '.join(f'{k}: {v}' for k, v in r['sample_counts'].items())}). "
        + "; ".join(f"{g} randomly subsampled from {r['pool_counts'][g]}" for g, n in r["sample_counts"].items()
                    if r["pool_counts"][g] > n),
        (f"(seed {SEED}). nucleuswatch {r['nucleuswatch_version']}, generated {r['generated']} by "
        "`validation/broker_truth.py`."),
        "",
        "## ALeRCE, the vote nucleuswatch casts (forced-photometry classifier, else light curve, else stamp)",
        "",
        table("ALeRCE", PRED_ORDER),
        "",
        "## ALeRCE forced-photometry classifier (`lc_classifier_BHRF_forced_phot`, has a TDE class)",
        "",
        table("ALeRCE (forced phot.)", PRED_ORDER),
        "",
        "## Fink, alert-time science modules only (SIMBAD crossmatch + SuperNNova)",
        "",
        table("Fink (science modules)", PRED_ORDER),
        "",
        "## Fink, served classification (includes the TNS answer; leakage)",
        "",
        table("Fink (served)", PRED_ORDER),
        "",
        "## ANTARES, AGN/quasar catalogue match (Milliquas, Véron-Cetty)",
        "",
        table("ANTARES (AGN catalogues)", PRED_ORDER),
        "",
    ]
    if r.get("lasair_n"):
        lines += [f"## Lasair Sherlock (raw labels, N = {r['lasair_n']})", "", table("Lasair Sherlock", r["lasair_columns"]), ""]
    lines += [
        ("## Consensus status vs truth (ALeRCE + Fink science modules + ANTARES"
         + (" + Lasair" if r.get("lasair_n") else "") + ")"),
        "",
        "| status | objects | TDE | SN | AGN | P(TDE \\| status), 68% interval |",
        "|---|---|---|---|---|---|",
        *(f"| {s} | {n} | {st[s].get('TDE', 0)} | {st[s].get('SN', 0)} | {st[s].get('AGN', 0)} | "
          f"{_pint(k, n)} |" for s, (k, n) in p_tde.items() if n),
        "",
        f"Cohen's κ, ALeRCE vs Fink (science modules), where both voted: **{r['kappa_alerce_fink']:.2f}**",
        "",
        *_weighted_lines(r),
        "![confusion](figures/broker_confusion.png)",
        "",
        "![status](figures/consensus_vs_truth.png)",
        "",
        "Caveats: ALeRCE classifications are today's, computed on the full light curve, not at selection",
        "time. Fink's ZTF science modules can't produce a TDE label, so a Fink TDE vote is impossible",
        "by construction. Sherlock labels describe host association (SN = offset from a galaxy, NT = on",
        "the nucleus), not physical type.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="2024-01-01")
    p.add_argument("--stop", default="2026-01-01")
    p.add_argument("--no-lasair", action="store_true")
    p.add_argument("--lasair-pace", type=float, default=40.0, help="seconds between Lasair calls")
    p.add_argument("--render-only", action="store_true")
    args = p.parse_args()
    if args.render_only:
        r = json.loads((HERE / "broker_truth.json").read_text())
        mats = {k: v for k, v in r["confusion"].items() if k != "Fink (served)"}
        cols = {k: (r["lasair_columns"] if k == "Lasair Sherlock" else PRED_ORDER) for k in mats}
        figure_confusion(mats, cols)
        figure_status(r["status_by_truth"])
        (HERE / "BROKER_TRUTH.md").write_text(render(r))
        return 0
    start, stop = date.fromisoformat(args.start), date.fromisoformat(args.stop)

    sample, pool = build_sample(start, stop)
    counts = Counter(s["group"] for s in sample)
    print(f"sample: {len(sample)} objects {dict(counts)}", flush=True)
    objects = lookup(sample)

    al = {o: predicted(alerce_vote(m)) for o, m in objects.items()}
    ab = {o: predicted(alerce_bhrf_vote(m)) for o, m in objects.items()}
    fs = {o: predicted(fink_science_vote(m)) for o, m in objects.items()}
    fv = {o: predicted(fink_served_vote(m)) for o, m in objects.items()}
    an = antares_votes(sample)
    matrices = {
        "ALeRCE": confusion(sample, al, PRED_ORDER),
        "ALeRCE (forced phot.)": confusion(sample, ab, PRED_ORDER),
        "Fink (science modules)": confusion(sample, fs, PRED_ORDER),
        "Fink (served)": confusion(sample, fv, PRED_ORDER),
        "ANTARES (AGN catalogues)": confusion(sample, an, PRED_ORDER),
    }
    columns = {k: PRED_ORDER for k in matrices}

    ls: dict[str, str] = {}
    if not args.no_lasair:
        raw = lasair_labels(sample, args.lasair_pace)
        ls = {o: (lab or "no vote") for o, lab in raw.items()}
        lcols = ["NT", "SN", "AGN", "VS", "other", "no vote"]
        ls = {o: (lab if lab in lcols else "other") for o, lab in ls.items()}
        matrices["Lasair Sherlock"] = confusion(sample, ls, lcols)
        columns["Lasair Sherlock"] = lcols

    status_by_truth: dict[str, Counter] = {"agree": Counter(), "disagree": Counter(), "insufficient": Counter()}
    status = {}
    for s in sample:
        o = s["object_id"]
        vs = [al[o], fs[o], an[o]]
        if o in ls:  # Sherlock: only physical labels vote, as in nucleuswatch
            vs.append(predicted(categorize(ls[o])) if ls[o] not in ("no vote", "other", "NT") else "no vote")
        status[o] = consensus_status(vs)
        status_by_truth[status[o]][s["truth"]] += 1

    order = [s["object_id"] for s in sample]
    result = {
        "nucleuswatch_version": __version__,
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "window": [args.start, args.stop],
        "seed": SEED,
        "n_sample": len(sample),
        "sample_counts": dict(counts),
        "pool_counts": pool,
        "confusion": matrices,
        "lasair_n": len(ls),
        "lasair_columns": columns.get("Lasair Sherlock"),
        "status_by_truth": {k: dict(v) for k, v in status_by_truth.items()},
        "kappa_alerce_fink": kappa([al[o] for o in order], [fs[o] for o in order]),
        "votes": {o: {"group": s["group"], "truth": s["truth"], "status": status[o], "alerce": al[o], "alerce_bhrf": ab[o], "fink_science": fs[o],
                      "fink_served": fv[o], "antares": an[o],
                      "lasair": ls.get(o)} for s in sample for o in [s["object_id"]]},
    }
    (HERE / "broker_truth.json").write_text(json.dumps(result, indent=1, default=str))
    figure_confusion({k: matrices[k] for k in matrices if k != "Fink (served)"},
                     {k: columns[k] for k in columns if k != "Fink (served)"})
    figure_status(result["status_by_truth"])
    (HERE / "BROKER_TRUTH.md").write_text(render(result))
    print((HERE / "BROKER_TRUTH.md").read_text())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
