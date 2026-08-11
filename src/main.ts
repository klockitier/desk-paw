import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { getCurrentWindow, LogicalPosition, currentMonitor } from "@tauri-apps/api/window";
import { ClassicCat } from "./cats/classic-cat";
import type { CatController, CatKind } from "./cats/types";
import { WalkerCat } from "./cats/walker-cat";
import { PetStateMachine, type PetState } from "./state";
import { play } from "./sound";

/** Cursor jitter below this (logical px) is not activity. */
const CURSOR_MOVE_EPSILON = 1.5;
const POS_KEY = "desk-paw.position";
const CAT_KEY = "desk-paw.cat";

const machine = new PetStateMachine();
const appWindow = getCurrentWindow();
const root = document.getElementById("root")!;
const menu = document.getElementById("menu")!;
const statusEl = document.getElementById("status")!;

const classic = new ClassicCat();
const walker = new WalkerCat();
let active: CatController = walker;

let lastCursor = { x: 0, y: 0, t: 0 };
let cachedWin = { x: 0, y: 0, w: 180, h: 180, scale: 1, t: 0 };

function loadCatKind(): CatKind {
  const raw = localStorage.getItem(CAT_KEY);
  return raw === "classic" ? "classic" : "walker";
}

async function setActiveCat(kind: CatKind) {
  localStorage.setItem(CAT_KEY, kind);
  classic.hide();
  walker.hide();
  active = kind === "classic" ? classic : walker;
  active.show();
  active.setState(machine.state);
  await invoke("set_walker_mode", { walker: kind === "walker" });
}

function applyState(state: PetState) {
  active.setState(state);
  if (state === "HAPPY" || state === "DONE") play("meow");
  if (state === "DRAGGED") play("thud");
}

machine.subscribe((state) => applyState(state));

async function restorePosition() {
  const raw = localStorage.getItem(POS_KEY);
  if (!raw) return;
  try {
    const { x, y } = JSON.parse(raw) as { x: number; y: number };
    if (!Number.isFinite(x) || !Number.isFinite(y)) return;
    const monitor = await currentMonitor();
    if (!monitor) return;
    const scale = monitor.scaleFactor;
    const mx = monitor.position.x / scale;
    const my = monitor.position.y / scale;
    const mw = monitor.size.width / scale;
    const mh = monitor.size.height / scale;
    const w = 180;
    const h = 180;
    const cx = Math.min(Math.max(x, mx), mx + mw - w);
    const cy = Math.min(Math.max(y, my), my + mh - h);
    await appWindow.setPosition(new LogicalPosition(cx, cy));
  } catch {
    localStorage.removeItem(POS_KEY);
  }
}

async function persistPosition() {
  const pos = await appWindow.outerPosition();
  const scale = await appWindow.scaleFactor();
  localStorage.setItem(
    POS_KEY,
    JSON.stringify({ x: pos.x / scale, y: pos.y / scale }),
  );
}

async function refreshWinCache(force = false) {
  const now = performance.now();
  if (!force && now - cachedWin.t < 50) return;
  const [outer, size, scale] = await Promise.all([
    appWindow.outerPosition(),
    appWindow.outerSize(),
    appWindow.scaleFactor(),
  ]);
  cachedWin = {
    x: outer.x / scale,
    y: outer.y / scale,
    w: size.width / scale,
    h: size.height / scale,
    scale,
    t: now,
  };
}

async function onGlobalMouse(x: number, y: number, down: boolean) {
  if (drag) {
    if (!down) await endDrag();
    return; // the window is following the pointer natively; don't fight it
  }
  await refreshWinCache();
  const { x: winX, y: winY, w: winW, h: winH } = cachedWin;

  const now = performance.now();
  const dt = Math.max(1, now - (lastCursor.t || now));
  const moved = Math.hypot(x - lastCursor.x, y - lastCursor.y);
  const speed = moved / dt;
  lastCursor = { x, y, t: now };

  const dist = active.onCursor(x, y, winX, winY, winW, winH);
  // The Rust side polls at 16ms and emits even when the cursor is parked, so only
  // real movement counts as activity — otherwise the cat can never fall asleep.
  if (moved > CURSOR_MOVE_EPSILON) {
    machine.dispatch({ type: "CURSOR", dist, speed: speed * 16 });
  }
}

