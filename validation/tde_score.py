"""Fit and cross-validate the TDE ranking score (nucleuswatch.score).

    python validation/tde_score.py [--render-only]

Sample: the broker_truth.py sample (TNS-classified objects passing the nuclear
cuts at mag <= 20, 2024-2025, SNe Ia subsampled), each object weighted by
(group size before subsampling) / (group size in the sample) so the metrics
describe the real nuclear stream.

Honest timing: each object's *selection time* is its first alert passing the
nuclear cuts, i.e. when the weekly search would first have listed it. The
light-curve features are computed from detections up to selection + 0, 7,
14, 28 and 56 days, and from the full light curve. The model is trained on
all of these snapshots together, and cross-validated with every snapshot of
an object in the same fold, so the score is never tested on an object it
has seen. Host features (Legacy Surveys DR9) don't change with time.

For comparison only, a "retrospective" model adds today's broker
classifications (ALeRCE's TDE votes, ALeRCE-Fink disagreement, SuperNNova),
evaluated on full light curves. It isn't shipped: we don't know what the
brokers said when each object was picked.

Model: L2-regularised logistic regression on standardised features, Newton's
method with sample weights (numpy only). The final model is refitted on
everything and written to src/nucleuswatch/assets/tde_score.json.

Writes validation/tde_score.json, validation/TDE_SCORE.md and figures.
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import plotstyle
from broker_truth import build_sample, lookup
from run_validation import load_truth

from nucleuswatch import __version__
from nucleuswatch.brokers.base import JD_TO_MJD
from nucleuswatch.recipes import NuclearCuts
from nucleuswatch.score import FEATURES, MODEL_FILE, features, hosts_for
from nucleuswatch.taxonomy import categorize

HERE = Path(__file__).parent
MODEL_PATH = HERE.parent / "src" / "nucleuswatch" / "assets" / MODEL_FILE
START, STOP = date(2024, 1, 1), date(2026, 1, 1)
EPOCHS = [0, 7, 14, 28, 56, None]  # days after selection; None = full light curve
L2 = 1.0
FOLDS, REPEATS, SEED = 5, 20, 20261024
TOP_FRACTION = 0.10
BROKER_FEATURES = ["alerce_tde", "alerce_transient_tde", "disagree", "snn_sn"]


def epoch_label(e) -> str:
    return "full" if e in (None, "full") else f"+{e} d"


# ------------------------------------------------------------------ model

def fit(x, y, w, l2=L2):
    """Weighted ridge logistic regression; the intercept is not penalised."""
    xa = np.hstack([x, np.ones((len(x), 1))])
    beta = np.zeros(xa.shape[1])
    pen = np.full(xa.shape[1], l2)
    pen[-1] = 0.0
    for _ in range(50):
        p = 1 / (1 + np.exp(-xa @ beta))
        grad = xa.T @ (w * (p - y)) + pen * beta
        hess = (xa * (w * p * (1 - p))[:, None]).T @ xa + np.diag(pen)
        step = np.linalg.solve(hess, grad)
        beta -= step
        if np.abs(step).max() < 1e-8:
            break
    return beta[:-1], float(beta[-1])


def matrix(rows, names, fill):
    x = np.array([[r[f] for f in names] for r in rows], float)
    for j, f in enumerate(names):
        x[np.isnan(x[:, j]), j] = fill[f]
    return x


def train(rows, y, w, names) -> dict:
    fill = {}
    for f in names:
        v = np.array([r[f] for r in rows], float)
        v = v[~np.isnan(v)]
        fill[f] = float(np.median(v)) if len(v) else 0.0
    x = matrix(rows, names, fill)
    mean = np.average(x, axis=0, weights=w)
    scale = np.sqrt(np.average((x - mean) ** 2, axis=0, weights=w))
    scale = np.where(scale > 0, scale, 1.0)
    coef, intercept = fit((x - mean) / scale, y, w)
    return {"features": names, "fill": fill, "mean": mean.tolist(), "scale": scale.tolist(),
            "coef": coef.tolist(), "intercept": intercept}


def predict(model, rows):
    x = matrix(rows, model["features"], model["fill"])
    z = (x - np.array(model["mean"])) / np.array(model["scale"])
    return 1 / (1 + np.exp(-(z @ np.array(model["coef"]) + model["intercept"])))


# ------------------------------------------------------------------ metrics

def weighted_auc(s, y, w):
    pos, neg = y == 1, y == 0
    diff = s[pos][:, None] - s[neg][None, :]
    ww = w[pos][:, None] * w[neg][None, :]
    return float((ww * ((diff > 0) + 0.5 * (diff == 0))).sum() / ww.sum())


def top_fraction(s, y, w, frac=TOP_FRACTION):
    """Weighted (precision, recall) in the top ``frac`` of the weighted ranked list."""
    order = np.argsort(-s, kind="stable")
    cw = np.cumsum(w[order])
    top = order[: int(np.searchsorted(cw, frac * w.sum())) + 1]
    hit = (w[top] * y[top]).sum()
    return float(hit / w[top].sum()), float(hit / (w * y).sum())


def pr_curve(s, y, w):
    order = np.argsort(-s, kind="stable")
    tp = np.cumsum((w * y)[order])
    return tp / (w * y).sum(), tp / np.cumsum(w[order])


def quantiles(v):
    return [float(np.percentile(v, 16)), float(np.median(v)), float(np.percentile(v, 84))]


def cross_validate(snap, y, w, names, epochs):
    """``snap[e]`` holds one feature dict per object at epoch ``e``.

    Training uses every epoch in ``epochs`` of the training objects; each
    epoch is scored separately on the held-out objects.
    """
    rng = np.random.default_rng(SEED)
    n = len(y)
    per = {e: {"auc": [], "precision": [], "recall": [], "oof": []} for e in epochs}
    for _ in range(REPEATS):
        folds = np.empty(n, int)
        for cls in (0, 1):
            idx = rng.permutation(np.flatnonzero(y == cls))
            folds[idx] = np.arange(len(idx)) % FOLDS
        oof = {e: np.empty(n) for e in epochs}
        for k in range(FOLDS):
            tr, te = np.flatnonzero(folds != k), np.flatnonzero(folds == k)
            rows = [snap[e][i] for e in epochs for i in tr]
            model = train(rows, np.tile(y[tr], len(epochs)), np.tile(w[tr], len(epochs)) / len(epochs), names)
            for e in epochs:
                oof[e][te] = predict(model, [snap[e][i] for i in te])
        for e in epochs:
            p, r = top_fraction(oof[e], y, w)
            per[e]["auc"].append(weighted_auc(oof[e], y, w))
            per[e]["precision"].append(p)
            per[e]["recall"].append(r)
            per[e]["oof"].append(oof[e])
    return {epoch_label(e): {"auc": quantiles(v["auc"]), "precision_top": quantiles(v["precision"]),
                             "recall_top": quantiles(v["recall"]),
                             "oof": np.median(v["oof"], axis=0).tolist()} for e, v in per.items()}


# ------------------------------------------------------------------ data

def selection_mjd(start, stop) -> dict[str, float]:
    """First alert of each object that passes the nuclear cuts at mag <= 20."""
    cuts = NuclearCuts(mag_max=20.0)
    out = {}
    for objs in load_truth(start, stop, verbose=False).values():
        for oid, alerts in objs.items():
            jds = [a["i:jd"] for a in alerts if a.get("i:jd") and cuts.passes(a)]
            if jds:
                out[oid] = min(jds) - JD_TO_MJD
    return out


def broker_features(obj) -> dict[str, float]:
    """Today's broker outputs (retrospective comparison only)."""
    cls = obj.classifications

    def top(classifier):
        rows = cls[(cls["broker"] == "alerce") & (cls["classifier"] == classifier)]
        if not len(rows):
            return None
        return rows.loc[rows["probability"].astype(float).fillna(-1).idxmax(), "label"]

    snn = math.nan
    fink = obj.results.get("fink")
    if fink is not None and len(fink.classifications):
        snn = float(fink.classifications.set_index("classifier")["probability"].get("supernnova_sn_vs_all", math.nan))
    votes = {v["broker"]: v["category"] for v in obj.consensus()["votes"]}
    return {
        "alerce_tde": float(categorize(top("lc_classifier_BHRF_forced_phot")) == "TDE"),
        "alerce_transient_tde": float(categorize(top("lc_classifier_BHRF_forced_phot_transient")) == "TDE"),
        "disagree": float("alerce" in votes and "fink" in votes and votes["alerce"] != votes["fink"]),
        "snn_sn": snn,
    }


