// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — real-hub tests for the editor WebSocket transport

import { type ChildProcess, spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
import { existsSync } from "node:fs";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { createConnection, createServer } from "node:net";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it, vi } from "vitest";
import { WebSocket as NodeWebSocket } from "ws";
import { type HubConnectionState } from "../src/connectionState.js";
import { type HubFrame } from "../src/hubProtocol.js";
import { HubTransport } from "../src/hubTransport.js";

const TEST_DIRECTORY = dirname(fileURLToPath(import.meta.url));
const EXTENSION_ROOT = resolve(TEST_DIRECTORY, "..");
const REPOSITORY_ROOT = resolve(EXTENSION_ROOT, "..", "..");

function delay(milliseconds: number): Promise<void> {
  return new Promise((resolveDelay) => setTimeout(resolveDelay, milliseconds));
}

async function reservePort(): Promise<number> {
  return await new Promise<number>((resolvePort, reject) => {
    const server = createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      if (address === null || typeof address === "string") {
        server.close();
        reject(new Error("Could not reserve a hub test port."));
        return;
      }
      server.close((error) => error ? reject(error) : resolvePort(address.port));
    });
  });
}

async function probeTcp(port: number): Promise<void> {
  await new Promise<void>((resolveProbe, reject) => {
    const socket = createConnection({ host: "127.0.0.1", port });
    socket.setTimeout(300);
    socket.once("connect", () => {
      socket.destroy();
      resolveProbe();
    });
    socket.once("timeout", () => {
      socket.destroy();
      reject(new Error("Hub readiness probe timed out."));
    });
    socket.once("error", reject);
  });
}

async function waitFor(
  predicate: () => boolean,
  label: string,
  diagnostic?: () => string,
  timeoutMs = 10_000,
): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (predicate()) {
      return;
    }
    await delay(25);
  }
  const suffix = diagnostic === undefined ? "" : ` Observed: ${diagnostic()}.`;
  throw new Error(`Timed out waiting for ${label}.${suffix}`);
}

async function startHub(
  python: string,
  port: number,
  tokenFile: string,
  database: string,
): Promise<ChildProcess> {
  const hub = spawn(
    python,
    [
      "-m",
      "synapse_channel.cli",
      "hub",
      "--host",
      "127.0.0.1",
      "--port",
      String(port),
      "--token-file",
      tokenFile,
      "--db",
      database,
      // Keep identity pins in memory: the default persists trust-on-first-use pins
      // to a shared ~/synapse/identity-pins.json, so a machine that has already
      // pinned this identity elsewhere would refuse the test's unsigned client.
      "--identity-pins",
      "",
    ],
    {
      cwd: REPOSITORY_ROOT,
      env: { ...process.env, PYTHONUNBUFFERED: "1" },
      stdio: "ignore",
    },
  );
  for (let attempt = 0; attempt < 100; attempt += 1) {
    if (hub.exitCode !== null || hub.signalCode !== null) {
      throw new Error("Real hub exited before the transport test connected.");
    }
    try {
      await probeTcp(port);
      return hub;
    } catch {
      await delay(50);
    }
  }
  throw new Error("Real hub did not become ready for the transport test.");
}

async function stopHub(hub: ChildProcess): Promise<void> {
  if (hub.exitCode !== null || hub.signalCode !== null) {
    return;
  }
  const exit = new Promise<void>((resolveExit) => hub.once("exit", () => resolveExit()));
  hub.kill("SIGINT");
  await Promise.race([exit, delay(3_000)]);
  if (hub.exitCode === null && hub.signalCode === null) {
    hub.kill("SIGKILL");
    await exit;
  }
}

describe("HubTransport real process boundary", () => {
  it("fails closed before welcome, claims through the hub, and reconnects after restart", async () => {
    const temporary = await mkdtemp(join(tmpdir(), "synapse-vscode-transport-"));
    const token = `transport-${randomUUID()}`;
    const tokenFile = join(temporary, "hub.token");
    const database = join(temporary, "hub.db");
    const localPython = join(REPOSITORY_ROOT, ".venv", "bin", "python");
    const python = process.env["SYNAPSE_PYTHON"]
      ?? (existsSync(localPython) ? localPython : "python3");
    const port = await reservePort();
    await writeFile(tokenFile, token, { flag: "wx", mode: 0o600 });
    let hub = await startHub(python, port, tokenFile, database);
    const states: HubConnectionState[] = [];
    const frames: HubFrame[] = [];
    vi.stubGlobal("WebSocket", NodeWebSocket);
    const transport = new HubTransport({
      onConnectionState: (state) => states.push(state),
      onFrame: (frame) => frames.push(frame),
    });
    try {
      transport.connect(`ws://127.0.0.1:${port}`, "transport/vscode", token);
      expect(transport.mutate("claim", {
        task_id: "vscode/transport",
        paths: ["src/live.ts"],
      })).toEqual({
        sent: false,
        reason: "SYNAPSE mutation withheld because the hub state is not live and compatible.",
      });
      await waitFor(
        () => transport.state().phase === "live",
        "the first live handshake",
        () =>
          `phase=${transport.state().phase}; transitions=${states.map((state) => state.phase).join(",")}; `
          + `frames=${frames.map((frame) => frame.kind).join(",")}`,
      );

      expect(transport.mutate("claim", {
        task_id: "vscode/transport",
        paths: ["src/live.ts"],
      })).toEqual({ sent: true });
      await waitFor(
        () => frames.some((frame) => frame.kind === "state-changed"
          && frame.operation === "claim"),
        "the real claim grant",
      );
      expect(transport.request("state_request")).toBe(true);
      await waitFor(
        () => frames.some((frame) => frame.kind === "state"
          && frame.claims.some((claim) => claim.taskId === "vscode/transport")),
        "the real active-claim snapshot",
        () => `phase=${transport.state().phase}; frames=${frames.map((frame) => frame.kind).join(",")}`,
      );

      await stopHub(hub);
      await waitFor(
        () => states.some((state) => state.phase === "disconnected"),
        "the disconnected transition",
      );
      hub = await startHub(python, port, tokenFile, database);
      await waitFor(
        () => transport.state().phase === "live",
        "the automatic post-restart handshake",
        () => `phase=${transport.state().phase}; transitions=${states.map((state) => state.phase).join(",")}`,
        20_000,
      );
      expect(transport.state().phase).toBe("live");
    } finally {
      transport.dispose();
      vi.unstubAllGlobals();
      await stopHub(hub);
      await rm(temporary, { recursive: true, force: true });
    }
  });
});
