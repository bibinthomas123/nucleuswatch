"""Fit and cross-validate the TDE ranking score (nucleuswatch.score).

    python validation/tde_score.py [--render-only]

Same sample as broker_truth.py: TNS-classified objects passing the nuclear
cuts at mag <= 20, 2024-2025, with SNe Ia subsampled. Each object is weighted
by (group size before subsampling) / (group size in the sample), so the
metrics describe the real nuclear stream, not the subsample.

Model: L2-regularised logistic regression on standardised features, fitted by
Newton's method with sample weights (numpy only). Scored by repeated
stratified 5-fold cross-validation; the final model is refitted on everything
and written to src/nucleuswatch/assets/tde_score.json.

Writes validation/tde_score.json, validation/TDE_SCORE.md and a figure.
"""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import plotstyle
from broker_truth import build_sample, lookup

from nucleuswatch import __version__
from nucleuswatch.score import FEATURES, MODEL_FILE, design, features

HERE = Path(__file__).parent
MODEL_PATH = HERE.parent / "src" / "nucleuswatch" / "assets" / MODEL_FILE
L2 = 1.0
FOLDS, REPEATS, SEED = 5, 20, 20261024
TOP_FRACTION = 0.10  # "the top 10% of the weekly list"


def fit(x: np.ndarray, y: np.ndarray, w: np.ndarray, l2: float = L2) -> tuple[np.ndarray, float]:
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


