from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .core import MergedObject

BAND_COLORS = {"u": "#3b6fb6", "g": "#2a9d8f", "r": "#e76f51", "i": "#8d6cab", "z": "#9c6b30", "y": "#555555"}
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
    # LSST difference fluxes can be negative (no magnitude), so plot flux.
    use_flux = obj.survey == "lsst"
    y, yerr = ("flux", "flux_err") if use_flux else ("mag", "mag_err")
    scale = 1e-3 if use_flux else 1.0  # nJy -> µJy
    ylabel = "Difference flux (µJy)" if use_flux else None

    if annotate:
        from matplotlib.lines import Line2D

        # Brokers usually report the very same alerts, so their points would sit
        # exactly on top of each other. Nudge each broker slightly in time.
        brokers = sorted(lc["broker"].unique())
        span = (lc["mjd"].max() - lc["mjd"].min()) or 1.0
        step = span * 0.012
        shift = {b: (i - (len(brokers) - 1) / 2) * step for i, b in enumerate(brokers)}
        for (broker, band), grp in lc.groupby(["broker", "band"]):
            colour = BAND_COLORS.get(band, "grey")
            ax.errorbar(
                grp["mjd"] + shift[broker], grp[y] * scale, yerr=grp[yerr] * scale,
                fmt=BROKER_MARKERS.get(broker, "D"), color=colour,
                mfc=colour if broker == brokers[0] else "none",
                ms=6, capsize=0, lw=0.8,
            )
        ax.set_xlabel("MJD" + (f"  (brokers offset by ±{step * 24:.1f} h for visibility)"
                               if len(brokers) > 1 else ""))
        band_handles = [Line2D([], [], ls="", marker="o", color=BAND_COLORS.get(b, "grey"), label=f"{b} band")
                        for b in sorted(lc["band"].dropna().unique())]
        broker_handles = [Line2D([], [], ls="", marker=BROKER_MARKERS.get(b, "D"), color="#555",
                                 mfc="#555" if b == brokers[0] else "none", label=b) for b in brokers]
        first = ax.legend(handles=broker_handles, title="broker", fontsize=8, title_fontsize=8,
                          frameon=False, loc="upper left")
        ax.add_artist(first)
        ax.legend(handles=band_handles, fontsize=8, frameon=False, loc="lower right")
        c = obj.consensus()
        votes = ", ".join(f"{v['broker']}:{v['label']}" for v in c["votes"])
        ax.set_title(f"{obj.object_id} — brokers {c['status']}\n{votes}", fontsize=10)
        _finish_axis(ax, use_flux, ylabel or "PSF magnitude")
        return ax

    # Volunteer view. The same alert seen by several brokers is plotted once.
    lc = lc.drop_duplicates("candid") if lc["candid"].notna().all() else lc
    t0 = lc["mjd"].min()
    for band, grp in lc.groupby("band"):
        ax.errorbar(
            grp["mjd"] - t0, grp[y] * scale, yerr=grp[yerr] * scale, fmt="o",
            color=BAND_COLORS.get(band, "grey"), ms=6, capsize=0, lw=0.8, label=f"{band} band",
        )
    ax.set_xlabel("Days since first detection")
    _finish_axis(ax, use_flux, ylabel or "Magnitude (brighter ↑)")
    ax.legend(fontsize=8, frameon=False)
    return ax


def _finish_axis(ax, use_flux: bool, ylabel: str) -> None:
    if use_flux:
        ax.axhline(0, color="#999", lw=0.6, zorder=0)
    else:
        ax.invert_yaxis()
    ax.set_ylabel(ylabel)
