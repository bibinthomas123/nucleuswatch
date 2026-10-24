# TDE ranking score

402 TNS-classified nuclear objects (34 TDEs), 2024-01-01 → 2026-01-01, weighted to the full nuclear stream (TDE base rate 5.8%). nucleuswatch 1.0.0, generated 2026-10-24T17:57:02+00:00 by `validation/tde_score.py`.

Cross-validation: 5-fold, stratified, repeated 20 times (median and 16–84th percentiles).

| metric | value |
|---|---|
| ROC AUC | 0.92 (0.91–0.92) |
| TDE share in the top 10% of the ranked list | 43% (42–43%) |
| TDEs that land in the top 10% | 76% (74–76%) |

Single-flag baselines on the same weighting:

| flag | TDEs recovered | TDE share among flagged |
|---|---|---|
| ALeRCE TDE vote | 32% | 35% |
| ALeRCE and Fink disagree | 15% | 13% |

Standardised coefficients of the final model (positive = more TDE-like):

| feature | coefficient |
|---|---|
| `g_r_slope` | -2.00 |
| `g_r` | -1.92 |
| `amplitude` | +0.63 |
| `log_rise` | -0.58 |
| `alerce_tde` | +0.49 |
| `simbad_agn` | -0.40 |
| `snn_sn` | -0.32 |
| `disagree` | +0.13 |
| `alerce_transient_tde` | -0.10 |
| `log_ndet` | +0.08 |

![precision-recall](figures/tde_score_pr.png)

Caveats: features come from today's light curves and classifications, which cover more of each
event than the weekly digest sees when it first selects the object, so these numbers are an
upper bound on live performance (`validation/prospective.py` measures the live picks). With
34 TDEs the model is deliberately small and strongly regularised (L2 = 1.0).
