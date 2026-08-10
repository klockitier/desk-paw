/** Central pet state machine. Animation code reads state; it does not decide transitions. */

export type PetState =
  | "IDLE"
  | "BLINKING"
  | "WATCHING_CURSOR"
  | "TYPING"
  | "TYPING_AGGRESSIVE"
  | "EXHAUSTED"
  | "DRAGGED"
  | "HAPPY"
  | "SLEEPING"
  | "WORKING"
  | "WAITING"
  | "DONE"
  | "ERROR"
  | "OVERHEATED";

export type PetEvent =
  | { type: "TICK"; now: number }
  | { type: "CURSOR"; dist: number; speed: number }
  | { type: "KEY_ACTIVITY" }
  | { type: "DRAG_START" }
  | { type: "DRAG_END" }
  | { type: "PET" }
  | { type: "AGENT"; event: string }
  | { type: "FORCE"; state: PetState };

type Listener = (state: PetState, prev: PetState) => void;

const AGENT_MAP: Record<string, PetState> = {
  working: "WORKING",
  waiting: "WAITING",
  done: "DONE",
  error: "ERROR",
  overheated: "OVERHEATED",
  idle: "IDLE",
  happy: "HAPPY",
  sleeping: "SLEEPING",
};

const BLINK_EVERY_MS = 4200;
const BLINK_MS = 160;
const HAPPY_MS = 1400;
const DONE_MS = 1600;
const SLEEP_AFTER_MS = 45_000;

/* Typing intensity — derived only from *when* keys arrive. The key itself is never
   read, recorded, or transmitted; all we keep is a list of timestamps. */
/** Window the key rate is measured over. */
const RATE_WINDOW_MS = 1000;
/** Events/sec that tips the cat into a rage. */
const AGGRESSIVE_RATE = 6;
/** Rate it has to fall back to before calming down — hysteresis, so a single slow
 *  keystroke mid-burst doesn't flip the animation back and forth. */
const CALM_RATE = 4;
/** Rate that saturates `typingIntensity` at 1.0. */
const MAX_RATE = 8;
/** Keep typing for this long after the last key, so one gap doesn't flicker it. */
const TYPING_TAIL_MS = 1200;
/** How long the exhausted pose holds after a rage episode. */
const EXHAUSTED_MS = 1600;

/** States an agent event pinned us to; typing returns here instead of IDLE. */
const AGENT_STATES: PetState[] = ["WORKING", "WAITING", "ERROR", "OVERHEATED"];
/** These outrank typing entirely (see the priority note in dispatch). */
const OVERRIDE_STATES: PetState[] = ["ERROR", "OVERHEATED"];

export class PetStateMachine {
  state: PetState = "IDLE";
  /** Clock seam — overridden by tools/state-check.mjs to drive time deterministically. */
  now: () => number = () => Date.now();
  /** 0 = not typing, 1 = flat out. Read by anything that wants to react to pace. */
  typingIntensity = 0;
  private listeners = new Set<Listener>();
  private lastActivity = 0;
  /** Timestamps only — never which keys were pressed. */
  private keyTimes: number[] = [];
  private lastKeyAt = 0;
  private agentState: PetState | null = null;
  private exhaustedUntil = 0;
  private blinkAt = Date.now() + BLINK_EVERY_MS;
  private holdUntil = 0;
  private dragPrev: PetState = "IDLE";

  subscribe(fn: Listener): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  private set(next: PetState) {
    if (next === this.state) return;
    const prev = this.state;
    this.state = next;
    for (const fn of this.listeners) fn(next, prev);
  }

