"""Regenerate the README screenshots from live broker data.

    python docs/make_images.py

Run from the repository root with LASAIR_TOKEN set (or in .env) so all three
brokers appear. Output goes to docs/images/.
"""

from __future__ import annotations

import contextlib
import io
import re
import sys
from html import escape
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

import nucleuswatch as nw
from nucleuswatch import cli
from nucleuswatch.brokers import fink
from nucleuswatch.zooniverse import draw_stamps

OUT = Path(__file__).parent / "images"

# GitHub dark-ish terminal palette
BG, FG, DIM = "#0d1117", "#c9d1d9", "#8b949e"
GREEN, ORANGE, BLUE, YELLOW = "#3fb950", "#f0883e", "#58a6ff", "#d29922"
CHAR_W, LINE_H, PAD = 8.8, 19, 22


def run_cli(args: list[str]) -> str:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        cli.main(args)
    return buf.getvalue().strip("\n")


def _colour_line(line: str) -> str:
    """Escape one line and wrap keywords in coloured tspans."""
    text = escape(line)
    rules = [
        (r"\b(AGREE|agree)\b", GREEN),
        (r"\b(DISAGREE|disagree)\b", ORANGE),
        (r"\b(insufficient)\b", YELLOW),
        (r"\b(ZTF\d{2}[a-z]{7})\b", BLUE),
    ]
    for pattern, colour in rules:
        text = re.sub(pattern, rf'<tspan fill="{colour}">\1</tspan>', text)
    return text


def terminal_svg(command: str, output: str, path: Path, title: str) -> None:
    lines = [f"$ {command}", ""] + output.splitlines()
    width = int(max(len(line) for line in lines) * CHAR_W + 2 * PAD)
    height = int(len(lines) * LINE_H + 2 * PAD + 30)
    mono = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"
    sans = "-apple-system, Segoe UI, sans-serif"
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        f'<rect width="{width}" height="{height}" rx="8" fill="{BG}"/>',
        *(f'<circle cx="{18 + i * 18}" cy="15" r="5.5" fill="{c}"/>'
          for i, c in enumerate(("#ff5f56", "#ffbd2e", "#27c93f"))),
        f'<text x="{width / 2}" y="19" fill="{DIM}" font-family="{sans}" font-size="12" '
        + f'text-anchor="middle">{escape(title)}</text>',
        f'<g font-family="{mono}" font-size="14" fill="{FG}">',
    ]
    for i, line in enumerate(lines):
        y = 30 + PAD + (i + 0.8) * LINE_H
        if i == 0:
            body = f'<tspan fill="{GREEN}">$</tspan> {escape(command)}'
        else:
            body = _colour_line(line)
        # Non-breaking spaces keep table columns aligned in every SVG renderer.
        body = re.sub(r"(?<=>)([^<]+)|^([^<]+)", lambda m: m.group(0).replace(" ", "&#160;"), body)
        parts.append(f'<text x="{PAD}" y="{y:.0f}">{body}</text>')
    parts += ["</g>", "</svg>"]
    path.write_text("\n".join(parts))


def subject_preview(obj: nw.MergedObject, path: Path) -> None:
    """What a Zooniverse volunteer sees for one subject: both images side by side."""
    stamps = {k: fink.cutout(obj.object_id, k) for k in fink.CUTOUT_KINDS}
    fig = plt.figure(figsize=(13, 3.6))
    grid = fig.add_gridspec(1, 5, width_ratios=[2.4, 0.15, 1, 1, 1])
    obj.plot(ax=fig.add_subplot(grid[0]), annotate=False)
    draw_stamps([fig.add_subplot(grid[i]) for i in (2, 3, 4)], stamps)
    fig.suptitle("What volunteers see: blind light curve + New / Before / Difference stamps",
                 fontsize=11, x=0.01, ha="left")
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def annotated_lightcurve(obj: nw.MergedObject, path: Path) -> None:
    ax = obj.plot()
    ax.figure.set_size_inches(9, 4.6)
    ax.figure.tight_layout()
    ax.figure.savefig(path, dpi=130)
    plt.close(ax.figure)


def main() -> int:
    OUT.mkdir(exist_ok=True)

    nuclear = run_cli(["nuclear", "--days", "14", "--mag-max", "19"])
    terminal_svg("nucleuswatch nuclear --days 14 --mag-max 19", nuclear,
                 OUT / "cli_nuclear.svg", "nuclear transient search")

    obj_out = run_cli(["object", "ZTF21abfmbix"])
    terminal_svg("nucleuswatch object ZTF21abfmbix", obj_out, OUT / "cli_object.svg",
                 "one object, three brokers")

    search = nw.nuclear_transients(days=14)
    status = dict(zip(search.candidates["object_id"], search.candidates["consensus"]))
    # Show a disagreement if there is one: that is the interesting case.
    pick = next((o for o, s in status.items() if s == "disagree"), next(iter(status)))
    annotated_lightcurve(search.objects[pick], OUT / "lightcurve_annotated.png")
    subject_preview(search.objects[pick], OUT / "zooniverse_subject.png")
    print(f"wrote images to {OUT} (example object: {pick})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
