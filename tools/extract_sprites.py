#!/usr/bin/env python3
"""Extract individual sprite frames from Desk-Paw grey-cat reference sheets.

Uses flood-fill background removal (preserving white outlines) and gap-based
row/column detection rather than fixed grid rectangles.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "assets" / "sprites"
DEFAULT_CANVAS = 128
DEFAULT_PAD = 4
BG_THR = 248

# Source sheet → ordered row specs.
# Each row is either a single (action, direction) with N frames,
# or a side-by-side pair when the sheet splits LEFT/RIGHT halves.
SHEET_CONFIG: dict[str, list[dict]] = {
    "ChatGPT Image Aug 11, 2026 at 01_44_38 PM.png": [
        {"action": "idle", "direction": "r", "expected": 5},
        {"action": "idle", "direction": "l", "expected": 5},
        {"action": "walk", "direction": "r", "expected": 8},
        {"action": "walk", "direction": "l", "expected": 8},
    ],
    "ChatGPT Image Aug 11, 2026 at 01_44_42 PM.png": [
        {"action": "run", "direction": "r", "expected": 8},
        {"action": "run", "direction": "l", "expected": 8},
        {"action": "climb", "direction": "r", "expected": 7},
        {"action": "climb", "direction": "l", "expected": 7},
    ],
    # Prefer the jump sheet that includes the wooden ledge prop on jump_down.
    "ChatGPT Image Aug 11, 2026 at 01_44_48 PM.png": [
        {"action": "jump_up", "direction": "r", "expected": 6},
        {"action": "jump_up", "direction": "l", "expected": 6},
        {"action": "jump_down", "direction": "r", "expected": 6},
        {"action": "jump_down", "direction": "l", "expected": 6},
    ],
    # Alternate jump sheet (no ledge) — skipped by default to avoid overwrite.
    "ChatGPT Image Aug 11, 2026 at 01_44_49 PM.png": [
        {
            "action": "jump_up",
            "direction": "r",
            "expected": 6,
            "skip": True,
            "note": "alternate jump sheet; use 01_44_48",
        },
        {"action": "jump_up", "direction": "l", "expected": 6, "skip": True},
        {"action": "jump_down", "direction": "r", "expected": 6, "skip": True},
        {"action": "jump_down", "direction": "l", "expected": 6, "skip": True},
    ],
    # Rows are 6 right-facing + 6 left-facing side by side.
    "ChatGPT Image Aug 11, 2026 at 01_44_50 PM.png": [
        {
            "action": "type",
            "split": "halves",
            "expected_right": 6,
            "expected_left": 6,
        },
        {
            "action": "focus",  # sheet labels say "study"
            "split": "halves",
            "expected_right": 6,
            "expected_left": 6,
        },
        {
            "action": "think",
            "split": "halves",
            "expected_right": 6,
            "expected_left": 6,
        },
        {
            "action": "relax",
            "split": "halves",
            "expected_right": 6,
            "expected_left": 6,
        },
    ],
    "ChatGPT Image Aug 11, 2026 at 01_44_53 PM.png": [
        {
            "action": "calm",
            "split": "halves",
            "expected_right": 6,
            "expected_left": 6,
        },
        {
            "action": "angry",
            "split": "halves",
            "expected_right": 6,
            "expected_left": 6,
        },
        {
            "action": "stretch",
            "split": "halves",
            "expected_right": 5,
            "expected_left": 6,
        },
        {
            "action": "angry_type",
            "split": "halves",
            "expected_right": 6,
            "expected_left": 6,
        },
    ],
}


@dataclass
class FrameMeta:
    action: str
    direction: str
    frame_index: int
    filename: str
    source_image: str
    bbox: list[int]  # x0,y0,x1,y1 in source
    output_dimensions: list[int]
    scaled: bool
    scale: float = 1.0


@dataclass
class ExtractionResult:
    frames: list[FrameMeta] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def flood_background(rgb: np.ndarray, thr: int = BG_THR) -> np.ndarray:
    """Return boolean mask of sheet background (True = background).

    Flood-fills from image edges through near-white pixels so the cat's white
    outline (enclosed / non-edge-connected) stays foreground.
    """
    h, w = rgb.shape[:2]
    nearly = np.all(rgb >= thr, axis=2)
    # cv2.floodFill needs a single-channel image; seed from a synthetic mask.
    seed_img = nearly.astype(np.uint8) * 255
    mask = np.zeros((h + 2, w + 2), np.uint8)

    # Seed along all borders that are near-white.
    for x in range(w):
        if nearly[0, x]:
            cv2.floodFill(seed_img, mask, (x, 0), 128, loDiff=0, upDiff=0, flags=4)
        if nearly[h - 1, x]:
            cv2.floodFill(seed_img, mask, (x, h - 1), 128, loDiff=0, upDiff=0, flags=4)
    for y in range(h):
        if nearly[y, 0]:
            cv2.floodFill(seed_img, mask, (0, y), 128, loDiff=0, upDiff=0, flags=4)
        if nearly[y, w - 1]:
            cv2.floodFill(seed_img, mask, (w - 1, y), 128, loDiff=0, upDiff=0, flags=4)

    # mask[1:-1,1:-1] == 1 where floodFill visited
    return mask[1:-1, 1:-1] > 0


def remove_dividers_and_text(rgb: np.ndarray, bg: np.ndarray) -> np.ndarray:
    """Strip grey divider lines and dark text labels from the foreground mask."""
    h, w = rgb.shape[:2]
    fg = (~bg).copy()

    # Long near-grey horizontal dividers
    for y in range(h):
        row = fg[y]
        n = int(row.sum())
        if n < w * 0.45:
            continue
        pix = rgb[y, row]
        mean = pix.mean(axis=0)
        if (
            mean.mean() > 175
            and abs(float(mean[0] - mean[2])) < 25
            and pix.std() < 45
        ):
            fg[y] = False

    # Long near-grey vertical dividers
    for x in range(w):
        col = fg[:, x]
        n = int(col.sum())
        if n < h * 0.35:
            continue
        pix = rgb[col, x]
        mean = pix.mean(axis=0)
        if (
            mean.mean() > 175
            and abs(float(mean[0] - mean[2])) < 25
            and pix.std() < 45
        ):
            fg[:, x] = False

    fgu = fg.astype(np.uint8) * 255
    num, labels, stats, _ = cv2.connectedComponentsWithStats(fgu, connectivity=8)
    keep = np.zeros((h, w), dtype=bool)
    for i in range(1, num):
        area = int(stats[i, cv2.CC_STAT_AREA])
        bw = int(stats[i, cv2.CC_STAT_WIDTH])
        bh = int(stats[i, cv2.CC_STAT_HEIGHT])
        comp = labels == i
        mean = rgb[comp].mean(axis=0)
        brightness = float(mean.mean())

        is_text = (brightness < 95 and bh <= 36 and area < 6000) or (
            brightness < 70 and bh <= 42 and area < 9000
        )
        is_line = (bh <= 3 and bw > 80) or (bw <= 3 and bh > 80)
        is_junk = (bw > w * 0.75 and area < 25000) or (
            bh > h * 0.75 and area < 25000
        )
        # Tiny speckles
        if area < 8:
            continue
        if is_text or is_line or is_junk:
            continue
        keep[comp] = True
    return keep


def find_content_rows(
    fg: np.ndarray, min_row_h: int = 40, proj_thr: int = 20
) -> list[tuple[int, int]]:
    hproj = fg.sum(axis=1)
    rows: list[tuple[int, int]] = []
    in_row = False
    start = 0
    h = len(hproj)
    for y, v in enumerate(hproj):
        if v > proj_thr and not in_row:
            in_row = True
            start = y
        elif v <= proj_thr and in_row:
            if y - start >= min_row_h:
                rows.append((start, y))
            in_row = False
    if in_row and h - start >= min_row_h:
        rows.append((start, h))
    return rows


def split_columns(
    fg_band: np.ndarray, min_w: int = 28, proj_thr: int = 10
) -> list[tuple[int, int]]:
    vproj = fg_band.sum(axis=0)
    cols: list[tuple[int, int]] = []
    in_c = False
    start = 0
    w = len(vproj)
    for x, v in enumerate(vproj):
        if v > proj_thr and not in_c:
            in_c = True
            start = x
        elif v <= proj_thr and in_c:
            if x - start >= min_w:
                cols.append((start, x))
            in_c = False
    if in_c and w - start >= min_w:
        cols.append((start, w))
    return cols


def tight_bbox(mask: np.ndarray) -> tuple[int, int, int, int] | None:
    ys, xs = np.where(mask)
    if len(xs) == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def clean_outer_white_halo(
    rgba: np.ndarray,
    white_thr: int = 205,
    fringe_thr: int = 115,
    fringe_depth: float = 3.0,
    thin_white_depth: float = 6.0,
    always_white_depth: float = 2.5,
) -> np.ndarray:
    """Remove outer white sticker halo / AA fringe; keep interior whites.

    Walks from transparency through light fringe pixels, but:
    - stops at dark outlines
    - only removes thin near-white (not solid fills like thought bubbles)
    - limits mid-grey walking to the outer shell so fur/eyes stay intact
    """
    from collections import deque

    out = rgba.copy()
    a = out[:, :, 3] > 0
    rgb = out[:, :, :3].astype(np.int16)
    mn = rgb.min(axis=2)
    mx = rgb.max(axis=2)
    low = (mx - mn) <= 40
    dark = a & (mx <= 75)
    dist = cv2.distanceTransform(a.astype(np.uint8), cv2.DIST_L2, 3)

    whiteish = a & low & (mn >= white_thr) & ~dark
    core = (
        cv2.erode(whiteish.astype(np.uint8), np.ones((3, 3), np.uint8), iterations=1)
        > 0
    )
    thin_white = whiteish & ~core
    removable_white = (
        (thin_white & (dist <= thin_white_depth))
        | (whiteish & (dist <= always_white_depth))
    ) & ~core

    fringe = (
        a
        & low
        & (mn >= fringe_thr)
        & (mn < white_thr)
        & ~dark
        & (dist <= fringe_depth)
    )
    walk = removable_white | fringe

    pad_t = np.pad((~a).astype(np.uint8), 1, constant_values=1)
    touch = walk & (
        (pad_t[1:-1, 2:] > 0)
        | (pad_t[1:-1, :-2] > 0)
        | (pad_t[2:, 1:-1] > 0)
        | (pad_t[:-2, 1:-1] > 0)
        | (pad_t[2:, 2:] > 0)
        | (pad_t[2:, :-2] > 0)
        | (pad_t[:-2, 2:] > 0)
        | (pad_t[:-2, :-2] > 0)
    )
    visited = np.zeros(a.shape, dtype=bool)
    q = deque()
    for y, x in zip(*np.where(touch)):
        visited[y, x] = True
        q.append((x, y))
    while q:
        x, y = q.popleft()
        for dx, dy in (
            (1, 0),
            (-1, 0),
            (0, 1),
            (0, -1),
            (1, 1),
            (1, -1),
            (-1, 1),
            (-1, -1),
        ):
            nx, ny = x + dx, y + dy
            if (
                0 <= nx < out.shape[1]
                and 0 <= ny < out.shape[0]
                and not visited[ny, nx]
                and walk[ny, nx]
            ):
                visited[ny, nx] = True
                q.append((nx, ny))
    out[visited, 3] = 0

    # Single follow-up peel of mid-grey AA left on the new silhouette edge.
    a = out[:, :, 3] > 0
    rgb = out[:, :, :3].astype(np.int16)
    mn = rgb.min(axis=2)
    mx = rgb.max(axis=2)
    dark = a & (mx <= 75)
    pad_a = np.pad(a.astype(np.uint8), 1, constant_values=0)
    pad_d = np.pad(dark.astype(np.uint8), 1, constant_values=0)
    border = a & (
        (pad_a[1:-1, 2:] == 0)
        | (pad_a[1:-1, :-2] == 0)
        | (pad_a[2:, 1:-1] == 0)
        | (pad_a[:-2, 1:-1] == 0)
        | (pad_a[2:, 2:] == 0)
        | (pad_a[2:, :-2] == 0)
        | (pad_a[:-2, 2:] == 0)
        | (pad_a[:-2, :-2] == 0)
    )
    near_dark = (
        (pad_d[1:-1, 2:] > 0)
        | (pad_d[1:-1, :-2] > 0)
        | (pad_d[2:, 1:-1] > 0)
        | (pad_d[:-2, 1:-1] > 0)
        | (pad_d[2:, 2:] > 0)
        | (pad_d[2:, :-2] > 0)
        | (pad_d[:-2, 2:] > 0)
        | (pad_d[:-2, :-2] > 0)
    )
    rem = border & near_dark & (mn >= 110) & ((mx - mn) <= 40)
    out[rem, 3] = 0
    return out


def extract_rgba_crop(
    rgb: np.ndarray, bg: np.ndarray, bbox: tuple[int, int, int, int], pad: int
) -> tuple[Image.Image, tuple[int, int, int, int]]:
    """Crop sprite with padding; background → transparent, then strip outer white halo."""
    h, w = rgb.shape[:2]
    x0, y0, x1, y1 = bbox
    x0 = max(0, x0 - pad)
    y0 = max(0, y0 - pad)
    x1 = min(w, x1 + pad)
    y1 = min(h, y1 + pad)

    crop_rgb = rgb[y0:y1, x0:x1]
    crop_bg = bg[y0:y1, x0:x1]
    alpha = np.where(crop_bg, 0, 255).astype(np.uint8)
    rgba = np.dstack([crop_rgb, alpha])
    rgba = clean_outer_white_halo(rgba)
    return Image.fromarray(rgba, "RGBA"), (x0, y0, x1, y1)


def place_on_canvas(
    sprite: Image.Image,
    canvas: int,
    baseline_from_bottom: int | None = None,
    group_scale: float | None = None,
) -> tuple[Image.Image, bool, float]:
    """Center (or baseline-align) sprite on transparent canvas; NN scale if needed."""
    sw, sh = sprite.size
    scale = 1.0 if group_scale is None else group_scale
    scaled = scale < 1.0 - 1e-9
    out = sprite
    if scaled:
        nw = max(1, int(round(sw * scale)))
        nh = max(1, int(round(sh * scale)))
        out = sprite.resize((nw, nh), Image.Resampling.NEAREST)
        sw, sh = nw, nh
    elif sw > canvas - 2 or sh > canvas - 2:
        scale = min((canvas - 2) / sw, (canvas - 2) / sh)
        nw = max(1, int(round(sw * scale)))
        nh = max(1, int(round(sh * scale)))
        out = sprite.resize((nw, nh), Image.Resampling.NEAREST)
        scaled = True
        sw, sh = nw, nh

    canvas_img = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    x = (canvas - sw) // 2
    if baseline_from_bottom is None:
        y = (canvas - sh) // 2
    else:
        y = canvas - baseline_from_bottom - sh
        y = max(0, min(y, canvas - sh))
    canvas_img.paste(out, (x, y), out)
    return canvas_img, scaled, scale


# kept for API completeness; group placement in process_sheet is preferred.


def detect_frames_in_band(
    fg: np.ndarray, y0: int, y1: int, x0: int = 0, x1: int | None = None
) -> list[tuple[int, int, int, int]]:
    """Return tight bboxes (absolute coords) for each frame in a horizontal band."""
    if x1 is None:
        x1 = fg.shape[1]
    band = fg[y0:y1, x0:x1]
    cols = split_columns(band)
    boxes: list[tuple[int, int, int, int]] = []
    for cx0, cx1 in cols:
        cell = band[:, cx0:cx1]
        tb = tight_bbox(cell)
        if tb is None:
            continue
        lx0, ly0, lx1, ly1 = tb
        boxes.append((x0 + cx0 + lx0, y0 + ly0, x0 + cx0 + lx1, y0 + ly1))
    return boxes


def process_sheet(
    path: Path,
    row_specs: list[dict],
    out_dir: Path,
    canvas: int,
    pad: int,
    bg_thr: int,
    include_skipped: bool = False,
) -> ExtractionResult:
    result = ExtractionResult()
    bgr = cv2.imread(str(path))
    if bgr is None:
        result.warnings.append(f"Failed to read {path}")
        return result
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    bg = flood_background(rgb, thr=bg_thr)
    fg = remove_dividers_and_text(rgb, bg)
    rows = find_content_rows(fg)

    active_specs = [
        s for s in row_specs if include_skipped or not s.get("skip")
    ]
    if not active_specs:
        # Intentionally skipped sheet — not a failure.
        return result

    if len(rows) != len(row_specs):
        result.warnings.append(
            f"{path.name}: detected {len(rows)} content rows, "
            f"config has {len(row_specs)} specs"
        )

    # Pair detected rows with full config (including skipped) by index.
    # Collect raw RGBA crops grouped by (action, direction) for baseline align.
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)

    for i, spec in enumerate(row_specs):
        if i >= len(rows):
            if not spec.get("skip"):
                result.warnings.append(
                    f"{path.name}: missing detected row for {spec}"
                )
            continue
        y0, y1 = rows[i]
        if spec.get("skip") and not include_skipped:
            continue

        if spec.get("split") == "halves":
            mid = rgb.shape[1] // 2
            right_boxes = detect_frames_in_band(fg, y0, y1, 0, mid)
            left_boxes = detect_frames_in_band(fg, y0, y1, mid, rgb.shape[1])
            er, el = spec.get("expected_right"), spec.get("expected_left")
            if er is not None and len(right_boxes) != er:
                result.warnings.append(
                    f"{path.name} row{i} {spec['action']}_r: "
                    f"expected {er}, got {len(right_boxes)}"
                )
            if el is not None and len(left_boxes) != el:
                result.warnings.append(
                    f"{path.name} row{i} {spec['action']}_l: "
                    f"expected {el}, got {len(left_boxes)}"
                )
            for bi, box in enumerate(right_boxes):
                groups[(spec["action"], "r")].append(
                    {"box": box, "index": bi + 1, "source": path.name}
                )
            for bi, box in enumerate(left_boxes):
                groups[(spec["action"], "l")].append(
                    {"box": box, "index": bi + 1, "source": path.name}
                )
        else:
            boxes = detect_frames_in_band(fg, y0, y1)
            expected = spec.get("expected")
            if expected is not None and len(boxes) != expected:
                result.warnings.append(
                    f"{path.name} row{i} {spec['action']}_{spec['direction']}: "
                    f"expected {expected}, got {len(boxes)}"
                )
            for bi, box in enumerate(boxes):
                groups[(spec["action"], spec["direction"])].append(
                    {"box": box, "index": bi + 1, "source": path.name}
                )

    # Emit frames with shared baseline per action+direction
    for (action, direction), items in sorted(groups.items()):
        action_dir = out_dir / action
        action_dir.mkdir(parents=True, exist_ok=True)

        crops: list[tuple[Image.Image, dict]] = []
        for item in items:
            img, used_box = extract_rgba_crop(rgb, bg, item["box"], pad=pad)
            # Re-trim fully transparent margins after padding
            arr = np.array(img)
            alpha = arr[:, :, 3]
            tb = tight_bbox(alpha > 0)
            if tb is not None:
                img = img.crop(tb)
                tx0, ty0, tx1, ty1 = tb
                ux0, uy0, _, _ = used_box
                used_box = (ux0 + tx0, uy0 + ty0, ux0 + tx1, uy0 + ty1)
            crops.append((img, {**item, "used_box": used_box}))

        if not crops:
            continue

        # Shared baseline + uniform scale across the action/direction group.
        gw = max(c.size[0] for c, _ in crops)
        gh = max(c.size[1] for c, _ in crops)
        gscale = min(1.0, (canvas - 2) / max(gw, 1), (canvas - 2) / max(gh, 1))
        scaled_h_max = int(round(gh * gscale))
        baseline = max(2, (canvas - scaled_h_max) // 2)

        for img, item in crops:
            if gscale < 1.0 - 1e-9:
                nw = max(1, int(round(img.size[0] * gscale)))
                nh = max(1, int(round(img.size[1] * gscale)))
                scaled_img = img.resize((nw, nh), Image.Resampling.NEAREST)
                scaled = True
                scale = gscale
            else:
                scaled_img = img
                scaled = False
                scale = 1.0
                nw, nh = img.size

            placed = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
            x = (canvas - nw) // 2
            y = canvas - baseline - nh
            y = max(0, min(y, canvas - nh))
            placed.paste(scaled_img, (x, y), scaled_img)
            # Final pass on the normalized canvas (catches residual fringe after scale).
            placed = Image.fromarray(clean_outer_white_halo(np.array(placed)))

            fname = f"{action}_{direction}_{item['index']:02d}.png"
            out_path = action_dir / fname
            placed.save(out_path, "PNG")
            meta = FrameMeta(
                action=action,
                direction=direction,
                frame_index=item["index"],
                filename=fname,
                source_image=item["source"],
                bbox=list(item["used_box"]),
                output_dimensions=[canvas, canvas],
                scaled=scaled,
                scale=round(scale, 4),
            )
            result.frames.append(meta)

    # Diagnostic overlay
    write_detection_preview(rgb, fg, rows, out_dir / "_previews" / f"detect_{path.stem}.png")
    return result


def write_detection_preview(
    rgb: np.ndarray, fg: np.ndarray, rows: list[tuple[int, int]], path: Path
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    vis = rgb.copy()
    # tint non-fg faintly
    overlay = vis.copy()
    overlay[~fg] = (overlay[~fg] * 0.55 + np.array([220, 220, 220]) * 0.45).astype(
        np.uint8
    )
    for y0, y1 in rows:
        boxes = detect_frames_in_band(fg, y0, y1)
        cv2.rectangle(overlay, (0, y0), (rgb.shape[1] - 1, y1), (0, 180, 0), 1)
        for box in boxes:
            x0, yy0, x1, yy1 = box
            cv2.rectangle(overlay, (x0, yy0), (x1 - 1, yy1 - 1), (220, 40, 40), 2)
    Image.fromarray(overlay).save(path)


def checkerboard(size: tuple[int, int], cell: int = 8) -> Image.Image:
    w, h = size
    img = Image.new("RGB", (w, h), (200, 200, 200))
    draw = ImageDraw.Draw(img)
    for y in range(0, h, cell):
        for x in range(0, w, cell):
            if ((x // cell) + (y // cell)) % 2 == 0:
                draw.rectangle([x, y, x + cell - 1, y + cell - 1], fill=(240, 240, 240))
    return img


def write_action_preview(
    action: str, frames: list[FrameMeta], sprites_dir: Path, preview_dir: Path
) -> None:
    preview_dir.mkdir(parents=True, exist_ok=True)
    by_dir: dict[str, list[FrameMeta]] = defaultdict(list)
    for f in frames:
        by_dir[f.direction].append(f)
    for d in by_dir:
        by_dir[d].sort(key=lambda m: m.frame_index)

    thumb = 128
    label_h = 22
    gap = 16
    directions = [d for d in ("r", "l") if d in by_dir]
    if not directions:
        return
    max_frames = max(len(by_dir[d]) for d in directions)
    width = gap + max_frames * (thumb + gap)
    height = gap + len(directions) * (thumb + label_h + gap) + 28
    bg = checkerboard((width, height))
    draw = ImageDraw.Draw(bg)
    try:
        font = ImageFont.load_default()
    except Exception:
        font = None

    draw.text((gap, 6), f"{action}", fill=(20, 20, 20), font=font)
    y = 28
    for d in directions:
        x = gap
        for meta in by_dir[d]:
            sprite = Image.open(sprites_dir / action / meta.filename).convert("RGBA")
            # paste onto checkerboard cell
            cell = checkerboard((thumb, thumb))
            cell.paste(sprite, (0, 0), sprite)
            bg.paste(cell, (x, y))
            draw.text(
                (x, y + thumb + 2),
                meta.filename,
                fill=(30, 30, 30),
                font=font,
            )
            x += thumb + gap
        y += thumb + label_h + gap

    bg.save(preview_dir / f"{action}_preview.png")


def build_manifest(frames: Iterable[FrameMeta], existing: dict | None = None) -> dict:
    manifest: dict = {}
    detailed: list[dict] = []
    if existing:
        # Start from existing detailed frames, replace by action/filename keys.
        for entry in existing.get("frames", []):
            detailed.append(entry)
    by_key = {f"{e['action']}/{e['filename']}": i for i, e in enumerate(detailed)}
    for f in frames:
        entry = asdict(f)
        key = f"{f.action}/{f.filename}"
        if key in by_key:
            detailed[by_key[key]] = entry
        else:
            by_key[key] = len(detailed)
            detailed.append(entry)

    for entry in detailed:
        bucket = manifest.setdefault(entry["action"], {"right": [], "left": []})
        key = "right" if entry["direction"] == "r" else "left"
        if entry["filename"] not in bucket[key]:
            bucket[key].append(entry["filename"])
    for action, dirs in manifest.items():
        for k in dirs:
            dirs[k] = sorted(dirs[k])
    return {"actions": manifest, "frames": detailed}


def resolve_sheets(
    input_image: Path | None, action_filter: str | None, include_skipped: bool = False
) -> list[tuple[Path, list[dict]]]:
    grey = ROOT / "src" / "assets" / "grey-cat"

    def prepare(specs: list[dict]) -> list[dict] | None:
        # Keep full row alignment; mark non-requested / skipped rows.
        prepared: list[dict] = []
        any_active = False
        for s in specs:
            s = dict(s)
            if s.get("skip") and not include_skipped:
                s["skip"] = True
            elif action_filter and s.get("action") != action_filter:
                s["skip"] = True
            else:
                s["skip"] = bool(s.get("skip")) and not include_skipped
            if not s["skip"]:
                any_active = True
            prepared.append(s)
        return prepared if any_active else None

    if input_image is not None:
        name = input_image.name
        specs = SHEET_CONFIG.get(name)
        if specs is None:
            specs = [
                {
                    "action": action_filter or "unknown",
                    "direction": "r",
                    "expected": None,
                }
            ]
        prepared = prepare(specs)
        return [(input_image, prepared)] if prepared else []

    pairs: list[tuple[Path, list[dict]]] = []
    for name, specs in SHEET_CONFIG.items():
        path = grey / name
        if not path.exists():
            continue
        prepared = prepare(specs)
        if prepared:
            pairs.append((path, prepared))
    return pairs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "input_image",
        nargs="?",
        type=Path,
        help="Optional single reference sheet. Default: all configured grey-cat sheets.",
    )
    parser.add_argument("--action", type=str, default=None, help="Only extract this action")
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help=f"Output directory (default: {DEFAULT_OUT})",
    )
    parser.add_argument("--canvas", type=int, default=DEFAULT_CANVAS)
    parser.add_argument("--padding", type=int, default=DEFAULT_PAD)
    parser.add_argument("--bg-threshold", type=int, default=BG_THR)
    parser.add_argument(
        "--include-skipped",
        action="store_true",
        help="Also extract sheets/rows marked skip in config (e.g. alternate jump sheet)",
    )
    parser.add_argument(
        "--expected-count",
        type=int,
        default=None,
        help="Override expected frame count warning threshold for single-image mode",
    )
    args = parser.parse_args(argv)

    sheets = resolve_sheets(args.input_image, args.action, args.include_skipped)
    if not sheets:
        print("No sheets to process.", file=sys.stderr)
        return 1

    args.out.mkdir(parents=True, exist_ok=True)
    all_frames: list[FrameMeta] = []
    all_warnings: list[str] = []

    for path, specs in sheets:
        if args.expected_count is not None and len(specs) == 1 and specs[0].get("expected") is None:
            specs[0]["expected"] = args.expected_count
        print(f"Processing {path.name} ...")
        result = process_sheet(
            path,
            specs,
            args.out,
            canvas=args.canvas,
            pad=args.padding,
            bg_thr=args.bg_threshold,
            include_skipped=args.include_skipped,
        )
        all_frames.extend(result.frames)
        all_warnings.extend(result.warnings)
        print(f"  extracted {len(result.frames)} frames")
        for w in result.warnings:
            print(f"  WARN: {w}")

    # Deduplicate by filename (later sheets overwrite earlier in filesystem;
    # keep last meta).
    by_name: dict[str, FrameMeta] = {}
    for f in all_frames:
        by_name[f"{f.action}/{f.filename}"] = f
    unique_frames = list(by_name.values())

    manifest_path = args.out / "manifest.json"
    existing = None
    if args.action and manifest_path.exists():
        existing = json.loads(manifest_path.read_text())
    manifest = build_manifest(unique_frames, existing=existing)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Wrote {manifest_path}")

    preview_dir = args.out / "_previews"
    # Preview only actions touched this run; full manifest actions stay on disk.
    actions = sorted({f.action for f in unique_frames})
    for action in actions:
        write_action_preview(
            action,
            [f for f in unique_frames if f.action == action],
            args.out,
            preview_dir,
        )
    print(f"Wrote previews to {preview_dir}")

    # Summary counts from this run
    print("\nFrame counts (this run):")
    for action in actions:
        rights = [f for f in unique_frames if f.action == action and f.direction == "r"]
        lefts = [f for f in unique_frames if f.action == action and f.direction == "l"]
        print(f"  {action}: R={len(rights)} L={len(lefts)}")

    if all_warnings:
        print(f"\n{len(all_warnings)} warning(s) — review _previews/detect_*.png")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
