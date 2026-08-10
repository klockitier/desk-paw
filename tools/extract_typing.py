"""Extract the typing sprite sheet: calm/aggressive rows, reactions, props, effects.

Blob-based like the other extractors. Typing frames are anchored on the desk line
and the keyboard's horizontal centre — not the raw bounding box — so the paws move
while the cat and keyboard stay put, even when lightning or steam juts out one side.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "assets/source/cat_typing_sprite_sheet.png"
OUT = ROOT / "assets/cat"
APP_OUT = ROOT / "src/assets/cat"

# Alpha below this is the sheet's soft glow/halo — dropping it keeps edges hard.
ALPHA_CUT = 128
MIN_FRAME_AREA = 4000
MIN_PROP_AREA = 380
# The typing art is drawn larger than the walk/idle sheet. Tune if the cat changes
# size when it sits down to type.
SCALE = 0.62
# Sprites left of this x in the final row are reactions; right of it, props/effects.
PROPS_X = 800
# Bottom slice of a frame used to locate the keyboard/desk centre.
DESK_BAND = 0.14

ROWS = [
    ("calm", "front"),
    ("calm", "left"),
    ("calm", "right"),
    ("aggressive", "front"),
    ("aggressive", "left"),
    ("aggressive", "right"),
]

# Named by eye from the sheet, left to right.
REACTION_NAMES = [
    "typing_sparkle",
    "starry_eyes",
    "headphones",
    "stretch",
    "walk_keyboard",
    "wave",
]

# The desk block, in the order the sprites are found (top row then bottom row).
# Anger marks and Zzz are drawn into the aggressive/exhausted frames themselves,
# so there are no standalone sprites for them.
PROP_NAMES = {
    1: ("props", "monitor"),
    2: ("props", "keyboard"),
    3: ("props", "mouse"),
    4: ("props", "mug"),
    5: ("props", "plant"),
    6: ("effects", "keycap_pink"),
    7: ("effects", "keycap_white"),
    8: ("effects", "keycap_teal"),
    9: ("effects", "keycap_blue"),
    10: ("effects", "steam"),
    11: ("props", "sign"),
    12: ("effects", "lightning"),
    13: ("effects", "sparkle"),
    14: ("effects", "sparkle_small"),
    15: ("effects", "heart"),
    16: ("effects", "heart_small"),
}


def main() -> None:
    rgba = np.array(Image.open(SOURCE).convert("RGBA")).astype(int)
    mask = ndimage.binary_fill_holes(rgba[..., 3] >= ALPHA_CUT)
    caption_tops = caption_rows(rgba)
    bands = list(zip(caption_tops, caption_tops[1:] + [rgba.shape[0]]))

    lab, _ = ndimage.label(mask, np.ones((3, 3)))
    comps = components(lab, mask, rgba)

    typing: dict[tuple[str, str], list[dict]] = {}
    for (mood, facing), (y0, y1) in zip(ROWS, bands):
        row = [c for c in comps if y0 <= c["y0"] < y1 and c["area"] >= MIN_FRAME_AREA]
        row.sort(key=lambda c: c["x0"])  # left to right = chronological
        typing[(mood, facing)] = row
        print(f"typing_{mood}_{facing:5s} {len(row)} frames")

    last_y0 = bands[-1][0]
    extras = sorted(
        [c for c in comps if c["y0"] >= last_y0 and c["x0"] < PROPS_X and c["area"] >= MIN_FRAME_AREA],
        key=lambda c: c["x0"],
    )
    props = sorted(
        [c for c in comps if c["y0"] >= last_y0 and c["x0"] >= PROPS_X and c["area"] >= MIN_PROP_AREA],
        key=lambda c: (c["y0"] // 60, c["x0"]),
    )
    print(f"reactions {len(extras)}   props/effects {len(props)}")

    frames = [f for row in typing.values() for f in row]
    w, h, ax, ay = canvas_for(frames)
    print(f"typing canvas {w}x{h} anchor=({ax},{ay})")

    meta: dict[str, dict] = {}
    flat: dict[str, list[str]] = {}
    for (mood, facing), row in typing.items():
        dest = OUT / "typing" / mood / facing
        reset(dest)
        names = []
        for i, f in enumerate(row, 1):
            img = compose(rgba, f, w, h, ax, ay)
            img.save(dest / f"{i:03d}.png")
            flat_name = f"typing_{mood}_{facing}_{i - 1}.png"
            img.save(APP_OUT / flat_name)
            # The last aggressive frame is the exhausted cat — its own animation, so
            # the rage loop never cycles back through a sleeping pose.
            last = mood == "aggressive" and i == len(row)
            key = f"typing_exhausted_{facing}" if last else f"typing_{mood}_{facing}"
            flat.setdefault(key, []).append(flat_name)
            names.append(f"{i:03d}.png")
        meta.setdefault(f"typing_{mood}", {})[facing] = phases(mood, names)

    reset(OUT / "reactions")
    for i, c in enumerate(extras):
        name = REACTION_NAMES[i] if i < len(REACTION_NAMES) else f"reaction_{i + 1}"
        crop(rgba, c).save(OUT / "reactions" / f"{name}.png")

    reset(OUT / "props")
    reset(OUT / "effects")
    for i, c in enumerate(props, 1):
        folder, name = PROP_NAMES.get(i, ("effects", f"effect_{i:02d}"))
        crop(rgba, c).save(OUT / folder / f"{name}.png")

    (OUT / "typing" / "typing.json").write_text(json.dumps(meta, indent=2) + "\n")
    write_manifest(flat)
    contact_sheet(typing, extras, props, rgba, w, h, ax, ay)
    write_preview(meta)


def reset(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def caption_rows(rgba: np.ndarray) -> list[int]:
    """Tops of the wide navy caption pills that start each section."""
    a, r, g, b = rgba[..., 3], rgba[..., 0], rgba[..., 1], rgba[..., 2]
    pill = (a > 128) & (b > r + 15) & (b > g + 10) & (b > 40) & (r < 70)
    lab, n = ndimage.label(ndimage.binary_closing(pill, np.ones((5, 25))))
    tops = []
    for i in range(1, n + 1):
        ys, xs = np.where(lab == i)
        # the section captions all hug the left margin and are wide
        if len(xs) >= 3000 and xs.min() <= 30:
            tops.append(int(ys.min()))
    tops.sort()
    assert len(tops) == len(ROWS) + 1, f"found {len(tops)} captions"
    return [max(0, t - 8) for t in tops]


def components(lab: np.ndarray, mask: np.ndarray, rgba: np.ndarray) -> list[dict]:
    """Every sprite blob, with caption pills filtered out."""
    out = []
    for i, sl in enumerate(ndimage.find_objects(lab), 1):
        if sl is None:
            continue
        sub = lab[sl] == i
        area = int(sub.sum())
        if area < MIN_PROP_AREA:
            continue
        y0, y1 = sl[0].start, sl[0].stop
        x0, x1 = sl[1].start, sl[1].stop
        if y1 - y0 <= 34 and x1 - x0 > 120:
            continue  # caption pill
        out.append(
            {
                "id": i,
                "y0": y0, "y1": y1, "x0": x0, "x1": x1,
                "area": area,
                "anchor": desk_anchor(sub, x0, y1),
            }
        )
    return out


def desk_anchor(sub: np.ndarray, x0: int, y1: int) -> tuple[int, int]:
    """Centre of the desk/keyboard slab, and the desk's bottom line.

    Effects (lightning, steam, flying keycaps) stick out sideways and would drag a
    bbox centre around; the desk under the paws does not move, so anchor on that.
    """
    band = max(1, int(sub.shape[0] * DESK_BAND))
    cols = np.where(sub[-band:].any(axis=0))[0]
    if len(cols) == 0:
        cols = np.where(sub.any(axis=0))[0]
    return x0 + int((cols.min() + cols.max()) // 2), y1


def canvas_for(frames: list[dict]) -> tuple[int, int, int, int]:
    s = SCALE
    left = round(s * max(f["anchor"][0] - f["x0"] for f in frames))
    right = round(s * max(f["x1"] - f["anchor"][0] for f in frames))
    up = round(s * max(f["anchor"][1] - f["y0"] for f in frames))
    pad = 2
    return left + right + 2 * pad, up + 2 * pad, left + pad, up + pad


def cut(rgba: np.ndarray, comp: dict) -> Image.Image:
    """RGBA crop with the halo removed but the sprite's own soft shading intact."""
    y0, y1, x0, x1 = comp["y0"], comp["y1"], comp["x0"], comp["x1"]
    sub = rgba[y0:y1, x0:x1]
    keep = sub[..., 3] >= ALPHA_CUT
    keep = ndimage.binary_fill_holes(keep)
    out = sub.copy()
    out[..., 3] = np.where(keep, sub[..., 3], 0)
    return Image.fromarray(out.astype(np.uint8))


