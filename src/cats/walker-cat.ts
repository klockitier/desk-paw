import { invoke } from "@tauri-apps/api/core";
import {
  availableMonitors,
  getCurrentWindow,
  LogicalPosition,
} from "@tauri-apps/api/window";
import type { PetState } from "../state";
import type { CatController, CatKind } from "./types";
import type { Facing, SpritePack, WalkerOptions } from "./sprite-pack";
import * as walkerSprites from "./sprites";

/** Base animation per state; the facing suffix is added at draw time. */
const DEFAULT_STATE_ANIMATION: Record<PetState, string> = {
  IDLE: "idle",
  BLINKING: "idle",
  WATCHING_CURSOR: "idle",
  TYPING: "typing_calm",
  TYPING_AGGRESSIVE: "typing_aggressive",
  EXHAUSTED: "typing_exhausted",
  WORKING: "typing_calm",
  DRAGGED: "dragged",
  HAPPY: "happy",
  DONE: "happy",
  SLEEPING: "sleep",
  WAITING: "sit",
  ERROR: "overheated",
  OVERHEATED: "overheated",
};

/** Milliseconds per frame, tuned so each cycle reads smoothly at its own pace. */
const FRAME_MS: Record<string, number> = {
  idle: 220,
  walk: 110,
  run: 80,
  sit: 380,
  sleep: 900,
  typing_calm: 111, // 9 fps — unhurried work
  typing_aggressive: 83, // 12 fps — noticeably faster than calm
  typing_exhausted: 500,
  working: 140,
  happy: 170,
  dragged: 200,
  overheated: 260,
  jump_up: 70,
  jump_down: 70,
};

const STOP_DISTANCE = 90;
const RUN_DISTANCE = 340;
/** Prefer hop when the cursor is this far above/below the cat. */
const JUMP_DY = 120;
const WALK_SPEED = 46;
const RUN_SPEED = 132;
const MOVE_INTERVAL_MS = 32;
const FACE_FRONT_AFTER_MS = 1200;
/** Walking without getting anywhere this long → give up and sit down. */
const STUCK_MS = 1400;
/** Movement that counts as progress, in logical px. */
const PROGRESS_EPSILON = 6;
/** After giving up, the target must move this far before the cat chases again. */
const RETARGET_DISTANCE = 48;
/** How long one hop arc lasts (crouch → peak → land). */
const HOP_DURATION_MS = 520;

const RESTING_STATES: PetState[] = [
  "DRAGGED",
  "SLEEPING",
  "TYPING",
  "TYPING_AGGRESSIVE",
  "EXHAUSTED",
  "WORKING",
  "WAITING",
  "ERROR",
  "OVERHEATED",
];

const WALKER_PACK: SpritePack = {
  frameWidth: walkerSprites.FRAME_WIDTH,
  frameHeight: walkerSprites.FRAME_HEIGHT,
  preload: walkerSprites.preload,
  framesOf: walkerSprites.framesOf,
  resolveAnimation: walkerSprites.resolveAnimation,
};

export class WalkerCat implements CatController {
  kind: CatKind;
  private mountSelector: string;
  private pack: SpritePack;
  private stateAnimation: Record<PetState, string>;
  private preferSideFacing: boolean;

  private wrap!: HTMLElement;
  private canvas!: HTMLCanvasElement;
  private ctx!: CanvasRenderingContext2D;
  private ready = false;

  private state: PetState = "IDLE";
  private facing: Facing = "front";
  private frame = 0;
  private frameAt = 0;
  private stillSince = 0;
  private lastMoveAt = 0;
  private lastAnimBase = "";

  private moving = false;
  private running = false;
  private jumping = false;
  private jumpDown = false;
  private target = { x: 0, y: 0 };

  /** Active hop arc (null when walking/idle). */
  private hop: null | {
    x0: number;
    y0: number;
    x1: number;
    y1: number;
    started: number;
    weave: 1 | -1;
  } = null;
  private hopWeave: 1 | -1 = 1;

  /** Last side the cat faced; typing always uses a side view, never front. */
  private sideFacing: Exclude<Facing, "front"> = "right";
  /** Last spot where the cat actually made ground, for the stuck check. */
  private progress = { x: 0, y: 0, at: 0 };
  /** Target the cat gave up on; it sits until the target moves away from it. */
  private gaveUpOn: { x: number; y: number } | null = null;