def standardise(x: np.ndarray, w: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    mean = np.average(x, axis=0, weights=w)
    scale = np.sqrt(np.average((x - mean) ** 2, axis=0, weights=w))
    return mean, np.where(scale > 0, scale, 1.0)


def medians(rows: list[dict]) -> dict[str, float]:
    out = {}
    for f in FEATURES:
        v = np.array([r[f] for r in rows], float)
        v = v[~np.isnan(v)]
        out[f] = float(np.median(v)) if len(v) else 0.0
    return out


def train(rows, y, w) -> dict:
    fill = medians(rows)
    x = design(rows, fill)
    mean, scale = standardise(x, w)
    coef, intercept = fit((x - mean) / scale, y, w)
    return {"features": FEATURES, "fill": fill, "mean": mean.tolist(), "scale": scale.tolist(),
            "coef": coef.tolist(), "intercept": intercept}


def predict(model: dict, rows) -> np.ndarray:
    x = design(rows, model["fill"])
    z = (x - np.array(model["mean"])) / np.array(model["scale"])
    return 1 / (1 + np.exp(-(z @ np.array(model["coef"]) + model["intercept"])))


def weighted_auc(s: np.ndarray, y: np.ndarray, w: np.ndarray) -> float:
    pos, neg = y == 1, y == 0
    # P(score of a random TDE > score of a random non-TDE), ties count half.
    diff = s[pos][:, None] - s[neg][None, :]
    ww = w[pos][:, None] * w[neg][None, :]
    return float((ww * ((diff > 0) + 0.5 * (diff == 0))).sum() / ww.sum())


def precision_at(s: np.ndarray, y: np.ndarray, w: np.ndarray, frac: float) -> tuple[float, float]:
    """Weighted precision and recall in the top ``frac`` of the weighted list."""
    order = np.argsort(-s, kind="stable")
    cw = np.cumsum(w[order])
    top = order[: int(np.searchsorted(cw, frac * w.sum())) + 1]
    return float((w[top] * y[top]).sum() / w[top].sum()), float((w[top] * y[top]).sum() / (w * y).sum())


def pr_curve(s, y, w) -> tuple[np.ndarray, np.ndarray]:
    order = np.argsort(-s, kind="stable")
    tp = np.cumsum((w * y)[order])
    allw = np.cumsum(w[order])
    return tp / (w * y).sum(), tp / allw


def flag_point(flag: np.ndarray, y, w) -> tuple[float, float]:
    sel = flag > 0
    return float((w[sel] * y[sel]).sum() / (w * y).sum()), float((w[sel] * y[sel]).sum() / w[sel].sum())


def cross_validate(rows, y, w) -> dict:
    rng = np.random.default_rng(SEED)
    oof_all = []
    aucs, precs, recs = [], [], []
    for _ in range(REPEATS):
        folds = np.empty(len(y), int)
        for cls in (0, 1):  # stratified: TDEs spread evenly over the folds
            idx = rng.permutation(np.flatnonzero(y == cls))
            folds[idx] = np.arange(len(idx)) % FOLDS
        oof = np.empty(len(y))
        for k in range(FOLDS):
            tr, te = folds != k, folds == k
            m = train([rows[i] for i in np.flatnonzero(tr)], y[tr], w[tr])
            oof[te] = predict(m, [rows[i] for i in np.flatnonzero(te)])
        oof_all.append(oof)
        aucs.append(weighted_auc(oof, y, w))
        p, r = precision_at(oof, y, w, TOP_FRACTION)
        precs.append(p)
        recs.append(r)
    q = lambda v: [float(np.percentile(v, 16)), float(np.median(v)), float(np.percentile(v, 84))]
    return {"auc": q(aucs), "precision_top": q(precs), "recall_top": q(recs),
            "oof": np.median(oof_all, axis=0).tolist()}


def figure(r: dict) -> None:
    import matplotlib.pyplot as plt

    plotstyle.setup()
    y, w, s = np.array(r["y"]), np.array(r["w"]), np.array(r["cv"]["oof"])
    rec, prec = pr_curve(s, y, w)
    fig, ax = plt.subplots(figsize=(plotstyle.SINGLE, 2.6))
    ax.plot(rec, prec, color=plotstyle.COLOURS["TDE"], lw=1.2, label="TDE score (cross-validated)")
    marks = {"alerce_tde": ("s", "ALeRCE TDE vote"), "disagree": ("o", "ALeRCE and Fink disagree")}
    for f, (m, label) in marks.items():
        rc, pc = r["baselines"][f]
        ax.plot(rc, pc, m, color="0.25", ms=4.5, label=label)
    ax.axhline(r["base_rate"], color="0.6", lw=0.7, ls="--")
    ax.text(0.02, r["base_rate"] + 0.012, "base rate", ha="left", va="bottom", fontsize=7, color="0.4")
    ax.set_xlabel("Fraction of TDEs recovered (recall)")
    ax.set_ylabel("Fraction that are TDEs (precision)")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.legend(loc="upper right", fontsize=7)
    plotstyle.save(fig, "tde_score_pr")


def render(r: dict) -> str:
    cv = r["cv"]
    pct = lambda v: f"{100 * v[1]:.0f}% ({100 * v[0]:.0f}–{100 * v[2]:.0f}%)"
    coef = sorted(zip(FEATURES, r["model"]["coef"]), key=lambda t: -abs(t[1]))
    lines = [
        "# TDE ranking score",
        "",
        (f"{r['n']} TNS-classified nuclear objects ({r['n_tde']} TDEs), 2024-01-01 → 2026-01-01, weighted to the "
         f"full nuclear stream (TDE base rate {100 * r['base_rate']:.1f}%). nucleuswatch {r['nucleuswatch_version']}, "
         f"generated {r['generated']} by `validation/tde_score.py`."),
        "",
        f"Cross-validation: {FOLDS}-fold, stratified, repeated {REPEATS} times (median and 16–84th percentiles).",
        "",
        "| metric | value |",
        "|---|---|",
        f"| ROC AUC | {cv['auc'][1]:.2f} ({cv['auc'][0]:.2f}–{cv['auc'][2]:.2f}) |",
        f"| TDE share in the top {100 * TOP_FRACTION:.0f}% of the ranked list | {pct(cv['precision_top'])} |",
        f"| TDEs that land in the top {100 * TOP_FRACTION:.0f}% | {pct(cv['recall_top'])} |",
        "",
        "Single-flag baselines on the same weighting:",
        "",
        "| flag | TDEs recovered | TDE share among flagged |",
        "|---|---|---|",
        *(f"| {name} | {100 * rc:.0f}% | {100 * pc:.0f}% |" for name, (rc, pc) in
          [("ALeRCE TDE vote", r["baselines"]["alerce_tde"]), ("ALeRCE and Fink disagree", r["baselines"]["disagree"])]),
        "",
        "Standardised coefficients of the final model (positive = more TDE-like):",
        "",
        "| feature | coefficient |",
        "|---|---|",
        *(f"| `{f}` | {c:+.2f} |" for f, c in coef),
        "",
        "![precision-recall](figures/tde_score_pr.png)",
        "",
        "Caveats: features come from today's light curves and classifications, which cover more of each",
        "event than the weekly digest sees when it first selects the object, so these numbers are an",
        "upper bound on live performance (`validation/prospective.py` measures the live picks). With",
        f"{r['n_tde']} TDEs the model is deliberately small and strongly regularised (L2 = {L2}).",
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
    else:
        sample, pool = build_sample(date(2024, 1, 1), date(2026, 1, 1))
        counts = {g: sum(s["group"] == g for s in sample) for g in pool}
        objects = lookup(sample)
        rows = [features(objects[s["object_id"]]) for s in sample]
        y = np.array([s["truth"] == "TDE" for s in sample], float)
        w = np.array([pool[s["group"]] / counts[s["group"]] for s in sample])
        cv = cross_validate(rows, y, w)
        model = train(rows, y, w)
        model.update({"nucleuswatch_version": __version__, "n_train": len(y), "n_tde": int(y.sum()),
                      "fitted": datetime.now(timezone.utc).date().isoformat()})
        x = {f: np.array([row[f] for row in rows]) for f in FEATURES}
        r = {
            "nucleuswatch_version": __version__,
            "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "n": len(y), "n_tde": int(y.sum()), "base_rate": float((w * y).sum() / w.sum()),
            "cv": cv, "model": model, "y": y.tolist(), "w": w.tolist(),
            "object_ids": [s["object_id"] for s in sample],
            "baselines": {f: flag_point(np.nan_to_num(x[f]), y, w) for f in ("alerce_tde", "disagree")},
        }
        out.write_text(json.dumps(r, indent=1))
        MODEL_PATH.write_text(json.dumps(model, indent=1) + "\n")
    figure(r)
    (HERE / "TDE_SCORE.md").write_text(render(r))
    print((HERE / "TDE_SCORE.md").read_text())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