def scaled(img: Image.Image) -> Image.Image:
    return img.resize(
        (max(1, round(img.width * SCALE)), max(1, round(img.height * SCALE))),
        Image.NEAREST,
    )


def crop(rgba: np.ndarray, comp: dict) -> Image.Image:
    return scaled(cut(rgba, comp))


def compose(rgba, comp, w, h, ax, ay) -> Image.Image:
    art = scaled(cut(rgba, comp))
    off_x = ax - round((comp["anchor"][0] - comp["x0"]) * SCALE)
    off_y = ay - round((comp["anchor"][1] - comp["y0"]) * SCALE)
    canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    canvas.paste(art, (off_x, off_y))
    return canvas


def phases(mood: str, names: list[str]) -> dict:
    """Split a row into start / loop / end.

    Calm rows are a plain loop with a settle-in first pose. Aggressive rows escalate
    and finish on the exhausted cat, which must not loop.
    """
    if mood == "calm":
        return {
            "frames": names,
            "start": names[:1],
            "loop": names[1:],
            "end": [],
            "fps": 9,
            "loop_forever": True,
        }
    return {
        "frames": names,
        "start": names[:2],
        "loop": names[2:-1],
        "end": names[-1:],
        "fps": 12,
        "loop_forever": False,
    }


def write_manifest(flat: dict[str, list[str]]) -> None:
    path = APP_OUT / "manifest.json"
    data = json.loads(path.read_text())
    fresh = {f for files in flat.values() for f in files}
    for old in [k for k in data["animations"] if k.startswith("typing_")]:
        for f in data["animations"][old]:
            if f not in fresh:
                (APP_OUT / f).unlink(missing_ok=True)
        del data["animations"][old]
    data["animations"].update(flat)
    path.write_text(json.dumps(data, indent=2) + "\n")
    print("manifest updated — run tools/extract_jumps.py to re-pad onto the shared canvas")


