"""Rebuild the sprite manifest from whatever PNGs are in src/assets/cat/.

For hand-edited frames: touch up the PNGs (trim background, fix a pixel), run this,
and the app picks them up. Unlike the extractors it reads no sprite sheet and
overwrites no artwork — it only re-pads frames onto the shared canvas so they stay
on one ground line, then regenerates manifest.json / manifest.ts.

    ./.venv/bin/python3 tools/sync_manifest.py

Frames are named `<animation>_<facing>_<index>.png`. Add, delete, or edit files and
the manifest follows; nothing else needs touching.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
APP_OUT = ROOT / "src/assets/cat"
NAME = re.compile(r"^(?P<anim>[a-z_]+)_(?P<facing>front|left|right|back)_(?P<idx>\d+)\.png$")
PAD = 8
# Distance from the canvas bottom to the ground line, matching tools/extract_jumps.py.
BELOW_FEET = 22


def main() -> None:
    frames: dict[str, list[tuple[int, str]]] = {}
    for path in sorted(APP_OUT.glob("*.png")):
        m = NAME.match(path.name)
        if not m:
            print(f"  skipped (unrecognised name): {path.name}")
            continue
        key = f"{m['anim']}_{m['facing']}"
        frames.setdefault(key, []).append((int(m["idx"]), path.name))

    if not frames:
        raise SystemExit(f"no sprites found in {APP_OUT}")

    w, h = canvas_size()
    repad(w, h)

    animations = {k: [n for _, n in sorted(v)] for k, v in sorted(frames.items())}
    total = sum(len(v) for v in animations.values())
    print(f"{len(animations)} animations, {total} frames, canvas {w}x{h}")

    (APP_OUT / "manifest.json").write_text(
        json.dumps({"frameWidth": w, "frameHeight": h, "animations": animations}, indent=2) + "\n"
    )
    lines = [
        "// Generated — do not edit by hand.",
        "",
        f"export const FRAME_WIDTH = {w};",
        f"export const FRAME_HEIGHT = {h};",
        "",
        "export const ANIMATIONS = {",
    ]
    for name, files in animations.items():
        lines.append(f"  {name}: [{', '.join(f'\"{f}\"' for f in files)}],")
    lines += ["} as const;", "", "export type AnimationName = keyof typeof ANIMATIONS;", ""]
    (APP_OUT / "manifest.ts").write_text("\n".join(lines))


def canvas_size() -> tuple[int, int]:
    """Keep the canvas the extractors chose, growing it only if an edit needs more."""
    manifest = APP_OUT / "manifest.json"
    w = h = 0
    if manifest.exists():
        data = json.loads(manifest.read_text())
        w, h = int(data.get("frameWidth", 0)), int(data.get("frameHeight", 0))

    sizes = {Image.open(p).size for p in APP_OUT.glob("*.png")}
    common = max(sizes, key=lambda s: sum(1 for p in APP_OUT.glob("*.png") if Image.open(p).size == s))
    w, h = max(w, common[0]), max(h, common[1])

    for path in APP_OUT.glob("*.png"):
        img = Image.open(path)
        if img.size == (w, h):
            continue  # already placed by an extractor; nothing to fit
        bbox = img.getbbox()
        if bbox is None:
            continue
        art_w, art_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        w = max(w, art_w + 2 * PAD)
        h = max(h, art_h + BELOW_FEET + PAD)
    return w, h


def repad(w: int, h: int) -> None:
    """Place edited frames on the shared canvas; leave already-sized frames alone.

    A frame that already matches the canvas was positioned by an extractor using its
    feet/desk anchor — re-deriving that from the alpha bbox would move it. An edited
    frame has no anchor to recover, so it is bottom-centred on the ground line.
    """
    for path in sorted(APP_OUT.glob("*.png")):
        img = Image.open(path).convert("RGBA")
        if img.size == (w, h):
            continue
        bbox = img.getbbox()
        if bbox is None:
            continue
        art = img.crop(bbox)
        canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        # no mask: pasting with alpha as its own mask squares it and fades the sprite
        canvas.paste(art, ((w - art.width) // 2, h - BELOW_FEET - art.height))
        canvas.save(path)
        print(f"  re-padded {path.name} ({img.width}x{img.height} → {w}x{h})")


if __name__ == "__main__":
    main()
