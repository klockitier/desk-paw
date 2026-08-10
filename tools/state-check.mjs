/**
 * Runnable check for the typing-intensity state machine.
 *
 * Compiles src/state.ts to a temp dir with the local tsc, then drives it with a
 * fake clock. Run with: npm test
 */
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const out = mkdtempSync(join(tmpdir(), "pet-state-"));
execFileSync(
  "npx",
  ["tsc", "src/state.ts", "--outDir", out, "--target", "es2022", "--module", "esnext"],
  { stdio: "inherit" },
);
const { PetStateMachine } = await import(join(out, "state.js"));

let clock = 1_000_000;
const machine = new PetStateMachine();
machine.now = () => clock;

const type = (count, gapMs) => {
  for (let i = 0; i < count; i++) {
    clock += gapMs;
    machine.dispatch({ type: "KEY_ACTIVITY" });
    machine.dispatch({ type: "TICK", now: clock });
  }
};
const wait = (ms) => {
  clock += ms;
  machine.dispatch({ type: "TICK", now: clock });
};

// A: typing at a relaxed pace reads as calm
type(6, 400);
assert.equal(machine.state, "TYPING", "slow typing should be calm");
assert.ok(machine.typingIntensity > 0 && machine.typingIntensity < 1);

// B: hammering the keyboard escalates to rage
type(12, 60);
assert.equal(machine.state, "TYPING_AGGRESSIVE", "fast typing should rage");
assert.equal(machine.typingIntensity, 1);

// C: stopping lands on exhausted, then returns to idle
wait(1300);
assert.equal(machine.state, "EXHAUSTED", "rage should finish on the exhausted pose");
assert.equal(machine.typingIntensity, 0);
wait(1700);
assert.equal(machine.state, "IDLE", "exhausted should settle to idle");

// D: rage is purely speed-driven — another fast burst rages again right away
type(12, 60);
assert.equal(machine.state, "TYPING_AGGRESSIVE", "fast typing should rage again");

// E: slowing down without stopping drops back to calm
type(6, 400);
assert.equal(machine.state, "TYPING", "slowing down should calm the cat");
wait(3400);

// F: an agent state is restored after typing, and ERROR outranks typing
machine.dispatch({ type: "AGENT", event: "working" });
type(4, 300);
assert.equal(machine.state, "TYPING", "typing works while the agent is WORKING");
wait(1300);
assert.equal(machine.state, "WORKING", "typing returns to the agent state");
machine.dispatch({ type: "AGENT", event: "error" });
type(12, 60);
assert.equal(machine.state, "ERROR", "ERROR outranks typing");

rmSync(out, { recursive: true, force: true });
console.log("state machine checks passed");
