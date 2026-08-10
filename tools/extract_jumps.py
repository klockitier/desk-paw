"""Extract the jump animations from assets/source/cat_jump_sheet.png.

Blob-based, not grid-based: every cat is found as a connected component, nearby
fragments (dust puffs, motion streaks, detached tail tips) are re-attached, and
each frame is pasted onto one shared canvas anchored on the cat's feet so the
sprite never jitters while the window moves it along the jump arc.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

from keying import straight_alpha

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "assets/source/cat_jump_sheet.png"
OUT = ROOT / "assets/cat/jump"
APP_OUT = ROOT / "src/assets/cat"

FG = 7  # background is pure black (0-4); low enough to keep the dark outline
BODY_AREA = 2000  # a whole cat; smaller blobs are dust/streaks/tips
FRAGMENT_AREA = 12
FRAGMENT_GAP = 45  # px from the body bbox that a fragment may sit
BAND_LIFT = 20  # rows start this far above their caption pill
FPS = 12
# Motion streaks reach ~170px above the paws on the most extreme falling poses.
# Cap them so the canvas stays well inside the 180px window: only the tips of two
# streaks are trimmed, and the hit box stays tight around the cat.
MAX_ABOVE_FEET = 150
# Breathing room around the art, so the cat isn't jammed against its own canvas.
PAD = 8
# The jump sheet is drawn ~4/3 the size of the walk/idle sheet — tune this if the
# cat still changes size when it jumps. Nearest-neighbour only, 3/4 is exact.
SCALE = 0.75

# Sheet captions say Left/Right, but those rows face the opposite way —
# name files by the direction the cat actually faces.
ROWS = [
    ("up", "front"),
    ("up", "right"),  # caption "Jump Up - Left"
    ("up", "left"),  # caption "Jump Up - Right"
    ("up", "back"),
    ("down", "front"),
    ("down", "right"),  # caption "Jump Down - Left"
    ("down", "left"),  # caption "Jump Down - Right"
    ("down", "back"),
]


def main() -> None:
    rgb = np.array(Image.open(SOURCE).convert("RGB")).astype(int)
    mask = build_mask(rgb)
    bands = row_bands(rgb)

    frames: dict[tuple[str, str], list[dict]] = {}
    for (jump, facing), (y0, y1) in zip(ROWS, bands):
        frames[(jump, facing)] = row_frames(mask, y0, y1)

    all_frames = [f for row in frames.values() for f in row]
    w, h, anchor_x, anchor_y = canvas_for(all_frames)
    print(f"canvas {w}x{h} anchor=({anchor_x},{anchor_y})")

    meta: dict[str, dict] = {}
    flat: dict[str, list[str]] = {}
    for (jump, facing), row in frames.items():
        dest = OUT / jump / facing
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True, exist_ok=True)
        names = []
        for i, f in enumerate(row, 1):
            img = compose(rgb, f, w, h, anchor_x, anchor_y)
            img.save(dest / f"{i:03d}.png")
            flat_name = f"jump_{jump}_{facing}_{i - 1}.png"
            img.save(APP_OUT / flat_name)
            names.append(f"{i:03d}.png")
            flat.setdefault(f"jump_{jump}_{facing}", []).append(flat_name)
        meta.setdefault(f"jump_{jump}", {})[facing] = {
            "frames": names,
            "fps": FPS,
            "loop": False,
        }
        print(f"jump_{jump}_{facing:5s} {len(row)} frames")

    (OUT / "jump.json").write_text(json.dumps(meta, indent=2) + "\n")
    write_manifest(flat, w, h)
    repad_others(w, h, h - anchor_y)
    contact_sheet(frames, w, h, rgb, anchor_x, anchor_y)
    write_preview(meta)


def build_mask(rgb: np.ndarray) -> np.ndarray:
    """Art pixels, with caption pills and enclosed dark outlines resolved."""
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    mask = rgb.max(axis=2) >= FG
    pill = (b > r + 10) & (b > g + 5) & (b > 28) & (r < 60)
    pill = ndimage.binary_dilation(pill, np.ones((3, 3)), iterations=2)
    mask &= ~pill
    # dark outline pixels inside a cat read as background; fill them back in
    return ndimage.binary_fill_holes(mask)


def row_bands(rgb: np.ndarray) -> list[tuple[int, int]]:
    """Row y-ranges derived from the caption pills, not from a fixed grid."""
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    pill = (b > r + 10) & (b > g + 5) & (b > 28) & (r < 60)
    lab, n = ndimage.label(ndimage.binary_closing(pill, np.ones((5, 25))))
    tops = sorted(
        int(np.where(lab == i)[0].min())
        for i in range(1, n + 1)
        if (lab == i).sum() > 500
    )
    assert len(tops) == len(ROWS), f"found {len(tops)} captions"
    starts = [max(0, t - BAND_LIFT) for t in tops]
    return list(zip(starts, starts[1:] + [rgb.shape[0]]))


def row_frames(mask: np.ndarray, y0: int, y1: int) -> list[dict]:
    """One dict per cat: its own mask, bbox, and feet anchor.

    A component belongs to this row when its *top* falls in the band — falling
    poses are drawn low and spill past the band's bottom edge.
    """
    lab, n = ndimage.label(mask, np.ones((3, 3)))
    objs = ndimage.find_objects(lab)
    comps = []
    for i, sl in enumerate(objs, 1):
        top = sl[0].start
        if not (y0 <= top < y1):
            continue
        area = int((lab[sl] == i).sum())
        if area < FRAGMENT_AREA:
            continue
        comps.append({"id": i, "sl": sl, "area": area})

    bodies = [c for c in comps if c["area"] >= BODY_AREA]
    bodies.sort(key=lambda c: c["sl"][1].start)  # left to right = chronological
    frames = []
    for body in bodies:
        by, bx = body["sl"]
        keep = lab == body["id"]
        for c in comps:
            if c is body or c["area"] >= BODY_AREA:
                continue
            cy, cx = c["sl"]
            gap = max(
                0, by.start - cy.stop, cy.start - by.stop,
                bx.start - cx.stop, cx.start - bx.stop,
            )
            if gap <= FRAGMENT_GAP and nearest_body(comps, c, bodies) is body:
                keep |= lab == c["id"]
        ys, xs = np.where(keep)
        frames.append(
            {
                "mask": keep,
                "box": (int(ys.min()), int(ys.max()) + 1, int(xs.min()), int(xs.max()) + 1),
                # anchor on the cat itself, ignoring dust and streaks
                "anchor": ((bx.start + bx.stop) // 2, by.stop),
            }
        )
    return frames


def nearest_body(comps: list[dict], frag: dict, bodies: list[dict]) -> dict:
    fy, fx = frag["sl"]
    fcy, fcx = (fy.start + fy.stop) / 2, (fx.start + fx.stop) / 2
    return min(
        bodies,
        key=lambda b: abs((b["sl"][0].start + b["sl"][0].stop) / 2 - fcy)
        + abs((b["sl"][1].start + b["sl"][1].stop) / 2 - fcx),
    )


def canvas_for(frames: list[dict]) -> tuple[int, int, int, int]:
    """Smallest shared canvas that holds every frame at native resolution.

    Other animations (typing especially, which carries a desk) can be wider than
    any jump pose, so their content is measured too — one canvas serves them all.
    """
    s = SCALE
    left = round(s * max(f["anchor"][0] - f["box"][2] for f in frames))
    right = round(s * max(f["box"][3] - f["anchor"][0] for f in frames))
    up = round(s * min(MAX_ABOVE_FEET, max(f["anchor"][1] - f["box"][0] for f in frames)))
    down = round(s * max(f["box"][1] - f["anchor"][1] for f in frames))
    pad = PAD
    for path in APP_OUT.glob("*.png"):
        if path.name.startswith("jump_"):
            continue
        img = Image.open(path)
        bbox = img.getbbox()
        if bbox is None:
            continue
        art_w, art_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        dx = abs((bbox[0] + bbox[2]) / 2 - img.width / 2)
        left = max(left, round(art_w / 2 + dx))
        right = max(right, round(art_w / 2 + dx))
        up = max(up, art_h)
    return left + right + 2 * pad, up + down + 2 * pad, left + pad, up + pad


def compose(rgb, frame, w, h, ax, ay) -> Image.Image:
    y0, y1, x0, x1 = frame["box"]
    sub = frame["mask"][y0:y1, x0:x1]
    cut = Image.fromarray(straight_alpha(rgb[y0:y1, x0:x1], sub))
    # match the walk/idle art scale; nearest-neighbour keeps the pixel edges hard
    cut = cut.resize(
        (max(1, round(cut.width * SCALE)), max(1, round(cut.height * SCALE))),
        Image.NEAREST,
    )
    off_x = ax - round((frame["anchor"][0] - x0) * SCALE)
    off_y = ay - round((frame["anchor"][1] - y0) * SCALE)
    canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    # no mask: the canvas is empty, and pasting with the alpha as mask would
    # multiply alpha by itself and fade the sprite a little on every run
    canvas.paste(cut, (off_x, off_y))
    return canvas


def repad_others(w: int, h: int, below_feet: int) -> None:
    """Put walk/idle/… frames on the jump canvas so the feet line never moves.

    Idempotent: placement is computed from the alpha bbox, so re-running is a
    no-op. Run this after tools/extract_sprites.py, which writes its own canvas.
    """
    for path in sorted(APP_OUT.glob("*.png")):
        if path.name.startswith("jump_"):
            continue
        img = Image.open(path).convert("RGBA")
        bbox = img.getbbox()
        if bbox is None:
            continue
        art = img.crop(bbox)
        # Keep the art's offset from its own canvas centre: typing frames carry
        # lightning/steam on one side, and re-centring each bbox would swing the
        # cat and keyboard around between frames.
        dx = (bbox[0] + bbox[2]) / 2 - img.width / 2
        x = round(w / 2 + dx - art.width / 2)
        canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        canvas.paste(art, (x, h - below_feet - art.height))
        canvas.save(path)


def write_manifest(flat: dict[str, list[str]], w: int, h: int) -> None:
    path = APP_OUT / "manifest.json"
    data = json.loads(path.read_text())
    fresh = {f for files in flat.values() for f in files}
    for old in [k for k in data["animations"] if k.startswith("jump_")]:
        for f in data["animations"][old]:
            if f not in fresh:
                (APP_OUT / f).unlink(missing_ok=True)
        del data["animations"][old]
    data["animations"].update(flat)
    data["frameWidth"] = w
    data["frameHeight"] = h
    path.write_text(json.dumps(data, indent=2) + "\n")

    lines = [
        "// Generated — do not edit by hand.",
        "",
        f"export const FRAME_WIDTH = {data['frameWidth']};",
        f"export const FRAME_HEIGHT = {data['frameHeight']};",
        "",
        "export const ANIMATIONS = {",
    ]
    for name in sorted(data["animations"]):
        entries = ", ".join(f'"{f}"' for f in data["animations"][name])
        lines.append(f"  {name}: [{entries}],")
    lines += ["} as const;", "", "export type AnimationName = keyof typeof ANIMATIONS;", ""]
    (APP_OUT / "manifest.ts").write_text("\n".join(lines))


def contact_sheet(frames, w, h, rgb, ax, ay) -> None:
    from PIL import ImageDraw

    cols = max(len(r) for r in frames.values())
    cell_h = h + 16
    sheet = Image.new("RGB", (cols * w, len(frames) * cell_h), (28, 30, 38))
    draw = ImageDraw.Draw(sheet)
    for row_i, ((jump, facing), row) in enumerate(frames.items()):
        for col_i, f in enumerate(row):
            img = compose(rgb, f, w, h, ax, ay)
            sheet.paste(img, (col_i * w, row_i * cell_h + 16), img)
            draw.text(
                (col_i * w + 3, row_i * cell_h + 3),
                f"jump_{jump}_{facing} {col_i + 1:02d}",
                fill=(210, 214, 230),
            )
    sheet.save(OUT / "jump_contact_sheet.png")


def write_preview(meta: dict) -> None:
    blocks = []
    for anim in ("jump_up", "jump_down"):
        cards = "".join(
            f'<figure class="card" data-anim="{anim}" data-facing="{facing}" '
            f'data-frames=\'{json.dumps(meta[anim][facing]["frames"])}\'>'
            f"<figcaption>{facing}</figcaption>"
            f'<div class="stage"><img alt="{anim} {facing}"></div>'
            '<div class="ctl"><button data-act="play">▶</button>'
            '<button data-act="pause">❚❚</button>'
            '<button data-act="prev">◀</button>'
            '<button data-act="next">▶|</button>'
            f'<label>fps <input type="number" min="1" max="30" value="{FPS}"></label>'
            '<span class="n"></span></div></figure>'
            for facing in ("front", "left", "right", "back")
        )
        blocks.append(f"<h2>{anim.replace('_', ' ').title()}</h2><section>{cards}</section>")
    (OUT / "jump_preview.html").write_text(PREVIEW.replace("{{BLOCKS}}", "".join(blocks)))


PREVIEW = """<!doctype html>
<meta charset="utf-8"><title>Jump frames</title>
<style>
  body{background:#15171d;color:#e6e8ef;font:14px system-ui;margin:24px}
  section{display:flex;flex-wrap:wrap;gap:16px;margin-bottom:24px}
  .card{margin:0;background:#20232c;border-radius:10px;padding:10px}
  figcaption{margin-bottom:6px;opacity:.7}
  .stage{width:200px;height:200px;display:grid;place-items:center;
    background:repeating-conic-gradient(#3a3f4b 0 25%,#2a2e38 0 50%) 0 0/20px 20px}
  .stage img{image-rendering:pixelated;transform:scale(1.4)}
  .ctl{display:flex;align-items:center;gap:6px;margin-top:8px;flex-wrap:wrap}
  button{background:#2f3542;color:#e6e8ef;border:0;border-radius:6px;padding:4px 8px;cursor:pointer}
  input{width:52px;background:#2f3542;color:#e6e8ef;border:0;border-radius:6px;padding:3px}
  .n{opacity:.6}
</style>
{{BLOCKS}}
<script>
for (const card of document.querySelectorAll('.card')) {
  const frames = JSON.parse(card.dataset.frames);
  const dir = `${card.dataset.anim.split('_')[1]}/${card.dataset.facing}/`;
  const img = card.querySelector('img'), n = card.querySelector('.n');
  const fps = card.querySelector('input');
  let i = 0, timer = null;
  const show = () => { img.src = dir + frames[i]; n.textContent = `${i + 1}/${frames.length}`; };
  const step = d => { i = (i + d + frames.length) % frames.length; show(); };
  const play = () => { stop(); timer = setInterval(() => step(1), 1000 / +fps.value); };
  const stop = () => { clearInterval(timer); timer = null; };
  card.querySelector('[data-act=play]').onclick = play;
  card.querySelector('[data-act=pause]').onclick = stop;
  card.querySelector('[data-act=prev]').onclick = () => { stop(); step(-1); };
  card.querySelector('[data-act=next]').onclick = () => { stop(); step(1); };
  fps.onchange = () => { if (timer) play(); };
  show(); play();
}
</script>
"""


if __name__ == "__main__":
    main()