# ------------------------------------------------------------------ outputs

def figure(r) -> None:
    import matplotlib.pyplot as plt

    plotstyle.setup()
    y, w = np.array(r["y"]), np.array(r["w"])
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(plotstyle.DOUBLE, 2.7))

    labels = [epoch_label(e) for e in EPOCHS]
    xs = np.arange(len(labels))
    for key, name, colour in (("recall_top", f"TDEs in the top {100 * TOP_FRACTION:.0f}%", plotstyle.COLOURS["TDE"]),
                              ("precision_top", f"TDE share of the top {100 * TOP_FRACTION:.0f}%",
                               plotstyle.COLOURS["SN Ia"])):
        q = np.array([r["cv"][lab][key] for lab in labels])
        ax1.errorbar(xs, q[:, 1], yerr=[q[:, 1] - q[:, 0], q[:, 2] - q[:, 1]], color=colour, marker="o", ms=3.5,
                     lw=1.1, capsize=2, label=name)
    ax1.axhline(r["base_rate"], color="0.6", lw=0.7, ls="--")
    ax1.text(0.0, r["base_rate"] + 0.015, "base rate", fontsize=7, color="0.4")
    ax1.set_xticks(xs, ["at\nselection" if lab == "+0 d" else lab for lab in labels])
    ax1.minorticks_off()
    ax1.set_ylim(0, 1)
    ax1.set_xlabel("Light curve used: days after the object was first selected")
    ax1.set_ylabel("Fraction (cross-validated)")
    ax1.legend(loc="lower right", fontsize=7)

    for lab, style in (("+0 d", "--"), ("full", "-")):
        rec, prec = pr_curve(np.array(r["cv"][lab]["oof"]), y, w)
        ax2.plot(rec, prec, color=plotstyle.COLOURS["TDE"], ls=style, lw=1.1,
                 label="at selection" if lab == "+0 d" else "full light curve")
    rec, prec = pr_curve(np.array(r["retro"]["oof"]), y, w)
    ax2.plot(rec, prec, color="0.35", lw=0.9, ls=":", label="full + today's broker labels")
    ax2.axhline(r["base_rate"], color="0.6", lw=0.7, ls="--")
    ax2.set_xlim(0, 1)
    ax2.set_ylim(0, 1)
    ax2.set_xlabel("Fraction of TDEs recovered (recall)")
    ax2.set_ylabel("Fraction that are TDEs (precision)")
    ax2.legend(loc="upper right", fontsize=7)
    fig.tight_layout()
    plotstyle.save(fig, "tde_score_pr")


