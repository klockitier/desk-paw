# Desk Paw

A tiny open-source macOS desktop cat.

It floats above your desktop, reacts to the mouse and keyboard, and can be steered by local agent hooks (`pawctl`). Inspired by the *idea* of desktop companions — **all artwork, animations, branding, and code here are original**.

## Why

Coding agents and long desktop sessions feel more alive with a small companion that notices when you type, when you wait, and when something finishes. This MVP is intentionally minimal so the behavior loop is easy to extend.

## Requirements

- macOS
- [Node.js](https://nodejs.org/) 20+
- [Rust](https://www.rust-lang.org/tools/install) (stable)
- Xcode Command Line Tools

## Install

```bash
npm install
```

## Run locally

```bash
npm run tauri dev
```

The cat window should appear without normal chrome. Right-click the cat for **Quit**.

Production-ish build:

```bash
npm run tauri build
```

## macOS permissions

| Feature | Permission | Required? |
| --- | --- | --- |
| Transparent always-on-top window | None | — |
| Drag cat / click / local reactions | None | — |
| Eyes follow cursor **inside** the pet window | None | — |
| Eyes follow cursor **across the desktop** | Accessibility (and sometimes Input Monitoring) | Optional |
| Detect typing in other apps | Accessibility / Input Monitoring | Optional |

If global monitoring is denied, the app still works: local mouse-over, click-to-pet, drag, and `pawctl` events keep functioning. A short on-screen note explains how to enable the permission.

**Privacy:** the app only learns that *a key event happened*. It does **not** read, store, log, or transmit which keys were pressed.

Enable via:

**System Settings → Privacy & Security → Accessibility** (allow **Desk Paw** / `desk-paw`).  
If needed, also check **Input Monitoring**.

## Architecture

```text
┌─────────────────────────────────────────────┐
│ Frontend (Vite + TS)                        │
│  state.ts        → central state machine    │
│  main.ts         → drag, events, persist    │
│  cats/walker-cat → sprite-sheet cat         │
│  cats/sprites.ts → frame loading / lookup   │
│  styles.css      → original CSS pixel cat   │
│  sound.ts        → no-op hooks for SFX      │
└─────────────────┬───────────────────────────┘
                  │ Tauri events / commands
┌─────────────────▼───────────────────────────┐
│ Rust (Tauri 2)                              │
│  transparent accessory window               │
│  CGEvent input monitor (activity only)      │
│  localhost pawctl TCP server :19283         │
└─────────────────────────────────────────────┘
```

## Cat artwork

The walker cat renders pre-rendered frames from `src/assets/cat/`, sliced out of a
single reference sprite sheet. Each animation exists in three views — `front`,
`left`, `right` — so the cat keeps the same identity whichever way it faces:

| Animation | Frames (front / left / right) |
| --- | --- |
| `idle` | 6 / 4 / 4 |
| `walk` | 5 / 4 / 4 |
| `run` | 5 / 4 / 4 |
| `sit` | 3 / 3 / 3 |
| `sleep` | 2 / 1 / 1 |
| `happy` | 2 / 1 / 1 |
| `dragged` | 3 / 3 / 3 |
| `overheated` | 1 / 1 / 1 |

> Note: the source sheet’s Walk/Left row contains a misplaced typing pose in the
> middle; that frame is skipped on purpose so walk/left stays a clean 4-frame cycle.

To re-slice after editing the sheet:

```bash
python3 -m venv .venv && ./.venv/bin/pip install -r tools/requirements.txt
./.venv/bin/python3 tools/extract_sprites.py
./.venv/bin/python3 tools/extract_typing.py  # run after extract_sprites.py
./.venv/bin/python3 tools/extract_jumps.py   # run last — it sizes the shared canvas
```

`tools/keying.py` handles the alpha for both black-background sheets: edge pixels are
un-premultiplied from the black they were drawn on (a plain threshold bakes that
black in as a dark fringe on light desktops), enclosed dark pixels like pupils are
filled so they never show through, and a uniform 1px outline is drawn round the
silhouette — the sheets outline dark fur heavily but leave the white chest and paws
almost bare, so without it the cat looks half-outlined.

The script uses **manual bounding boxes** (not equal-grid crops), keys out the black
background, drops labels, writes per-animation folders under `assets/cat/`, plus
`cat_atlas.png`, `cat_atlas.json`, `extracted_preview.png`, and `preview.html`.
Flat copies for the Vite app land in `src/assets/cat/` with `manifest.ts`.

### Jump artwork

`tools/extract_jumps.py` slices `assets/source/cat_jump_sheet.png`. It finds every
cat as a connected blob (no grid), re-attaches nearby dust puffs and motion
streaks, and pastes each frame at native resolution onto one shared canvas
anchored on the cat's **feet**, so the sprite never jitters while the window
moves along the jump arc. It also re-pads the other animations onto that canvas
so every frame shares one ground line — hence the run-order note above.

| Animation | Frames (front / left / right / back) |
| --- | --- |
| `jump_up` | 8 / 8 / 8 / 8 |
| `jump_down` | 8 / 8 / 8 / 6 |

> The sheet's `Left`/`Right` captions are swapped relative to the pose; files are
> named by the direction the cat actually faces. `jump_down/back` genuinely has
> only 6 frames in the source (its two mid-air frames are missing).

Output: `assets/cat/jump/{up,down}/{front,left,right,back}/NNN.png`, plus
`jump.json` (frame order, fps, loop), `jump_contact_sheet.png`, and
`jump_preview.html` (play / pause / step / fps over a checkerboard).

### Typing artwork

`tools/extract_typing.py` slices `assets/source/cat_typing_sprite_sheet.png` into a
calm and an aggressive typing animation, three facings each, plus standalone
reactions, desk props, and effect glyphs. Frames are anchored on the **desk line and
keyboard centre**, not the raw bounding box, so the paws move while the cat and
keyboard stay put even when lightning or steam juts out one side.

| Animation | Frames (front / left / right) | Rate |
| --- | --- | --- |
| `typing_calm` | 8 / 7 / 7 | 9 fps, loops |
| `typing_aggressive` | 6 / 6 / 6 | 12 fps, escalates |
| `typing_exhausted` | 1 / 1 / 1 | held pose |

The last aggressive frame (the collapsed cat with Zzz) is split into
`typing_exhausted` so the rage loop never cycles back through a sleeping pose.

Output: `assets/cat/typing/{calm,aggressive}/{front,left,right}/NNN.png`, plus
`typing.json` (start/loop/end phases and fps), `typing_contact_sheet.png`,
`preview.html`, and `assets/cat/{reactions,props,effects}/`. Left/right sprites are
the sheet's own artwork — nothing is CSS-mirrored, so tail and fur markings stay
correct. The cat always types in a **side view** — the front frames stay in the
sheet as a fallback but are never selected, since the desk only reads from the
side. When the cat is idle-facing-front, typing uses the last side it faced.

### Typing intensity

Key **timing** drives the mood; the keys themselves are never read or stored (see
[Privacy](#privacy-guarantees)). `PetStateMachine` keeps a list of timestamps from
the last second:

| Rate | State |
| --- | --- |
| up to 6 keys/sec | `TYPING` (calm) |
| more than 6 keys/sec | `TYPING_AGGRESSIVE` |
| back under 4 keys/sec | `TYPING` again |
| no keys for 1.2s | wind down — rage lands on `EXHAUSTED` for 1.6s first |

Purely speed-driven, in both directions. The enter/exit thresholds differ (6 up, 4
down) so one slow keystroke mid-burst doesn't flip the animation back and forth.
`typingIntensity` (0→1) is exposed on the machine for anything else that wants to
react to pace. Priority is
`ERROR`/`OVERHEATED` → typing → agent state → idle: the cat can type while an agent
is `WORKING` and returns to that state afterwards.

Run the state-machine checks with:

```bash
npm test
```

### State machine

States: `IDLE`, `BLINKING`, `WATCHING_CURSOR`, `TYPING`, `TYPING_AGGRESSIVE`, `EXHAUSTED`, `DRAGGED`, `HAPPY`, `SLEEPING`, plus agent states `WORKING`, `WAITING`, `DONE`, `ERROR`, `OVERHEATED`.

All transitions go through `src/state.ts`. Animations only render the current state.

## Trigger cat states

### In the UI

- **Click** the cat → happy
- **Drag** the cat → dragged pose, position saved
- **Right-click** → quick menu (idle / happy / sleep / quit)
- Move the cursor near the cat → watching
- Type (globally if permitted, or while the window is focused) → kneading / typing
- Leave the cat alone ~45s → sleeping

### Via `pawctl`

With the app running:

```bash
npm run pawctl -- working
npm run pawctl -- waiting
npm run pawctl -- done
npm run pawctl -- error
npm run pawctl -- overheated
npm run pawctl -- idle
npm run pawctl -- happy
npm run pawctl -- sleeping
```

Or:

```bash
node pawctl.mjs done
```

Protocol: one line over TCP to `127.0.0.1:19283` (override with `PAWCTL_PORT`).

## AI-agent integrations (later)

Point Claude Code / Codex / Cursor hooks at `pawctl`:

```bash
# example: when an agent starts a task
node /path/to/desk-paw/pawctl.mjs working

# when it finishes
node /path/to/desk-paw/pawctl.mjs done

# on failure
node /path/to/desk-paw/pawctl.mjs error
```

No cloud API, no accounts — just a local loopback poke.

## Privacy guarantees

- No analytics
- No telemetry
- No external network requests from the app
- No cloud services
- Keyboard contents are never collected
- Mouse coordinates are used only for immediate local interaction (eyes / proximity)
- `pawctl` listens on **localhost only**

## Position persistence

Last window position is stored in `localStorage` (`desk-paw.position`) and restored on launch.

## Contributing

1. Keep the MVP spirit: working behavior over polish.
2. Do not add analytics, accounts, or network callers.
3. Do not copy proprietary pet artwork or animations from other apps.
4. Prefer small PRs that extend the state machine or art swap-ins.

```bash
npm run tauri dev
```

## License

MIT — see [LICENSE](./LICENSE).