  dispatch(event: PetEvent) {
    // Drag always wins.
    if (event.type === "DRAG_START") {
      this.dragPrev = this.state === "DRAGGED" ? this.dragPrev : this.state;
      this.set("DRAGGED");
      return;
    }
    if (event.type === "DRAG_END") {
      this.lastActivity = this.now();
      this.set(this.dragPrev === "DRAGGED" ? "IDLE" : "IDLE");
      return;
    }
    if (this.state === "DRAGGED") return;

    if (event.type === "FORCE") {
      this.set(event.state);
      return;
    }

    if (event.type === "AGENT") {
      const mapped = AGENT_MAP[event.event.toLowerCase()];
      if (!mapped) return;
      this.lastActivity = this.now();
      // Remember where typing should return to when it finishes.
      this.agentState = AGENT_STATES.includes(mapped) ? mapped : null;
      if (mapped === "DONE") {
        this.holdUntil = this.now() + DONE_MS;
        this.set("DONE");
      } else if (mapped === "HAPPY") {
        this.holdUntil = this.now() + HAPPY_MS;
        this.set("HAPPY");
      } else {
        this.holdUntil = 0;
        this.set(mapped);
      }
      return;
    }

    if (event.type === "PET") {
      this.lastActivity = this.now();
      this.holdUntil = this.now() + HAPPY_MS;
      this.set("HAPPY");
      return;
    }

    if (event.type === "KEY_ACTIVITY") {
      const now = this.now();
      this.lastActivity = now;
      this.lastKeyAt = now;

      // Rate over the last second — timing only, no key contents.
      this.keyTimes.push(now);
      const cutoff = now - RATE_WINDOW_MS;
      while (this.keyTimes.length && this.keyTimes[0] < cutoff) this.keyTimes.shift();
      const rate = this.keyTimes.length;
      this.typingIntensity = Math.min(1, rate / MAX_RATE);

      // Priority: ERROR/OVERHEATED win outright; everything else yields to typing,
      // so the cat can type while an agent is WORKING.
      if (OVERRIDE_STATES.includes(this.state)) return;
      if (AGENT_STATES.includes(this.state)) this.agentState = this.state;

      // Pure speed: fast typing rages, slowing back down calms it.
      if (this.state === "EXHAUSTED") return;
      const raging = this.state === "TYPING_AGGRESSIVE";
      const rage = raging ? rate > CALM_RATE : rate > AGGRESSIVE_RATE;
      this.set(rage ? "TYPING_AGGRESSIVE" : "TYPING");
      return;
    }

    if (event.type === "CURSOR") {
      // Any cursor motion counts — keeps eyes awake while you work in other apps.
      this.lastActivity = this.now();
      if (
        this.state === "IDLE" ||
        this.state === "BLINKING" ||
        this.state === "SLEEPING" ||
        this.state === "WATCHING_CURSOR"
      ) {
        this.set("WATCHING_CURSOR");
      }
      return;
    }

    if (event.type === "TICK") {
      const now = event.now;

      if (this.holdUntil && now < this.holdUntil) return;
      if (this.holdUntil && now >= this.holdUntil) {
        this.holdUntil = 0;
        if (this.state === "HAPPY" || this.state === "DONE" || this.state === "BLINKING") {
          this.set("IDLE");
        }
      }

      // Typing tail: hold the pose past small gaps, then wind down.
      if (this.state === "TYPING" || this.state === "TYPING_AGGRESSIVE") {
        if (now - this.lastKeyAt <= TYPING_TAIL_MS) return;
        if (this.keyTimes.length) this.keyTimes = [];
        this.typingIntensity = 0;
        if (this.state === "TYPING_AGGRESSIVE") {
          // Rage always lands on the exhausted pose, then goes on cooldown.
          this.exhaustedUntil = now + EXHAUSTED_MS;
          this.set("EXHAUSTED");
        } else {
          this.set(this.agentState ?? "IDLE");
        }
        return;
      }

      if (this.state === "EXHAUSTED") {
        if (now < this.exhaustedUntil) return;
        this.set(this.agentState ?? "IDLE");
        return;
      }

      if (AGENT_STATES.includes(this.state)) {
        return;
      }

      if (now - this.lastActivity > SLEEP_AFTER_MS) {
        this.set("SLEEPING");
        return;
      }

      if (this.state === "WATCHING_CURSOR") {
        // Fall back to idle if nothing refreshes watching.
        if (now - this.lastActivity > 700) this.set("IDLE");
        return;
      }

      if (this.state === "IDLE" && now >= this.blinkAt) {
        this.blinkAt = now + BLINK_EVERY_MS + Math.random() * 2000;
        this.holdUntil = now + BLINK_MS;
        this.set("BLINKING");
      }
    }
  }
}
