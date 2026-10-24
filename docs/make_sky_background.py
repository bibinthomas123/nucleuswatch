"""Re-project ESA's Gaia all-sky map into the equatorial frame used by the sky map.

    python docs/make_sky_background.py path/to/gaia_edr3_sky.jpg

Source: "The colour of the sky from Gaia's Early Data Release 3", ESA/Gaia/DPAC,
CC BY-SA 3.0 IGO (8000x4000, Hammer-Aitoff projection in Galactic coordinates,
l = 0 at the centre and increasing to the left):
https://www.esa.int/ESA_Multimedia/Images/2020/12/The_colour_of_the_sky_from_Gaia_s_Early_Data_Release_3

Output: src/nucleuswatch/assets/sky_equatorial.jpg, a plate carree map in
ICRS with RA increasing to the LEFT (360 at the left edge, 0 at the right) and
Dec from +90 (top) to -90 (bottom), the same orientation as the page's sky map.
The derived image is shared under the same licence, with the same credit.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image

OUT = Path(__file__).resolve().parents[1] / "src" / "nucleuswatch" / "assets" / "sky_equatorial.jpg"
WIDTH, HEIGHT = 1440, 720

# ICRS -> Galactic rotation matrix (Hipparcos definition, as used by astropy).
ICRS_TO_GAL = np.array([
    [-0.0548755604162154, -0.8734370902348850, -0.4838350155487132],
    [+0.4941094278755837, -0.4448296299600112, +0.7469822444972189],
    [-0.8676661490190047, -0.1980763734312015, +0.4559837761750669],
])


def equatorial_grid(width: int, height: int) -> tuple[np.ndarray, np.ndarray]:
    """RA, Dec (radians) at pixel centres; RA runs 360 -> 0 from left to right."""
    ra = np.radians(360.0 - (np.arange(width) + 0.5) / width * 360.0)
    dec = np.radians(90.0 - (np.arange(height) + 0.5) / height * 180.0)
    return np.meshgrid(ra, dec)


def to_galactic(ra: np.ndarray, dec: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    v = np.stack([np.cos(dec) * np.cos(ra), np.cos(dec) * np.sin(ra), np.sin(dec)])
    g = np.tensordot(ICRS_TO_GAL, v, axes=1)
    return np.arctan2(g[1], g[0]), np.arcsin(np.clip(g[2], -1, 1))


def hammer_aitoff(l: np.ndarray, b: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Projected x in [-2√2, 2√2], y in [-√2, √2]; l in (-π, π], 0 at centre."""
    l = (l + np.pi) % (2 * np.pi) - np.pi
    d = np.sqrt(1 + np.cos(b) * np.cos(l / 2))
    return 2 * np.sqrt(2) * np.cos(b) * np.sin(l / 2) / d, np.sqrt(2) * np.sin(b) / d


def sample_bilinear(img: np.ndarray, u: np.ndarray, v: np.ndarray) -> np.ndarray:
    h, w = img.shape[:2]
    u = np.clip(u, 0, w - 1.001)
    v = np.clip(v, 0, h - 1.001)
    u0, v0 = np.floor(u).astype(int), np.floor(v).astype(int)
    du, dv = (u - u0)[..., None], (v - v0)[..., None]
    top = img[v0, u0] * (1 - du) + img[v0, u0 + 1] * du
    bottom = img[v0 + 1, u0] * (1 - du) + img[v0 + 1, u0 + 1] * du
    return top * (1 - dv) + bottom * dv


def main(src: str) -> int:
    Image.MAX_IMAGE_PIXELS = None  # the source is a trusted 32-megapixel ESA image
    img = np.asarray(Image.open(src).convert("RGB"), dtype=np.float32)
    h, w = img.shape[:2]
    ra, dec = equatorial_grid(WIDTH, HEIGHT)
    l, b = to_galactic(ra, dec)
    x, y = hammer_aitoff(l, b)
    # l increases to the LEFT in the Gaia image, so x maps to columns mirrored.
    u = (0.5 - x / (4 * np.sqrt(2))) * w - 0.5
    v = (0.5 - y / (2 * np.sqrt(2))) * h - 0.5
    out = sample_bilinear(img, u, v)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.clip(out, 0, 255).astype(np.uint8)).save(OUT, quality=86, optimize=True, progressive=True)
    print(f"wrote {OUT} ({OUT.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    raise SystemExit(main(sys.argv[1]))
