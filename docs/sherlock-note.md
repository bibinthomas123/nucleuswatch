# Sherlock labels some galaxy nuclei as variable stars

A note for the Lasair team, from validating
[nucleuswatch](https://github.com/bibinthomas123/nucleuswatch) against
spectroscopic classifications. To report it, open an issue with the Lasair
team and link this page.

## Summary

We checked Sherlock against 146 ZTF objects in galaxy nuclei with TNS
spectroscopic classifications (2024–2025). It labels **11 of 34 TDEs and 27
of 72 AGN as `VS`** (variable star). In every TDE case we examined, the
description says the transient is *"synonymous with … a stellar source found
in the GAIA catalogue"* (or GSC/GAIA). Gaia DR3's own source classification
calls those sources **galaxies**: DSC galaxy probability ≈ 1, no significant
parallax, and no proper motion above 3σ. Gaia detects the compact, bright nucleus
of the host and Sherlock treats it as a star, so a nuclear transient becomes
"VS".

## Examples

These objects are spectroscopically classified as TDEs on TNS today, and
Sherlock labels them `VS`:

| ZTF | TNS | Sherlock match | sep. |
|---|---|---|---|
| ZTF20aahmtso | AT 2022gri | Gaia 896352978822838144, G = 17.87 | 0.9″ |
| ZTF20abwtifz | AT 2020afhd | GSC/Gaia S1SF000213, B = 15.06 | 0.6″ |
| ZTF20accxwrk | AT 2020ukj | Gaia 989332389351642880, G = 18.88 | 0.8″ |
| ZTF21aanxhjv | AT 2021ehb | Gaia 239058846065255552, G = 17.69 | 0.4″ |
| ZTF24aaecooj | AT 2024bgz | GSC/Gaia S53Q000166, B = 14.77 | 0.2″ |
| ZTF24abfuwbm | AT 2024vip | Gaia 2969002250532085632, G = 19.51 | 0.3″ |
| ZTF25aafwfzz | AT 2025cyh | Gaia 4589066865223674752, G = 19.69 | 0.2″ |
| ZTF25abwvuln | AT 2025abqg | Gaia 2746473943312685056, G = 19.63 | 0.2″ |

Gaia DR3 `gaia_source` for the matched sources we could retrieve:

| Gaia DR3 source_id | parallax / error | proper motion (mas/yr) | P(galaxy) | P(star) |
|---|---|---|---|---|
| 989332389351642880 (ZTF20accxwrk) | −0.29 | 0.75 ± 1.1 | 1.000 | 0.000 |
| 4589066865223674752 (ZTF25aafwfzz) | −0.09 | 1.30 ± 0.5 | 1.000 | 0.000 |
| 2746473943312685056 (ZTF25abwvuln) | 1.19 | 0.92 ± 1.0 | 1.000 | 0.000 |
| 2827574990425655040 (AT 2024pvu: ZTF18abxnvoz, ZTF24abiqyej) | −0.59 | 0.25 ± 0.4 | 1.000 | 0.000 |

P(galaxy) and P(star) are `classprob_dsc_combmod_galaxy` and
`classprob_dsc_combmod_star`.

## Possible fix

Before treating a Gaia match as stellar, require some evidence that it is a
star. Options, in increasing order of effort:

- a significant parallax or proper motion (e.g. `parallax_over_error > 3`
  or a proper-motion significance above 3);
- Gaia DR3's DSC classification (`classprob_dsc_combmod_star` high, or the
  galaxy probability low);
- morphology from Pan-STARRS or the Legacy Surveys: an extended source at
  the same position means the "star" is a galaxy core.

A Gaia match that fails these tests could fall through to the galaxy
catalogues, where the transient would come out as `NT`.

## Reproduce

```python
from nucleuswatch.brokers import lasair
r = lasair.fetch("ZTF20aahmtso")          # needs LASAIR_TOKEN
r.classifications                          # sherlock: VS
r.extra["sherlock_description"]            # "... synonymous with 896352978822838144; a G=17.87 mag stellar source ..."
```

The Gaia check, through any Gaia DR3 TAP service:

```sql
SELECT source_id, parallax_over_error, pm, classprob_dsc_combmod_galaxy, classprob_dsc_combmod_star
FROM gaiadr3.gaia_source
WHERE source_id IN (989332389351642880, 4589066865223674752, 2746473943312685056, 2827574990425655040)
```

The full comparison is in
[validation/BROKER_TRUTH.md](https://github.com/bibinthomas123/nucleuswatch/blob/main/validation/BROKER_TRUTH.md).