  private appWindow = getCurrentWindow();

  constructor(options?: Partial<WalkerOptions>) {
    this.kind = options?.kind ?? "walker";
    this.mountSelector = options?.mountSelector ?? "#cat-walker";
    this.pack = options?.pack ?? WALKER_PACK;
    this.stateAnimation = { ...DEFAULT_STATE_ANIMATION, ...options?.stateAnimation };
    this.preferSideFacing = options?.preferSideFacing ?? false;
    if (this.preferSideFacing) this.facing = "right";
  }

  mount(root: HTMLElement): void {
    this.wrap = root.querySelector(this.mountSelector) as HTMLElement;
    this.canvas = this.wrap.querySelector("canvas") as HTMLCanvasElement;
    this.applyFrameSize();
    this.ctx = this.canvas.getContext("2d")!;
    this.ctx.imageSmoothingEnabled = false;

    void this.pack.preload().then(() => {
      this.ready = true;
      this.draw();
    });
  }

  show(): void {
    this.wrap.classList.remove("hidden");
    this.applyFrameSize();
  }

  hide(): void {
    this.wrap.classList.add("hidden");
  }

  private applyFrameSize(): void {
    const { frameWidth: w, frameHeight: h } = this.pack;
    this.canvas.width = w;
    this.canvas.height = h;
    for (const el of [this.wrap, this.canvas]) {
      el.style.width = `${w}px`;
      el.style.height = `${h}px`;
    }
    void invoke("set_cat_size", { w, h });
  }

  setState(state: PetState): void {
    if (state === this.state) return;
    this.state = state;
    this.frame = 0;
    this.frameAt = 0;
    if (RESTING_STATES.includes(state)) {
      this.moving = false;
      this.jumping = false;
      this.hop = null;
    }
    this.draw();
  }

  onCursor(
    screenX: number,
    screenY: number,
    winX: number,
    winY: number,
    winW: number,
    winH: number,
  ): number {
    this.target = { x: screenX, y: screenY };
    const dx = screenX - (winX + winW / 2);
    const dy = screenY - (winY + winH / 2);
    const dist = Math.hypot(dx, dy);

    if (this.gaveUpOn) {
      const moved = Math.hypot(screenX - this.gaveUpOn.x, screenY - this.gaveUpOn.y);
      if (moved < RETARGET_DISTANCE) {
        this.moving = false;
        this.jumping = false;
        return dist;
      }
      this.gaveUpOn = null;
    }

    const canMove = !RESTING_STATES.includes(this.state);
    this.moving = canMove && dist > STOP_DISTANCE;
    this.running = dist > RUN_DISTANCE;

    // Vertical climb/drop → hop like a real cat leaping onto something
    const vertical = Math.abs(dy) >= JUMP_DY && Math.abs(dy) >= Math.abs(dx) * 0.85;
    this.jumping = canMove && this.moving && vertical;
    this.jumpDown = dy > 0;

    if (this.moving) {
      this.stillSince = 0;
      if (this.jumping) {
        // Face the leap direction; weave left/right when mostly straight up/down
        if (Math.abs(dx) > 24) this.face(dx > 0 ? "right" : "left");
        else if (this.hop) this.face(this.hop.weave > 0 ? "right" : "left");
        else this.face(this.hopWeave > 0 ? "right" : "left");
      } else if (Math.abs(dx) > 8) {
        this.face(dx > 0 ? "right" : "left");
      }
    }
    return dist;
  }

  onLocalPointer(offsetX: number, offsetY: number): number {
    return Math.hypot(
      offsetX - this.pack.frameWidth / 2,
      offsetY - this.pack.frameHeight / 2,
    );
  }

  async tick(now: number): Promise<void> {
    if (!this.ready) return;

    if (this.moving) {
      this.stillSince = 0;
      if (now - this.lastMoveAt >= MOVE_INTERVAL_MS) {
        const dt = Math.min(0.1, (now - this.lastMoveAt) / 1000);
        this.lastMoveAt = now;
        if (this.jumping) await this.hopTowardTarget(now);
        else {
          this.hop = null;
          await this.stepTowardTarget(dt, now);
        }
      }
    } else {
      this.lastMoveAt = now;
      this.hop = null;
      this.jumping = false;
      // Standing still is not being stuck — the next walk gets a fresh timer.
      this.progress.at = 0;
      if (!this.stillSince) this.stillSince = now;
      if (
        !this.preferSideFacing &&
        this.facing !== "front" &&
        now - this.stillSince > FACE_FRONT_AFTER_MS
      ) {
        this.facing = "front";
      }
    }

    this.advanceFrame(now);
    this.draw();
  }

