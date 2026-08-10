"""Turn art drawn on a black background into correct straight-alpha RGBA.

The source sheets are composited over black, so every edge pixel is
`coverage * colour`. Giving those pixels alpha 255 (what a plain threshold does)
bakes the black in, and the cat grows a dark speckled fringe on light desktops.

Here the edge ring's coverage is recovered from its brightness relative to the
solid pixels beside it, and the colour is un-premultiplied by that coverage, so
`alpha*colour + (1-alpha)*background` lands on the right result over any backdrop.
"""

from __future__ import annotations

import numpy as np
from scipy import ndimage

# Neighbourhood used to find "what a fully covered pixel looks like here".
REF_SIZE = 7


def add_outline(
    rgba: np.ndarray,
    solid: np.ndarray | None = None,
    colour=(46, 34, 30),
    alpha: int = 235,
) -> np.ndarray:
    """Draw a uniform 1px outline around the silhouette.

    The sheets outline dark fur heavily but leave the white chest and paws with a
    barely-there edge, so the cat looked half-outlined on a light desktop. One ring
    at a constant weight makes every frame and every animation read the same.

    The array must already have a 1px transparent margin to draw into.
    """
    # Follow the keyed silhouette when given one: thresholding alpha instead picks up
    # faint fringe pixels and outlines them into visible dots beside the cat.
    if solid is None:
        solid = rgba[..., 3] > 128
    ring = ndimage.binary_dilation(solid, np.ones((3, 3))) & ~solid
    out = rgba.copy()
    out[ring, 0], out[ring, 1], out[ring, 2] = colour
    out[ring, 3] = np.maximum(out[ring, 3], alpha)
    return out


def straight_alpha(rgb: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """RGBA uint8 array for `rgb` (over black) keyed by boolean `mask`."""
    rgb = rgb.astype(float)
    interior = ndimage.binary_erosion(mask, np.ones((3, 3)))
    lum = rgb.max(axis=2)

    # Brightest solid pixel nearby — the reference for full coverage.
    ref = ndimage.maximum_filter(np.where(interior, lum, 0.0), size=REF_SIZE)
    # Thin features (whiskers, tail tips) have no interior; treat them as solid.
    ref = np.maximum(ref, lum)
    coverage = np.clip(lum / np.maximum(ref, 1.0), 0.0, 1.0)

    alpha = np.where(interior, 1.0, np.where(mask, coverage, 0.0))
    colour = np.clip(rgb / np.maximum(alpha[..., None], 1e-3), 0, 255)
    out = np.dstack([colour, alpha * 255.0])
    out[alpha == 0] = 0
    # 1px margin so the outline ring has somewhere to go
    out = np.pad(out, ((1, 1), (1, 1), (0, 0)))
    inner = np.pad(ndimage.binary_erosion(mask, np.ones((3, 3))), 1)
    return add_outline(out.astype(np.uint8), inner)
