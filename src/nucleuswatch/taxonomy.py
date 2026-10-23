"""Map each broker's class labels onto one shared, coarse set of categories.

Brokers use different taxonomies (ALeRCE's ``SNIbc``, Fink's ``SN candidate``,
Lasair/Sherlock's ``SN``), so labels have to be normalised before they can be
compared. The categories are intentionally coarse: the goal is to detect real
disagreement (supernova vs. AGN), not to rank subtypes.
"""

from __future__ import annotations

import re

SN = "SN"
TDE = "TDE"
KILONOVA = "Kilonova"
AGN = "AGN"
CV = "CV/Nova"
VARSTAR = "VarStar"
STAR = "Star"
MICROLENSING = "Microlensing"
SOLAR_SYSTEM = "SolarSystem"
BOGUS = "Bogus"

# Classifiers whose output is a single best-guess label for the whole object,
# per broker, in order of preference. Each broker casts ONE vote: the first
# classifier in its list that gave a usable label. ALeRCE's light-curve
# classifier uses the whole light curve, so it is preferred over the stamp
# classifier, which only sees the first images. Binary scores (e.g. Fink's
# SNIa-vs-non-Ia) and ALeRCE's conditional sub-classifiers never vote.
VOTE_PRIORITY = {
    "alerce": ("lc_classifier", "stamp_classifier"),
    "fink": ("fink_classification", "cats"),  # cats: Rubin/LSST
    "lasair": ("sherlock",),
}
VOTING_CLASSIFIERS = {(b, c) for b, names in VOTE_PRIORITY.items() for c in names}

# Ordered (pattern, category) rules matched case-insensitively against labels.
_RULES: list[tuple[str, str | None]] = [
    # Abstentions first, so "Unknown"/"Ambiguous" never match anything else.
    (r"^(unknown|ambiguous|unclear|orphan|fail.*|none|nan)$", None),
    # Fink CATS (LSST) broad classes that mix several physical types.
    (r"^(fast|long)$", None),
    (r"^non-periodic$", AGN),
    (r"^periodic$", VARSTAR),
    # Sherlock "NT" says *where* the transient is (on a galaxy nucleus), not
    # what it is. Every nucleuswatch candidate is nuclear by construction, so
    # it carries no information about SN vs AGN vs TDE and abstains.
    (r"^(sherlock-)?nt$", None),
    (r"^bs$", STAR),
    (r"^vs$", VARSTAR),
    (r"^cv$|cv/nova|cataclyv|^nova|^cv\*", CV),
    (r"tde", TDE),
    (r"kilonova|^kn", KILONOVA),
    (r"microlens", MICROLENSING),
    (r"solar system|tracklet|asteroid|^sso", SOLAR_SYSTEM),
    (r"bogus|artefact|artifact", BOGUS),
    (r"slsn|^sn|sn ia|supernova", SN),
    (r"agn|qso|blazar|seyfert|bllac|bl_lac|quasar", AGN),
    (r"^(rrl|rrlyr|cep|cepheid|dsct|delsct|eb|eb\*|lpv|lpv\*|mira|yso|e|periodic.*|v\*|variable.*|pulsv\*|rotv\*)$", VARSTAR),
    (r"^(\*|star|stars?|high ?pm\*|pm\*)$", STAR),
]


def categorize(label: str | None) -> str | None:
    """Return the coarse category for a broker label, or None to abstain."""
    if label is None:
        return None
    text = str(label).strip().lower()
    if not text:
        return None
    for pattern, category in _RULES:
        if re.search(pattern, text):
            return category
    return None
