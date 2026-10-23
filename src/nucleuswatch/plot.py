from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .core import MergedObject

BAND_COLORS = {"g": "#2a9d8f", "r": "#e76f51", "i": "#8d6cab"}
BROKER_MARKERS = {"alerce": "o", "fink": "s", "lasair": "^"}


def plot_object(obj: MergedObject, ax=None, *, annotate: bool = True):
    """Merged light curve.

    ``annotate=False`` gives a "blind" plot for volunteers: no object id, no
    broker labels, one point per alert, time relative to first detection.
    """
    import matplotlib.pyplot as plt

    if ax is None:
        _, ax = plt.subplots(figsize=(8, 4.5))

    lc = obj.lightcurve
    if annotate:
        for (broker, band), grp in lc.groupby(["broker", "band"]):
            ax.errorbar(
                grp["mjd"], grp["mag"], yerr=grp["mag_err"],
                fmt=BROKER_MARKERS.get(broker, "D"), color=BAND_COLORS.get(band, "grey"),
                mfc="none" if broker != "alerce" else BAND_COLORS.get(band, "grey"),
                ms=6, capsize=0, lw=0.8, label=f"{broker} {band}",
            )
        ax.set_xlabel("MJD")
        c = obj.consensus()
        votes = ", ".join(f"{v['broker']}:{v['label']}" for v in c["votes"])
        ax.set_title(f"{obj.object_id} — brokers {c['status']}\n{votes}", fontsize=10)
    else:
        # Same alert seen by several brokers -> plot it once.
        lc = lc.drop_duplicates("candid") if lc["candid"].notna().all() else lc
        t0 = lc["mjd"].min()
        for band, grp in lc.groupby("band"):
            ax.errorbar(
                grp["mjd"] - t0, grp["mag"], yerr=grp["mag_err"], fmt="o",
                color=BAND_COLORS.get(band, "grey"), ms=6, capsize=0, lw=0.8, label=f"{band} band",
            )
        ax.set_xlabel("Days since first detection")

    ax.invert_yaxis()
    ax.set_ylabel("Magnitude (brighter ↑)" if not annotate else "PSF magnitude")
    ax.legend(fontsize=8, ncol=2, frameon=False)
    return ax
