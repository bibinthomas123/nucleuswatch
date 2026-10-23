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
NUCLEAR = "Nuclear"  # Sherlock "NT": nuclear transient, AGN or TDE
AGN = "AGN"
CV = "CV/Nova"
VARSTAR = "VarStar"
STAR = "Star"
MICROLENSING = "Microlensing"
SOLAR_SYSTEM = "SolarSystem"
BOGUS = "Bogus"

# Classifiers whose output is a single best-guess label for the whole object.
# Binary scores (e.g. Fink's SNIa-vs-non-Ia) and ALeRCE's conditional
# sub-classifiers are excluded because they only answer a narrower question.
VOTING_CLASSIFIERS = {
    ("alerce", "lc_classifier"),
    ("alerce", "stamp_classifier"),
    ("fink", "fink_classification"),
    ("lasair", "sherlock"),
}

# Ordered (pattern, category) rules matched case-insensitively against labels.
_RULES: list[tuple[str, str | None]] = [
    # Abstentions first, so "Unknown"/"Ambiguous" never match anything else.
    (r"^(unknown|ambiguous|unclear|orphan|fail.*|none|nan)$", None),
    (r"^(sherlock-)?nt$", NUCLEAR),
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