/** Set while the button is physically held after grabbing the cat. */
let drag: { before: Promise<{ x: number; y: number }> } | null = null;

function bindDrag(el: HTMLElement) {
  el.addEventListener("mousedown", (e) => {
    if (e.button !== 0) return;
    machine.dispatch({ type: "DRAG_START" });
    void invoke("set_follow_paused", { paused: true });
    // startDragging() resolves when the drag begins, so the release is detected
    // from the global button state instead (see onGlobalMouse).
    drag = { before: appWindow.outerPosition() };
    void appWindow.startDragging();
  });
}

async function endDrag(): Promise<void> {
  const active = drag;
  if (!active) return;
  drag = null;
  machine.dispatch({ type: "DRAG_END" });
  void invoke("set_follow_paused", { paused: false });
  const [before, after] = await Promise.all([
    active.before,
    appWindow.outerPosition(),
  ]);
  await persistPosition();
  await refreshWinCache(true);
  if (Math.hypot(after.x - before.x, after.y - before.y) < 4) {
    machine.dispatch({ type: "PET" });
  }
}

function bindPointer(el: HTMLElement) {
  el.addEventListener("mousemove", (e) => {
    const dist = active.onLocalPointer(e.offsetX, e.offsetY);
    machine.dispatch({ type: "CURSOR", dist, speed: 0.5 });
  });
}

function bindContextMenu(el: HTMLElement) {
  el.addEventListener("contextmenu", (e) => {
    e.preventDefault();
    menu.classList.toggle("hidden");
    void invoke("set_interactive_locked", {
      locked: !menu.classList.contains("hidden"),
    });
  });
}

interface MousePos {
  x: number;
  y: number;
  down: boolean;
}

async function boot() {
  classic.mount(root);
  walker.mount(root);

  const kind = loadCatKind();
  await setActiveCat(kind);
  applyState(machine.state);

  bindDrag(classic.getElement() as HTMLElement);
  bindDrag(walker.getElement() as HTMLElement);
  bindPointer(classic.getElement() as HTMLElement);
  bindPointer(walker.getElement() as HTMLElement);
  bindContextMenu(classic.getElement() as HTMLElement);
  bindContextMenu(walker.getElement() as HTMLElement);

  await restorePosition();

  await listen<MousePos>("mouse-global", (ev) => {
    void onGlobalMouse(ev.payload.x, ev.payload.y, ev.payload.down);
  });

  await listen("key-activity", () => {
    machine.dispatch({ type: "KEY_ACTIVITY" });
  });

  await listen<{ event: string }>("agent-event", (ev) => {
    machine.dispatch({ type: "AGENT", event: ev.payload.event });
  });

  await listen<{ keyboard: boolean; message: string }>("permission-status", (ev) => {
    if (!ev.payload.keyboard) {
      statusEl.textContent = ev.payload.message;
      statusEl.classList.remove("hidden");
    } else {
      statusEl.classList.add("hidden");
    }
  });

  menu.addEventListener("click", async (e) => {
    const btn = (e.target as HTMLElement).closest("button");
    if (!btn) return;
    const action = btn.getAttribute("data-action");
    menu.classList.add("hidden");
    void invoke("set_interactive_locked", { locked: false });

    if (action === "quit") {
      await invoke("quit_app");
      return;
    }
    if (action === "reset") {
      await invoke("reset_position");
      localStorage.removeItem(POS_KEY);
      return;
    }
    if (action === "cat-classic") {
      await setActiveCat("classic");
      return;
    }
    if (action === "cat-walker") {
      await setActiveCat("walker");
      return;
    }
    if (action) {
      machine.dispatch({ type: "AGENT", event: action });
    }
  });

  document.addEventListener("click", (e) => {
    const t = e.target as HTMLElement;
    if (
      !menu.contains(t) &&
      !t.closest("#cat-classic") &&
      !t.closest("#cat-walker")
    ) {
      if (!menu.classList.contains("hidden")) {
        menu.classList.add("hidden");
        void invoke("set_interactive_locked", { locked: false });
      }
    }
  });

  window.addEventListener("keydown", () => {
    machine.dispatch({ type: "KEY_ACTIVITY" });
  });

  const tick = (now: number) => {
    machine.dispatch({ type: "TICK", now: Date.now() });
    void active.tick(now);
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}

void boot();