def render(r) -> str:
    def pct(v):
        return f"{100 * v[1]:.0f}% ({100 * v[0]:.0f}–{100 * v[2]:.0f}%)"

    coef = sorted(zip(r["model"]["features"], r["model"]["coef"]), key=lambda t: -abs(t[1]))
    retro, no_host, full = r["retro"], r["no_host"]["full"], r["cv"]["full"]
    top = f"{100 * TOP_FRACTION:.0f}%"
    lines = [
        "# TDE ranking score",
        "",
        (f"{r['n']} TNS-classified nuclear objects ({r['n_tde']} TDEs), {START} → {STOP}, weighted to the full "
         f"nuclear stream (TDE base rate {100 * r['base_rate']:.1f}%); {r['n_host']} have a Legacy Surveys host, "
         f"{r['n_z']} of them with a redshift. nucleuswatch {r['nucleuswatch_version']}, generated {r['generated']} "
         "by `validation/tde_score.py`."),
        "",
        "Each object is scored from its light curve as it stood at *selection* (its first alert passing the nuclear",
        ("cuts, when the weekly search would first list it) and at later dates. Cross-validation: "
         f"{FOLDS}-fold, stratified, repeated {REPEATS} times, with all of an object's snapshots in one fold "
         "(median and 16–84th percentiles)."),
        "",
        f"| light curve used | ROC AUC | TDEs in the top {top} | TDE share of the top {top} |",
        "|---|---|---|---|",
        *(f"| {'at selection' if lab == '+0 d' else lab} | {r['cv'][lab]['auc'][1]:.2f} | "
          f"{pct(r['cv'][lab]['recall_top'])} | {pct(r['cv'][lab]['precision_top'])} |"
          for lab in (epoch_label(e) for e in EPOCHS)),
        "",
        "For comparison, on full light curves only:",
        "",
        f"| model | ROC AUC | TDEs in the top {top} | TDE share of the top {top} |",
        "|---|---|---|---|",
        (f"| shipped (light curve + host) | {full['auc'][1]:.2f} | {pct(full['recall_top'])} | "
         f"{pct(full['precision_top'])} |"),
        (f"| + today's broker labels (not shipped) | {retro['auc'][1]:.2f} | {pct(retro['recall_top'])} | "
         f"{pct(retro['precision_top'])} |"),
        (f"| light curve only, no host | {no_host['auc'][1]:.2f} | {pct(no_host['recall_top'])} | "
         f"{pct(no_host['precision_top'])} |"),
        "",
        "Broker labels are left out of the shipped model because only today's are known; what the brokers said",
        "when each object was picked isn't recorded. They add little once the light curve is in.",
        "",
        "Host features matter most early, while the light curve is short:",
        "",
        "| light curve used | ROC AUC with host | without host |",
        "|---|---|---|",
        *(f"| {'at selection' if lab == '+0 d' else lab} | {r['cv'][lab]['auc'][1]:.2f} | "
          f"{r['no_host'][lab]['auc'][1]:.2f} |" for lab in (epoch_label(e) for e in EPOCHS)),
        "",
        "Standardised coefficients of the shipped model (positive = more TDE-like):",
        "",
        "| feature | coefficient |",
        "|---|---|",
        *(f"| `{f}` | {c:+.2f} |" for f, c in coef),
        "",
        "![score vs time](figures/tde_score_pr.png)",
        "",
        f"Caveats: {r['n_tde']} TDEs is a small training set, so the model is small and strongly regularised",
        f"(L2 = {L2}). Host redshifts are mostly photometric, which blurs the absolute magnitudes. The score",
        "ranks; it isn't a calibrated probability. `validation/prospective.py` measures the live picks.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--render-only", action="store_true")
    args = p.parse_args()
    out = HERE / "tde_score.json"
    if args.render_only:
        r = json.loads(out.read_text())
        figure(r)
        (HERE / "TDE_SCORE.md").write_text(render(r))
        return 0

    sample, pool = build_sample(START, STOP)
    t_sel = selection_mjd(START, STOP)
    sample = [s for s in sample if s["object_id"] in t_sel]
    counts = {g: sum(s["group"] == g for s in sample) for g in pool}
    objects = lookup(sample)
    found = hosts_for({s["object_id"]: objects[s["object_id"]] for s in sample})
    print(f"hosts: {sum(v is not None for v in found.values())}/{len(sample)}", flush=True)

    snap = {}
    for e in EPOCHS:
        snap[e] = [features(objects[s["object_id"]], found.get(s["object_id"]),
                            None if e is None else t_sel[s["object_id"]] + e) for s in sample]
    y = np.array([s["truth"] == "TDE" for s in sample], float)
    w = np.array([pool[s["group"]] / counts[s["group"]] for s in sample])

    cv = cross_validate(snap, y, w, FEATURES, EPOCHS)
    full = {"full": [{**row, **broker_features(objects[s["object_id"]])}
                     for row, s in zip(snap[None], sample)]}
    retro = cross_validate(full, y, w, FEATURES + BROKER_FEATURES, ["full"])["full"]
    host_free = [f for f in FEATURES if not (f.startswith("host_") or f == "peak_abs_mag")]
    no_host = cross_validate(snap, y, w, host_free, EPOCHS)

    rows = [snap[e][i] for e in EPOCHS for i in range(len(sample))]
    model = train(rows, np.tile(y, len(EPOCHS)), np.tile(w, len(EPOCHS)) / len(EPOCHS), FEATURES)
    model.update({"nucleuswatch_version": __version__, "n_train": len(y), "n_tde": int(y.sum()),
                  "epochs_days": [e for e in EPOCHS if e is not None] + ["full"],
                  "fitted": datetime.now(timezone.utc).date().isoformat()})
    r = {
        "nucleuswatch_version": __version__,
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "n": len(y), "n_tde": int(y.sum()), "base_rate": float((w * y).sum() / w.sum()),
        "n_host": sum(found.get(s["object_id"]) is not None for s in sample),
        "n_z": sum((found.get(s["object_id"]) or {}).get("z") is not None for s in sample),
        "cv": cv, "retro": retro, "no_host": no_host, "model": model,
        "y": y.tolist(), "w": w.tolist(), "object_ids": [s["object_id"] for s in sample],
    }
    out.write_text(json.dumps(r, indent=1))
    MODEL_PATH.write_text(json.dumps(model, indent=1) + "\n")
    figure(r)
    (HERE / "TDE_SCORE.md").write_text(render(r))
    print((HERE / "TDE_SCORE.md").read_text())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
