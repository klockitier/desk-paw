"""Extract clean transparent cat sprites from the reference sheet.

Uses manually verified per-frame bounding boxes so neighboring cats, labels,
and the black background never leak into a sprite. The source sheet itself has
one misplaced frame in Walk/Left (a typing pose) — that frame is skipped.

Outputs:
  assets/cat/<anim>/<facing>/001.png ...
  assets/cat/cat_atlas.png + cat_atlas.json
  assets/cat/extracted_preview.png
  assets/cat/preview.html
  src/assets/cat/  (flat copies + manifest.ts for the Vite app)
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage

from keying import straight_alpha

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "assets/source/cat_sprite_sheet.png"
OUT = ROOT / "assets/cat"
APP_OUT = ROOT / "src/assets/cat"

# Low on purpose: the sheet's background is pure black (0-4), so this still keys
# cleanly, and it pulls the dark outline *inside* the mask. With coverage-based
# alpha (tools/keying.py) the faint outer ring fades out instead of forming a halo,
# and the outline stays fully opaque and even all the way round the cat.
FG_THRESHOLD = 7
# Unambiguously art; anything at least this bright anchors the hysteresis.
STRONG_THRESHOLD = 30
# How far a near-black pixel may sit from bright art and still count (px * 2 + 1).
GLOW_REACH = 5
MIN_SPECK = 12
# Widest near-black shadow gap to treat as art rather than background. Raise it and
# real gaps (between the legs, ear to head) start filling in.
SHADOW_BRIDGE = 5

# Manually verified (x0, y0, x1, y1) boxes. Walk/left skips the source-sheet
# typing pose that ChatGPT put in the middle of that row.
FRAMES: dict[str, list[tuple[int, int, int, int]]] = {
    "idle/front": [
        (15, 25, 78, 98),
        (84, 25, 141, 98),
        (145, 21, 201, 97),
        (207, 23, 268, 97),
        (270, 24, 331, 97),
        (334, 23, 398, 98),
    ],
    "idle/left": [
        (415, 26, 483, 97),
        (481, 26, 551, 97),
        (546, 25, 615, 97),
        (614, 24, 690, 97),
    ],
    "idle/right": [
        (721, 26, 791, 97),  # tail starts at x=720; the old box sliced it flat
        (791, 26, 859, 97),
        (859, 25, 936, 97),
        (926, 23, 994, 97),
    ],
    "walk/front": [
        (20, 119, 71, 188),
        (87, 115, 145, 188),
        (157, 119, 212, 188),
        (215, 119, 270, 186),
        (272, 119, 336, 189),
    ],
    # Skip the crouching typing pose between frames 2 and 3 of the source row.
    "walk/left": [
        # frame0 sits under the caption bar — keep y0 below it
        (351, 121, 433, 185),
        (428, 123, 494, 185),  # tightened right edge: neighbor bleed under the tail
        (548, 113, 620, 185),  # raised for full white tip (+margin)
        (615, 111, 692, 184),
    ],
    "walk/right": [
        (715, 119, 790, 185),  # tip starts ~119; stay below blue caption
        (790, 119, 865, 185),
        (863, 111, 938, 185),
        (938, 111, 1017, 185),
    ],
    "run/front": [
        (17, 211, 73, 273),
        (84, 207, 138, 276),
        (146, 207, 205, 277),
        (207, 199, 267, 273),  # raised tail reaches y=200
        (271, 205, 334, 279),
    ],
    "run/left": [
        (346, 210, 436, 271),
        (437, 207, 527, 270),
        (526, 202, 609, 269),
        (615, 213, 702, 269),
    ],
    "run/right": [
        (711, 210, 782, 272),
        (782, 211, 856, 273),
        (862, 208, 934, 270),
        (938, 207, 1014, 273),
    ],
    "sit/front": [
        (13, 297, 66, 363),
        (69, 297, 121, 361),
        (127, 295, 177, 361),
    ],
    "sit/left": [
        (194, 299, 255, 361),
        (248, 298, 306, 363),
        (299, 296, 358, 363),
    ],
    "sit/right": [
        (359, 299, 403, 364),  # expanded left for full white tip
        (403, 299, 456, 363),
        (439, 298, 496, 363),
    ],
    "sleep/front": [
        (531, 315, 604, 353),  # includes floating zzz
        (613, 293, 689, 359),
    ],
    "sleep/left": [
        (725, 287, 833, 355),  # includes zzz trail
    ],
    "sleep/right": [
        (873, 287, 1000, 361),
    ],
    "typing/front": [
        (15, 388, 65, 424),
        (75, 388, 125, 424),
        (137, 387, 188, 424),
    ],
    "typing/left": [
        (207, 391, 267, 424),
        (291, 386, 395, 451),  # includes hearts
    ],
    "typing/right": [
        (422, 388, 526, 459),  # includes hearts
        (524, 384, 590, 436),
    ],
    # Boxes widened to hold the sparkles and hearts, which sit above and beside
    # each cat and were being sliced off at the frame corners.
    "happy/front": [
        (599, 386, 673, 462),
        (686, 383, 759, 462),
    ],
    "happy/left": [
        (778, 375, 867, 462),
    ],
    "happy/right": [
        (894, 368, 999, 461),
    ],
    "dragged/front": [
        (15, 481, 65, 578),
        (81, 481, 130, 575),
        (149, 471, 199, 574),
    ],
    "dragged/left": [
        (215, 481, 272, 581),
        (286, 473, 344, 577),
        (344, 472, 409, 569),
    ],
    "dragged/right": [
        (415, 483, 470, 579),
        (467, 483, 522, 577),
        (527, 479, 595, 569),
    ],
    "overheated/front": [
        (613, 488, 707, 572),
    ],
    "overheated/left": [
        (772, 479, 868, 561),
    ],
    "overheated/right": [
        (901, 499, 993, 564),
    ],
}


def build_mask(rgb: np.ndarray) -> np.ndarray:
    """Hysteresis keying: definite art, plus dark pixels that sit against it.

    A flat threshold has to choose between losing the dark outline (high) and
    keeping the sheet's wide faint glow (low). At the low value the glow around the
    happy sparkles reaches the cat, and everything it encloses becomes solid black.
    So: bright pixels are art outright, and near-black pixels only count when they
    are within `GLOW_REACH` of something bright — which is exactly the outline.
    """
    lum = rgb.max(axis=2)
    strong = lum >= STRONG_THRESHOLD
    near_strong = ndimage.binary_dilation(strong, np.ones((GLOW_REACH, GLOW_REACH)))
    return (lum >= FG_THRESHOLD) & near_strong


def scrub_caption_blue(rgb: np.ndarray, mask: np.ndarray) -> None:
    """Drop leftover blue label pixels that sit above the cats."""
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    blueish = (b > r + 18) & (b > g + 8) & (b > 40) & (r < 80)
    mask[blueish] = False
    # The pill's rim and drop shadow are too dark/desaturated to match by colour and
    # came through as a black bar. Clear each caption's whole rectangle instead —
    # cats never overlap a caption, only effects beside one do.
    lab, n = ndimage.label(ndimage.binary_closing(blueish, np.ones((5, 25))))
    for sl in ndimage.find_objects(lab):
        if sl is None or sl[1].stop - sl[1].start < 60:
            continue
        mask[
            max(0, sl[0].start - 4) : sl[0].stop + 4,
            max(0, sl[1].start - 4) : sl[1].stop + 4,
        ] = False


def cut(rgb: np.ndarray, mask: np.ndarray, box: tuple[int, int, int, int]) -> Image.Image | None:
    x0, y0, x1, y1 = box
    sub_m = mask[y0:y1, x0:x1].copy()
    lab, n = ndimage.label(sub_m, structure=np.ones((3, 3)))
    if n == 0:
        return None

    sizes = np.array(ndimage.sum(sub_m, lab, range(1, n + 1)))
    main = int(np.argmax(sizes)) + 1
    # centroids of every component
    cents = ndimage.center_of_mass(sub_m, lab, range(1, n + 1))
    my, mx = cents[main - 1]
    keep = np.zeros(n + 1, dtype=bool)
    keep[main] = True
    for i, (cy, cx) in enumerate(cents, 1):
        if i == main:
            continue
        if sizes[i - 1] < MIN_SPECK:
            continue
        # Keep nearby effect blobs (zzz / hearts / steam); drop distant speckles
        if abs(cy - my) + abs(cx - mx) < 55:
            keep[i] = True

    if not keep[lab].any():
        return None
    # Close and fill the *body* only. Closing bridges narrow near-black shadows (the
    # gap between a tail and the body reads as 1-6 luminance) which are open to the
    # edge and so would survive fill_holes as a notch bitten out of the cat. Run over
    # every component at once and it webs the sparkles and hearts together instead.
    body = ndimage.binary_closing(lab == main, np.ones((SHADOW_BRIDGE, SHADOW_BRIDGE)))
    body = ndimage.binary_fill_holes(body)
    others = keep[lab] & (lab != main)
    cleaned = body | ndimage.binary_fill_holes(others)
    ys, xs = np.where(cleaned)
    ty0, ty1 = int(ys.min()), int(ys.max()) + 1
    tx0, tx1 = int(xs.min()), int(xs.max()) + 1
    colour = rgb[y0:y1, x0:x1][ty0:ty1, tx0:tx1]
    return Image.fromarray(straight_alpha(colour, cleaned[ty0:ty1, tx0:tx1]))


def keep_largest(a: np.ndarray) -> np.ndarray:
    opaque = a[..., 3] > 0
    lab, n = ndimage.label(opaque, structure=np.ones((3, 3)))
    if n <= 1:
        return a
    sizes = ndimage.sum(opaque, lab, range(1, n + 1))
    main = int(np.argmax(sizes)) + 1
    out = a.copy()
    out[lab != main, 3] = 0
    out[out[..., 3] == 0, :3] = 0
    return out


def whiten_tail_tip(a: np.ndarray, rear: str) -> np.ndarray:
    """Paint the extreme rear tip of the tail cream/white."""
    a = a.copy()
    ys, xs = np.where(a[..., 3] > 0)
    if len(xs) == 0:
        return a
    x0, y0, x1, y1 = int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())
    h, w = y1 - y0 + 1, x1 - x0 + 1
    tip = np.zeros(a.shape[:2], dtype=bool)
    if rear == "left":
        tip[y0 + int(h * 0.40) : y1 + 1, x0 : x0 + max(5, int(w * 0.28))] = True
        # only the outermost columns of that band
        tip[:, x0 + 8 :] = False
    else:
        tip[y0 + int(h * 0.40) : y1 + 1, x1 - max(5, int(w * 0.28)) : x1 + 1] = True
        tip[:, : x1 - 7] = False
    tip &= a[..., 3] > 0
    lum = a[..., :3].max(axis=2)
    orange = (a[..., 0] > 150) & (a[..., 1] > 80) & (a[..., 0] > a[..., 2] + 30)
    darkish = tip & (lum < 160) & ~orange
    a[darkish, 0] = 245
    a[darkish, 1] = 240
    a[darkish, 2] = 230
    return a


def stabilize_sit(extracted: dict[str, list[Image.Image]]) -> None:
    """Lock face/body to frame 0; only the rear tail is allowed to animate.

    The source sheet's sit frames flicker facial markings between poses. For a
    desktop pet, a steady body with a moving tip looks much better.
    """
    for facing in ("front", "left", "right"):
        key = f"sit/{facing}"
        frames = extracted.get(key)
        if not frames:
            continue
        arrays = [keep_largest(np.array(im.convert("RGBA"))) for im in frames]
        base = arrays[0]
        ys, xs = np.where(base[..., 3] > 0)
        bx0, by0, bx1, by1 = int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())
        w, h = bx1 - bx0 + 1, by1 - by0 + 1

        # Tail-only zone: lower rear. Keep well below the face.
        zone = np.zeros(base.shape[:2], dtype=bool)
        ty0 = by0 + int(h * 0.58)
        if facing == "right":
            zone[ty0 : by1 + 1, max(0, bx0 - 2) : bx0 + int(w * 0.50)] = True
            rear = "left"
        elif facing == "left":
            zone[ty0 : by1 + 1, bx0 + int(w * 0.50) : min(base.shape[1], bx1 + 2)] = True
            rear = "right"
        else:
            zone[ty0 : by1 + 1, bx0 + int(w * 0.58) : min(base.shape[1], bx1 + 2)] = True
            rear = "right"

        fixed: list[Image.Image] = []
        for i, anim in enumerate(arrays):
            if i == 0:
                out = whiten_tail_tip(base, rear)
            else:
                # Start from base (locks face/body bit-identically), then swap only
                # the lower-rear tail pixels from the animated frame.
                out = base.copy()
                color_diff = (
                    np.abs(anim[..., :3].astype(int) - base[..., :3].astype(int)).sum(axis=2) > 40
                )
                alpha_diff = anim[..., 3] != base[..., 3]
                use = zone & (color_diff | alpha_diff | ((anim[..., 3] > 0) & (base[..., 3] == 0)))
                out[use] = anim[use]
                clear = zone & (base[..., 3] > 0) & (anim[..., 3] == 0) & alpha_diff
                out[clear, 3] = 0
                out[out[..., 3] == 0, :3] = 0
                # Re-assert face/upper body from base in case zone edged too high
                upper = np.zeros(out.shape[:2], dtype=bool)
                upper[: by0 + int(h * 0.55), :] = True
                out[upper] = base[upper]
                out = whiten_tail_tip(keep_largest(out), rear)
            fixed.append(Image.fromarray(out))
        extracted[key] = fixed
        print(f"  stabilized {key} ({len(fixed)} frames, tail-only motion)")


def pad(img: Image.Image, w: int, h: int) -> Image.Image:
    canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    canvas.paste(img, ((w - img.width) // 2, h - img.height))
    return canvas


def write_debug_overlay(rgb: np.ndarray, path: Path) -> None:
    vis = Image.fromarray(rgb.astype(np.uint8)).convert("RGBA")
    draw = ImageDraw.Draw(vis)
    palette = [
        (255, 80, 80, 220),
        (80, 220, 120, 220),
        (80, 160, 255, 220),
        (255, 220, 60, 220),
        (220, 80, 255, 220),
        (80, 255, 255, 220),
    ]
    for i, (key, boxes) in enumerate(FRAMES.items()):
        col = palette[i % len(palette)]
        for j, (x0, y0, x1, y1) in enumerate(boxes):
            draw.rectangle([x0, y0, x1 - 1, y1 - 1], outline=col, width=2)
            draw.text((x0 + 2, max(0, y0 - 11)), f"{key.split('/')[0][:1]}{j+1}", fill=col)
    path.parent.mkdir(parents=True, exist_ok=True)
    vis.save(path)


def main() -> None:
    rgb = np.array(Image.open(SOURCE).convert("RGB")).astype(int)
    mask = build_mask(rgb)
    scrub_caption_blue(rgb, mask)

    # Debug overlay first so crop regions can be inspected
    write_debug_overlay(rgb, OUT / "debug_regions.png")

    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)

    extracted: dict[str, list[Image.Image]] = {}
    raw_sizes: list[tuple[int, int]] = []

    for key, boxes in FRAMES.items():
        anim, facing = key.split("/")
        dest = OUT / anim / facing
        dest.mkdir(parents=True, exist_ok=True)
        frames: list[Image.Image] = []
        for i, box in enumerate(boxes, 1):
            img = cut(rgb, mask, box)
            if img is None:
                raise SystemExit(f"empty cut for {key} frame {i}: {box}")
            frames.append(img)
            raw_sizes.append(img.size)
            # tight save first; we'll rewrite padded versions after sizing
            img.save(dest / f"{i:03d}.png")
        extracted[key] = frames
        print(f"{key:22s} {len(frames)} frames  sizes={[f.size for f in frames]}")

    # Normalize canvas: consistent size, bottom-centre anchor, a little headroom
    # so raised tails never sit flush against the top edge of the frame.
    TOP_SLACK = 4
    max_w = max(w for w, _ in raw_sizes)
    max_h = max(h for _, h in raw_sizes) + TOP_SLACK
    canvas_w = max_w + (max_w % 2)
    canvas_h = max_h + (max_h % 2)
    print(f"\nnormalized canvas: {canvas_w}x{canvas_h}")

    for key, frames in extracted.items():
        anim, facing = key.split("/")
        dest = OUT / anim / facing
        padded = [pad(f, canvas_w, canvas_h) for f in frames]
        extracted[key] = padded
        for i, img in enumerate(padded, 1):
            img.save(dest / f"{i:03d}.png")

    print("\nstabilizing sit animations...")
    stabilize_sit(extracted)
    # rewrite sit folders after stabilization
    for key, frames in extracted.items():
        if not key.startswith("sit/"):
            continue
        anim, facing = key.split("/")
        dest = OUT / anim / facing
        for i, img in enumerate(frames, 1):
            img.save(dest / f"{i:03d}.png")

    # Atlas + JSON
    keys = list(extracted.keys())
    cols = 8
    rows = (len(keys) + cols - 1) // cols
    # pack frames of each anim horizontally inside a row-of-anims grid is messy;
    # instead pack every individual frame left-to-right, wrapping.
    all_named: list[tuple[str, Image.Image]] = []
    for key, frames in extracted.items():
        anim, facing = key.split("/")
        for i, img in enumerate(frames, 1):
            all_named.append((f"{anim}_{facing}_{i:03d}", img))

    atlas_cols = 10
    atlas_rows = (len(all_named) + atlas_cols - 1) // atlas_cols
    atlas = Image.new("RGBA", (atlas_cols * canvas_w, atlas_rows * canvas_h), (0, 0, 0, 0))
    atlas_meta: dict[str, dict] = {}
    for idx, (name, img) in enumerate(all_named):
        ax = (idx % atlas_cols) * canvas_w
        ay = (idx // atlas_cols) * canvas_h
        atlas.paste(img, (ax, ay), img)
        atlas_meta[name] = {"x": ax, "y": ay, "width": canvas_w, "height": canvas_h}
    atlas.save(OUT / "cat_atlas.png")
    (OUT / "cat_atlas.json").write_text(json.dumps(atlas_meta, indent=2) + "\n")

    # Contact sheet for visual QA
    contact_keys = [k for k in FRAMES if not k.startswith("run/")]  # prioritize requested
    # include run too
    contact_keys = list(FRAMES.keys())
    max_frames = max(len(v) for v in extracted.values())
    label_w = 160
    contact = Image.new(
        "RGB",
        (label_w + max_frames * canvas_w, len(contact_keys) * canvas_h),
        (28, 30, 38),
    )
    draw = ImageDraw.Draw(contact)
    for r, key in enumerate(contact_keys):
        y = r * canvas_h
        draw.rectangle([0, y, contact.width, y + canvas_h], outline=(55, 58, 72))
        draw.text((8, y + canvas_h // 2 - 6), f"{key} ({len(extracted[key])})", fill=(230, 230, 240))
        for i, img in enumerate(extracted[key]):
            # composite over dark bg so transparency is visible
            cell = Image.new("RGBA", (canvas_w, canvas_h), (28, 30, 38, 255))
            cell = Image.alpha_composite(cell, img)
            contact.paste(cell.convert("RGB"), (label_w + i * canvas_w, y))
    contact.save(OUT / "extracted_preview.png")

    # HTML preview with checkerboard + playback
    write_preview_html(extracted, canvas_w, canvas_h)

    # Flat copies for the Vite app (keeps existing loader happy)
    if APP_OUT.exists():
        shutil.rmtree(APP_OUT)
    APP_OUT.mkdir(parents=True)
    app_anims: dict[str, list[str]] = {}
    for key, frames in extracted.items():
        anim, facing = key.split("/")
        name = f"{anim}_{facing}"
        files = []
        for i, img in enumerate(frames):
            fname = f"{name}_{i}.png"
            img.save(APP_OUT / fname)
            files.append(fname)
        app_anims[name] = files

    meta = {"frameWidth": canvas_w, "frameHeight": canvas_h, "animations": app_anims}
    (APP_OUT / "manifest.json").write_text(json.dumps(meta, indent=2) + "\n")
    lines = [
        "// Generated by tools/extract_sprites.py — do not edit by hand.",
        "",
        f"export const FRAME_WIDTH = {canvas_w};",
        f"export const FRAME_HEIGHT = {canvas_h};",
        "",
        "export const ANIMATIONS = {",
    ]
    for name, files in sorted(app_anims.items()):
        entries = ", ".join(f'"{f}"' for f in files)
        lines.append(f"  {name}: [{entries}],")
    lines += ["} as const;", "", "export type AnimationName = keyof typeof ANIMATIONS;", ""]
    (APP_OUT / "manifest.ts").write_text("\n".join(lines))

    # Quality checks
    print("\nQuality checks:")
    bad = 0
    for key, frames in extracted.items():
        for i, img in enumerate(frames, 1):
            a = np.array(img)
            if a.shape[0] != canvas_h or a.shape[1] != canvas_w:
                print(f"  SIZE FAIL {key}/{i}")
                bad += 1
            # opaque pixels must not be pure black background leftovers at edges?
            opaque = a[..., 3] > 0
            if not opaque.any():
                print(f"  EMPTY {key}/{i}")
                bad += 1
            # alpha must be 0 or 255 (hard edges)
            mid = ((a[..., 3] > 0) & (a[..., 3] < 255)).sum()
            if mid > 0:
                print(f"  SOFT ALPHA {key}/{i}: {mid} px")
    print(f"  soft-edge alpha issues: {bad}")
    print(f"\nWrote {OUT} and {APP_OUT}")
    print(f"Skipped walk/left source-sheet typing pose (manual).")


def write_preview_html(
    extracted: dict[str, list[Image.Image]],
    canvas_w: int,
    canvas_h: int,
) -> None:
    cards = []
    for key, frames in extracted.items():
        anim, facing = key.split("/")
        paths = [f"./{anim}/{facing}/{i:03d}.png" for i in range(1, len(frames) + 1)]
        cards.append(
            f"""<section class="card" data-frames='{json.dumps(paths)}'>
  <header><strong>{anim}</strong> · {facing} · {len(frames)} frames</header>
  <div class="stage"><img alt="{key}" /></div>
  <footer class="meta">frame <span class="n">1</span>/{len(frames)}</footer>