  hitSize(): { w: number; h: number } {
    return { w: this.pack.frameWidth, h: this.pack.frameHeight };
  }

  getElement(): HTMLElement {
    return this.wrap;
  }

  /**
   * Both movers report where they ended up. Walking without covering ground —
   * screen edge, or a stale target while the cursor sits still — stops the walk
   * instead of animating on the spot forever.
   */
  private noteProgress(x: number, y: number, now: number): void {
    if (Math.hypot(x - this.progress.x, y - this.progress.y) >= PROGRESS_EPSILON) {
      this.progress = { x, y, at: now };
      return;
    }
    if (!this.progress.at) {
      this.progress.at = now;
      return;
    }
    if (now - this.progress.at < STUCK_MS) return;
    this.moving = false;
    this.jumping = false;
    this.hop = null;
    this.gaveUpOn = { ...this.target };
  }

  private currentBase(): string {
    // Gave up chasing → settle. Uses idle, not sit: the sheet's sit pose draws the
    // tail as a pale curl joined by a near-black outline, which reads as a floating
    // blob on a dark desktop.
    if (this.gaveUpOn && !this.moving && !RESTING_STATES.includes(this.state)) {
      return "idle";
    }
    if (this.jumping && this.moving && !RESTING_STATES.includes(this.state)) {
      return this.jumpDown ? "jump_down" : "jump_up";
    }
    if (this.moving && !RESTING_STATES.includes(this.state)) {
      return this.running ? "run" : "walk";
    }
    return this.stateAnimation[this.state] ?? "idle";
  }

  /**
   * Facing to render with. The cat types at a desk seen from the side, so the
   * front sheet is never used for typing — it falls back to the last side faced.
   */
  private face(side: Exclude<Facing, "front">): void {
    this.facing = side;
    this.sideFacing = side;
  }

  private renderFacing(base: string): Facing {
    if (this.preferSideFacing && this.facing === "front") return this.sideFacing;
    if (this.facing === "front" && base.startsWith("typing_")) return this.sideFacing;
    // The sheet only has one side pose for happy, which freezes for the whole
    // reaction; the front pose has two frames, and facing the user on a click reads
    // better anyway.
    if (base === "happy" && !this.preferSideFacing) return "front";
    return this.facing;
  }

  private advanceFrame(now: number): void {
    const base = this.currentBase();
    if (base !== this.lastAnimBase) {
      this.lastAnimBase = base;
      this.frame = 0;
      this.frameAt = now;
      return;
    }
    const step = FRAME_MS[base] ?? 200;
    if (!this.frameAt) {
      this.frameAt = now;
      return;
    }
    if (now - this.frameAt < step) return;
    this.frameAt = now;

    // During a hop, drive frames from hop progress so crouch→peak→land syncs
    if (this.hop && (base === "jump_up" || base === "jump_down")) {
      const frames = this.pack.framesOf(
        this.pack.resolveAnimation(base, this.renderFacing(base)),
      );
      if (frames.length) {
        const t = Math.min(1, (now - this.hop.started) / HOP_DURATION_MS);
        this.frame = Math.min(frames.length - 1, Math.floor(t * frames.length));
      }
      return;
    }

    const count = this.pack.framesOf(
      this.pack.resolveAnimation(base, this.renderFacing(base)),
    ).length;
    this.frame = count ? (this.frame + 1) % count : 0;
  }

  private draw(): void {
    if (!this.ready) return;
    const base = this.currentBase();
    const frames = this.pack.framesOf(
      this.pack.resolveAnimation(base, this.renderFacing(base)),
    );
    const { frameWidth: w, frameHeight: h } = this.pack;
    this.ctx.clearRect(0, 0, w, h);
    const img = frames[this.frame % Math.max(1, frames.length)];
    if (img) this.ctx.drawImage(img, 0, 0);
  }

