"""Shared figure style for the validation and paper figures.

Vector PDF for the paper, 300-dpi PNG for the web. Widths follow AAS/MNRAS
column sizes. Colours are the Okabe-Ito colourblind-safe palette.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

FIGDIR = Path(__file__).parent / "figures"
SINGLE, DOUBLE = 3.4, 7.1  # inches

COLOURS = {
    "TDE": "#D55E00",      # vermillion
    "SN Ia": "#0072B2",    # blue
    "SN II": "#009E73",    # green
    "AGN/QSO": "#CC79A7",  # purple
    "SN IIn": "#E69F00",   # orange
    "SN Ibc": "#56B4E9",   # sky blue
    "SLSN": "#F0E442",     # yellow
    "other": "#7f7f7f",
}


def setup() -> None:
    plt.rcParams.update({
        "font.family": "serif",
        "font.serif": ["STIXGeneral", "DejaVu Serif"],
        "mathtext.fontset": "stix",
        "font.size": 9,
        "axes.labelsize": 9,
        "axes.titlesize": 9,
        "legend.fontsize": 8,
        "xtick.labelsize": 8,
        "ytick.labelsize": 8,
        "axes.linewidth": 0.7,
        "xtick.direction": "in",
        "ytick.direction": "in",
        "xtick.top": True,
        "ytick.right": True,
        "xtick.minor.visible": True,
        "ytick.minor.visible": True,
        "legend.frameon": False,
        "savefig.bbox": "tight",
        "savefig.pad_inches": 0.02,
        "pdf.fonttype": 42,  # embed TrueType so journals accept the PDF
    })


def save(fig, name: str) -> list[Path]:
    FIGDIR.mkdir(exist_ok=True)
    paths = [FIGDIR / f"{name}.pdf", FIGDIR / f"{name}.png"]
    fig.savefig(paths[0])
    fig.savefig(paths[1], dpi=300)
    plt.close(fig)
    return paths


def wilson(k: int, n: int, z: float = 1.0) -> tuple[float, float, float]:
    """Fraction and Wilson score interval (z=1: 68%)."""
    if n == 0:
        return float("nan"), float("nan"), float("nan")
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / denom
    return p, centre - half, centre + half
