"""Common result type and column schema that every broker adapter returns."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import pandas as pd

# Every adapter produces tables with exactly these columns, so results from
# different brokers can be concatenated and compared directly.
# ``flux``/``flux_err`` are difference-image fluxes in nJy (LSST); ZTF adapters
# leave them empty. ``mag`` is empty when a flux is negative.
LIGHTCURVE_COLUMNS = ["mjd", "band", "mag", "mag_err", "flux", "flux_err", "candid", "broker"]
CLASSIFICATION_COLUMNS = ["broker", "classifier", "label", "probability", "version"]

# ZTF filter ids, shared by all brokers serving ZTF data.
ZTF_BANDS = {1: "g", 2: "r", 3: "i"}

JD_TO_MJD = 2400000.5

# LSST fluxes are in nJy; AB magnitude zero point for nJy.
NJY_ZEROPOINT = 31.4


def flux_to_mag(flux: float | None, flux_err: float | None) -> tuple[float | None, float | None]:
    """AB magnitude and error from a flux in nJy, or (None, None) if flux <= 0."""
    if flux is None or math.isnan(flux) or flux <= 0:
        return None, None
    mag = -2.5 * math.log10(flux) + NJY_ZEROPOINT
    err = 2.5 / math.log(10) * flux_err / flux if flux_err is not None else None
    return mag, err


@dataclass
class BrokerResult:
    """What one broker knows about one object."""

    broker: str
    object_id: str
    ra: float | None = None
    dec: float | None = None
    lightcurve: pd.DataFrame = field(
        default_factory=lambda: pd.DataFrame(columns=LIGHTCURVE_COLUMNS)
    )
    classifications: pd.DataFrame = field(
        default_factory=lambda: pd.DataFrame(columns=CLASSIFICATION_COLUMNS)
    )
    # Broker-specific extras worth surfacing (crossmatches, TNS name, ...).
    extra: dict = field(default_factory=dict)
    url: str | None = None

    @property
    def ndet(self) -> int:
        return len(self.lightcurve)


def lightcurve_frame(rows: list[dict], broker: str) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=LIGHTCURVE_COLUMNS[:-1])
    df["broker"] = broker
    numeric = ["mjd", "mag", "mag_err", "flux", "flux_err"]
    df[numeric] = df[numeric].apply(pd.to_numeric, errors="coerce")
    df = df.dropna(subset=["mjd"])
    df = df[df["mag"].notna() | df["flux"].notna()]
    df["candid"] = df["candid"].astype("string")
    return df.sort_values("mjd").reset_index(drop=True)


def classification_frame(rows: list[dict], broker: str) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=CLASSIFICATION_COLUMNS[1:])
    df.insert(0, "broker", broker)
    return df
