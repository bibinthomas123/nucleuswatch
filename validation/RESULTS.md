# Validation of the nuclear cuts

Window **2024-01-01 → 2026-01-01**, ZTF alerts via Fink, ground truth = TNS
spectroscopic classification. nucleuswatch 0.2.0, generated
2026-10-23T19:30:42+00:00 by `validation/run_validation.py`. Default cuts: `mag_max=19.0`, `distnr_max=0.5`, `distpsnr1_max=1.0`, `sgscore1_max=0.5`, `ndethist_min=2`.

## Recovery of spectroscopically confirmed TDEs

**24 / 47 (51%)** confirmed TDEs have at least one
alert passing the default cuts.

| Removed by | TDEs |
|---|---|
| `mag_max` | 21 |
| `distnr` | 2 |

Recovery as a function of the magnitude limit (all other cuts at default):

| `mag_max` | recovered | fraction |
|---|---|---|
| 18.5 | 14 | 30% |
| 19.0 | 24 | 51% |
| 19.5 | 30 | 64% |
| 20.0 | 34 | 72% |
| 20.5 | 37 | 79% |

## What else passes the cuts

Spectroscopically classified objects of each type in the same window, and how many pass:

| TNS type | objects | pass cuts | pass rate | share before cuts | share after cuts |
|---|---|---|---|---|---|
| SN Ia | 2505 | 236 | 9% | 65.0% | 58.7% |
| SN II | 808 | 56 | 7% | 21.0% | 13.9% |
| AGN/QSO | 117 | 48 | 41% | 3.0% | 11.9% |
| TDE | 47 | 24 | 51% | 1.2% | 6.0% |
| SN IIn | 123 | 16 | 13% | 3.2% | 4.0% |
| SN Ibc | 180 | 15 | 8% | 4.7% | 3.7% |
| SLSN | 75 | 7 | 9% | 1.9% | 1.7% |

The cuts raise the TDE share of the classified sample **4.9×** (from 1.2% to 6.0%).

## Caveats

- This measures the **positional/brightness cuts** only. The live recipe additionally
  draws candidates from four Fink classifier streams; that stream-selection step is not
  measured here.
- Ground truth is limited to objects that were spectroscopically classified on TNS and
  cross-matched by Fink, which favours bright, well-observed events.
- Nuclear SNe are genuinely nuclear and are expected to pass; their share is a measure of
  how much non-TDE follow-up a nuclear sample costs, not an error of the cuts.
