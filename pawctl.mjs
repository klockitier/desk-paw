#!/usr/bin/env node
/**
 * Tiny CLI to poke the running Desk Paw companion.
 * Usage: npm run pawctl -- working
 *    or: node pawctl.mjs done
 */
import net from "node:net";

const PORT = Number(process.env.PAWCTL_PORT || process.env.PETCTL_PORT || 19283);
const cmd = (process.argv[2] || "").toLowerCase();
const allowed = ["working", "waiting", "done", "error", "overheated", "idle", "happy", "sleeping"];

if (!cmd || cmd === "-h" || cmd === "--help") {
  console.log(`pawctl <event>

Events:
  ${allowed.join("\n  ")}

Talks to the running Desk Paw app on 127.0.0.1:${PORT}
`);
  process.exit(cmd ? 0 : 1);
}

if (!allowed.includes(cmd)) {
  console.error(`unknown event: ${cmd}`);
  process.exit(1);
}

const socket = net.createConnection({ host: "127.0.0.1", port: PORT }, () => {
  socket.write(cmd + "\n");
});

socket.setEncoding("utf8");
socket.on("data", (data) => {
  process.stdout.write(String(data));
  socket.end();
});
socket.on("error", (err) => {
  console.error(`Could not reach Desk Paw on 127.0.0.1:${PORT}`);
  console.error(err.message);
  console.error("Is the desktop app running?");
  process.exit(1);
});