</section>"""
        )
    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>Cat sprite preview</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{
    margin: 0; padding: 24px;
    font: 14px/1.4 ui-monospace, SFMono-Regular, Menlo, monospace;
    background: #12141a; color: #e8e8ef;
  }}
  h1 {{ font-size: 18px; font-weight: 600; margin: 0 0 16px; }}
  .grid {{
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
    gap: 16px;
  }}
  .card {{
    background: #1a1d27; border: 1px solid #2c3040; border-radius: 10px;
    overflow: hidden;
  }}
  .card header, .card footer {{ padding: 8px 12px; }}
  .card footer {{ color: #9aa0b4; border-top: 1px solid #2c3040; }}
  .stage {{
    image-rendering: pixelated;
    image-rendering: crisp-edges;
    height: {canvas_h}px;
    display: grid; place-items: center;
    background-image:
      linear-gradient(45deg, #2a2e3a 25%, transparent 25%),
      linear-gradient(-45deg, #2a2e3a 25%, transparent 25%),
      linear-gradient(45deg, transparent 75%, #2a2e3a 75%),
      linear-gradient(-45deg, transparent 75%, #2a2e3a 75%);
    background-size: 16px 16px;
    background-position: 0 0, 0 8px, 8px -8px, -8px 0;
    background-color: #222632;
  }}
  .stage img {{ width: {canvas_w}px; height: {canvas_h}px; image-rendering: pixelated; }}
</style>
</head>
<body>
  <h1>Extracted cat sprites · {canvas_w}×{canvas_h}</h1>
  <div class="grid">
    {''.join(cards)}
  </div>
  <script>
    const FPS = 8;
    for (const card of document.querySelectorAll('.card')) {{
      const frames = JSON.parse(card.dataset.frames);
      const img = card.querySelector('img');
      const n = card.querySelector('.n');
      let i = 0;
      const tick = () => {{
        img.src = frames[i];
        n.textContent = String(i + 1);
        i = (i + 1) % frames.length;
      }};
      tick();
      setInterval(tick, 1000 / FPS);
    }}
  </script>
</body>
</html>
"""
    (OUT / "preview.html").write_text(html)


if __name__ == "__main__":
    main()