def contact_sheet(typing, extras, props, rgba, w, h, ax, ay) -> None:
    from PIL import ImageDraw

    rows = [(f"typing_{m}_{f}", r) for (m, f), r in typing.items()]
    rows.append(("reactions", extras))
    rows.append(("props/effects", props))
    cols = max(len(r) for _, r in rows)
    cell_h = h + 16
    sheet = Image.new("RGB", (cols * w, len(rows) * cell_h), (28, 30, 38))
    draw = ImageDraw.Draw(sheet)
    for ri, (label, row) in enumerate(rows):
        for ci, c in enumerate(row):
            img = compose(rgba, c, w, h, ax, ay) if c["area"] >= MIN_FRAME_AREA else None
            if img is None:
                img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
                art = crop(rgba, c)
                img.paste(art, ((w - art.width) // 2, h - art.height))
            sheet.paste(img, (ci * w, ri * cell_h + 16), img)
            draw.text((ci * w + 3, ri * cell_h + 3), f"{label} {ci + 1:02d}", fill=(210, 214, 230))
    sheet.save(OUT / "typing" / "typing_contact_sheet.png")


def write_preview(meta: dict) -> None:
    blocks = []
    for anim in ("typing_calm", "typing_aggressive"):
        cards = "".join(
            f'<figure class="card" data-dir="{anim.split("_")[1]}/{facing}" '
            f'data-frames=\'{json.dumps(meta[anim][facing]["frames"])}\' '
            f'data-fps="{meta[anim][facing]["fps"]}">'
            f"<figcaption>{facing}</figcaption>"
            '<div class="stage"><img alt=""></div>'
            '<div class="ctl"><button data-act="play">▶</button>'
            '<button data-act="pause">❚❚</button>'
            '<button data-act="prev">◀</button>'
            '<button data-act="next">▶|</button>'
            f'<label>fps <input type="number" min="1" max="30" value="{meta[anim][facing]["fps"]}"></label>'
            '<span class="n"></span></div></figure>'
            for facing in ("front", "left", "right")
        )
        blocks.append(f"<h2>{anim.replace('_', ' ').title()}</h2><section>{cards}</section>")
    (OUT / "typing" / "preview.html").write_text(PREVIEW.replace("{{BLOCKS}}", "".join(blocks)))


PREVIEW = """<!doctype html>
<meta charset="utf-8"><title>Typing frames</title>
<style>
  body{background:#15171d;color:#e6e8ef;font:14px system-ui;margin:24px}
  section{display:flex;flex-wrap:wrap;gap:16px;margin-bottom:24px}
  .card{margin:0;background:#20232c;border-radius:10px;padding:10px}
  figcaption{margin-bottom:6px;opacity:.7}
  .stage{width:260px;height:220px;display:grid;place-items:center;
    background:repeating-conic-gradient(#3a3f4b 0 25%,#2a2e38 0 50%) 0 0/20px 20px}
  .stage img{image-rendering:pixelated;transform:scale(1.5)}
  .ctl{display:flex;align-items:center;gap:6px;margin-top:8px;flex-wrap:wrap}
  button{background:#2f3542;color:#e6e8ef;border:0;border-radius:6px;padding:4px 8px;cursor:pointer}
  input{width:52px;background:#2f3542;color:#e6e8ef;border:0;border-radius:6px;padding:3px}
  .n{opacity:.6}
</style>
{{BLOCKS}}
<script>
for (const card of document.querySelectorAll('.card')) {
  const frames = JSON.parse(card.dataset.frames);
  const dir = card.dataset.dir + '/';
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