  private async hopTowardTarget(now: number): Promise<void> {
    const [outer, scale, size] = await Promise.all([
      this.appWindow.outerPosition(),
      this.appWindow.scaleFactor(),
      this.appWindow.outerSize(),
    ]);
    const winW = size.width / scale;
    const winH = size.height / scale;
    const cx = outer.x / scale + winW / 2;
    const cy = outer.y / scale + winH / 2;
    const dx = this.target.x - cx;
    const dy = this.target.y - cy;
    const dist = Math.hypot(dx, dy);
    this.noteProgress(cx, cy, now);
    if (!this.moving) return;
    if (dist < STOP_DISTANCE) {
      this.moving = false;
      this.jumping = false;
      this.hop = null;
      return;
    }

    // Start a new hop arc when idle or finished
    if (!this.hop || now - this.hop.started >= HOP_DURATION_MS) {
      if (this.hop) this.hopWeave = (this.hopWeave === 1 ? -1 : 1) as 1 | -1;
      const weave = this.hopWeave;
      // Each hop covers a chunk of the remaining gap — like leaping shelf to shelf
      const hopDist = Math.min(150, Math.max(70, dist * 0.38));
      const ux = dx / dist;
      const uy = dy / dist;
      // Sideways weave so pure-vertical leaps still hop left/right
      const side = Math.abs(ux) < 0.35 ? weave * 0.55 : 0;
      const x1 = cx + ux * hopDist + side * 36;
      const y1 = cy + uy * hopDist;
      this.hop = { x0: cx, y0: cy, x1, y1, started: now, weave };
      this.face(ux > 0.2 ? "right" : ux < -0.2 ? "left" : weave > 0 ? "right" : "left");
      this.frame = 0;
    }

    const t = Math.min(1, (now - this.hop.started) / HOP_DURATION_MS);
    // Ease-out horizontal, arc vertically (leap feel)
    const ease = 1 - (1 - t) * (1 - t);
    const arc = Math.sin(Math.PI * t); // 0→1→0 loft
    const loft = this.jumpDown ? 18 : 42; // hop higher when climbing
    let nx = this.hop.x0 + (this.hop.x1 - this.hop.x0) * ease - winW / 2;
    let ny =
      this.hop.y0 +
      (this.hop.y1 - this.hop.y0) * ease -
      loft * arc -
      winH / 2;

    ({ nx, ny } = await this.clampToDisplays(nx, ny, winW, winH, scale));

    await this.appWindow.setPosition(new LogicalPosition(nx, ny));
  }

  /**
   * Clamp against the union of every connected display, not just the one the
   * window currently sits on — clamping to a single monitor pinned the cat to
   * it forever, since the target position got snapped back before it could
   * cross the shared edge into the next screen.
   */
  private async clampToDisplays(
    nx: number,
    ny: number,
    winW: number,
    winH: number,
    scale: number,
  ): Promise<{ nx: number; ny: number }> {
    const monitors = await availableMonitors();
    if (!monitors.length) return { nx, ny };
    const left = Math.min(...monitors.map((m) => m.position.x)) / scale;
    const top = Math.min(...monitors.map((m) => m.position.y)) / scale;
    const right = Math.max(...monitors.map((m) => m.position.x + m.size.width)) / scale;
    const bottom = Math.max(...monitors.map((m) => m.position.y + m.size.height)) / scale;
    return {
      nx: Math.min(Math.max(nx, left), right - winW),
      ny: Math.min(Math.max(ny, top), bottom - winH),
    };
  }

  private async stepTowardTarget(dt: number, now: number): Promise<void> {
    const [outer, scale, size] = await Promise.all([
      this.appWindow.outerPosition(),
      this.appWindow.scaleFactor(),
      this.appWindow.outerSize(),
    ]);
    const winW = size.width / scale;
    const winH = size.height / scale;
    const cx = outer.x / scale + winW / 2;
    const cy = outer.y / scale + winH / 2;
    const dx = this.target.x - cx;
    const dy = this.target.y - cy;
    const dist = Math.hypot(dx, dy);
    this.noteProgress(cx, cy, now);
    if (!this.moving) return;
    if (dist < STOP_DISTANCE) {
      this.moving = false;
      return;
    }

    const speed = this.running ? RUN_SPEED : WALK_SPEED;
    const step = Math.min(speed * dt, dist - STOP_DISTANCE);
    let nx = cx + (dx / dist) * step - winW / 2;
    let ny = cy + (dy / dist) * step - winH / 2;

    ({ nx, ny } = await this.clampToDisplays(nx, ny, winW, winH, scale));

    await this.appWindow.setPosition(new LogicalPosition(nx, ny));
  }
}
