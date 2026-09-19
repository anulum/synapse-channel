// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE CHANNEL — pinned pi native mutation guard

import { spawn } from "node:child_process";
import { realpathSync } from "node:fs";
import { isAbsolute, relative, resolve, sep } from "node:path";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const MAX_EVENT_BYTES = 65_536;
const MAX_VERDICT_BYTES = 65_536;
const GUARD_TIMEOUT_MS = 8_000;
const READ_TOOLS = new Set(["read", "grep", "find", "ls"]);
const MUTATION_TOOLS = new Set(["write", "edit", "bash"]);

type GuardConfig = {
  binary: string;
  identity: string;
  project: string;
  repository: string;
  taskId: string;
  epoch: string;
  sessionId: string;
  uri: string;
  tokenFile: string;
};

function readConfig(): GuardConfig | null {
  const config: GuardConfig = {
    binary: process.env.SYNAPSE_PI_BIN || "synapse",
    identity: process.env.SYNAPSE_PI_IDENTITY || "",
    project: process.env.SYNAPSE_PI_PROJECT || "",
    repository: process.env.SYNAPSE_PI_REPOSITORY || "",
    taskId: process.env.SYNAPSE_PI_TASK_ID || "",
    epoch: process.env.SYNAPSE_PI_EPOCH || "",
    sessionId: process.env.SYNAPSE_PI_SESSION_ID || "",
    uri: process.env.SYNAPSE_PI_HUB_URI || "",
    tokenFile: process.env.SYNAPSE_PI_TOKEN_FILE || "",
  };
  if (
    !config.identity || !config.project || !config.repository ||
    !config.taskId || !config.sessionId || !config.uri ||
    !/^[1-9][0-9]*$/.test(config.epoch)
  ) return null;
  return config;
}

function inside(root: string, target: string): boolean {
  const part = relative(root, target);
  return part === "" || (part !== ".." && !part.startsWith(`..${sep}`) && !isAbsolute(part));
}

function readInsideRepository(config: GuardConfig, cwd: string, input: unknown, tool: string): boolean {
  if (typeof input !== "object" || input === null || !("path" in input)) {
    if (tool === "read") return false;
    input = { path: "." };
  }
  const requested = (input as { path?: unknown }).path;
  if (typeof requested !== "string" || !requested) return false;
  // Pi rewrites these path spellings before execution. Refuse an alternate
  // spelling instead of checking one path and letting the host read another.
  if (/^(?:~(?:\/|$)|@|file:\/\/)/.test(requested) || /[\u00A0\u2000-\u200A\u202F\u205F\u3000]/.test(requested)) {
    return false;
  }
  try {
    const root = realpathSync(config.repository);
    const current = realpathSync(cwd);
    if (!inside(root, current)) return false;
    const target = realpathSync(resolve(current, requested));
    return inside(root, target);
  } catch {
    return false;
  }
}

async function guard(config: GuardConfig, event: unknown): Promise<boolean> {
  const payload = JSON.stringify(event);
  if (Buffer.byteLength(payload, "utf8") > MAX_EVENT_BYTES) return false;
  const args = [
    "adapters", "pi-claim-hook", "--identity", config.identity,
    "--project", config.project, "--repository", config.repository,
    "--task-id", config.taskId, "--epoch", config.epoch,
    "--session-id", config.sessionId, "--uri", config.uri,
  ];
  if (config.tokenFile) args.push("--token-file", config.tokenFile);
  return await new Promise<boolean>((resolve) => {
    let completed = false;
    let output = "";
    const child = spawn(config.binary, args, { stdio: ["pipe", "pipe", "pipe"] });
    const finish = (allowed: boolean): void => {
      if (completed) return;
      completed = true;
      clearTimeout(timer);
      resolve(allowed);
    };
    const timer = setTimeout(() => {
      child.kill("SIGKILL");
      finish(false);
    }, GUARD_TIMEOUT_MS);
    child.on("error", () => finish(false));
    child.stdin.on("error", () => finish(false));
    child.stdout.on("error", () => finish(false));
    child.stdout.on("data", (chunk: Buffer) => {
      output += chunk.toString("utf8");
      if (Buffer.byteLength(output, "utf8") > MAX_VERDICT_BYTES) {
        child.kill("SIGKILL");
        finish(false);
      }
    });
    child.stderr.resume();
    child.on("close", (code) => {
      if (code !== 0) return finish(false);
      try {
        const verdict: unknown = JSON.parse(output);
        finish(
          typeof verdict === "object" && verdict !== null &&
          "allowed" in verdict && verdict.allowed === true,
        );
      } catch {
        finish(false);
      }
    });
    child.stdin.end(payload);
  });
}

export default function synapsePiExtension(pi: ExtensionAPI): void {
  pi.registerCommand("synapse-claim-guard-health", {
    description: "Report that the Synapse claim guard extension loaded",
    handler: async () => {},
  });
  pi.on("tool_call", async (event, ctx) => {
    const config = readConfig();
    if (!config || ctx.sessionManager.getSessionId() !== config.sessionId) {
      return { block: true, reason: "Synapse pi session or claim is not bound", terminate: true };
    }
    if (READ_TOOLS.has(event.toolName)) {
      if (readInsideRepository(config, ctx.cwd, event.input, event.toolName)) return;
      return { block: true, reason: "Synapse pi read is outside the repository", terminate: true };
    }
    if (!MUTATION_TOOLS.has(event.toolName)) {
      return { block: true, reason: "Synapse has no policy for this pi tool", terminate: true };
    }
    const allowed = await guard(config, {
      event: "tool_call",
      tool_name: event.toolName,
      tool_call_id: event.toolCallId,
      session_id: ctx.sessionManager.getSessionId(),
      cwd: ctx.cwd,
      input: event.input,
    });
    if (!allowed) {
      return { block: true, reason: "Synapse live claim denied pi mutation", terminate: true };
    }
  });
}
