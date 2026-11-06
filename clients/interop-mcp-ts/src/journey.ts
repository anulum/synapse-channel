// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — the official MCP TypeScript SDK half of the interop journey

// Drive the official MCP TypeScript SDK through the same journey as the Python
// SDK row: initialize, claim, a refused conflicting claim, a directed message,
// a board read and a release. The Synapse processes are started by the Python
// runner; this program only connects to them. It reads one JSON configuration
// file holding paths, never secrets, and prints one JSON document with the
// content-minimised steps and the first failed step.

import { readFileSync } from "node:fs";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";
import type { Transport } from "@modelcontextprotocol/sdk/shared/transport.js";

interface ServerCommand {
  readonly command: string;
  readonly args: string[];
  readonly cwd: string;
  readonly env: Record<string, string>;
}

interface Configuration {
  readonly transport: "stdio" | "streamable-http";
  readonly servers?: ServerCommand[];
  readonly url?: string;
  readonly token_files?: string[];
  readonly first: string;
  readonly second: string;
  readonly task_id: string;
  readonly operation_ids: boolean;
  readonly marker: string;
}

interface StepRecord {
  name: string;
  route: string;
  outcome: string;
  request_keys: string[];
  response_keys: string[];
}

const REMOTE_REFUSAL = "remote MCP operation refused or unavailable";

class StepFailure extends Error {
  constructor(readonly step: string) {
    super(step);
  }
}

class Steps {
  readonly records: StepRecord[] = [];

  ok(name: string, route: string, request: object = {}, response: unknown = {}): void {
    this.records.push({
      name,
      route,
      outcome: "ok",
      request_keys: Object.keys(request).sort(),
      response_keys: response !== null && typeof response === "object" ? Object.keys(response).sort() : [],
    });
  }

  fail(name: string, route: string, outcome: string): StepFailure {
    const code = /^[A-Za-z0-9_.:-]{1,64}$/.test(outcome) ? outcome : "error";
    this.records.push({ name, route, outcome: code, request_keys: [], response_keys: [] });
    return new StepFailure(name);
  }
}

function errorCode(error: unknown): string {
  return error instanceof Error ? error.constructor.name : "error";
}

function resultText(result: { structuredContent?: unknown }): string {
  const structured = result.structuredContent;
  if (structured !== null && typeof structured === "object" && "result" in structured) {
    const value = (structured as { result: unknown }).result;
    return typeof value === "string" ? value : "";
  }
  return "";
}

async function open(steps: Steps, client: Client, transport: Transport, name: string): Promise<void> {
  try {
    await client.connect(transport);
    const tools = await client.listTools();
    const resources = await client.listResources();
    const names = new Set(tools.tools.map((tool) => tool.name));
    if (!["synapse_claim", "synapse_release", "synapse_send"].every((tool) => names.has(tool))) {
      throw steps.fail(name, "tools/list", "tools-missing");
    }
    if (!resources.resources.some((resource) => resource.uri === "synapse://board")) {
      throw steps.fail(name, "resources/list", "board-missing");
    }
  } catch (error) {
    if (error instanceof StepFailure) throw error;
    throw steps.fail(name, "initialize", errorCode(error));
  }
  steps.ok(name, "initialize");
}

async function call(
  steps: Steps,
  client: Client,
  config: Configuration,
  name: string,
  tool: string,
  args: Record<string, unknown>,
  expect: (isError: boolean, text: string) => boolean,
): Promise<void> {
  const route = `tools/call ${tool}`;
  let result: { isError?: boolean; structuredContent?: unknown };
  try {
    result = (await client.callTool({
      name: tool,
      arguments: args,
      ...(config.operation_ids ? { _meta: { "synapse/operation-id": `interop-ts-${name}` } } : {}),
    })) as { isError?: boolean; structuredContent?: unknown };
  } catch (error) {
    throw steps.fail(name, route, errorCode(error));
  }
  if (!expect(result.isError === true, resultText(result))) {
    throw steps.fail(name, route, "unexpected-result");
  }
  steps.ok(name, route, args, result.structuredContent);
}

function transports(config: Configuration): [Transport, Transport] {
  if (config.transport === "stdio") {
    const [first, second] = (config.servers ?? []).map(
      (server) => new StdioClientTransport({ command: server.command, args: server.args, cwd: server.cwd, env: server.env }),
    );
    if (first === undefined || second === undefined) throw new Error("two stdio servers are required");
    return [first, second];
  }
  const url = new URL(`${config.url ?? ""}/mcp`);
  const [first, second] = (config.token_files ?? []).map((file) => {
    const token = readFileSync(file, "utf8").trim();
    return new StreamableHTTPClientTransport(url, { requestInit: { headers: { Authorization: `Bearer ${token}` } } });
  });
  if (first === undefined || second === undefined) throw new Error("two HTTP token files are required");
  return [first, second];
}

async function journey(config: Configuration, steps: Steps): Promise<string> {
  const first = new Client({ name: "synapse-interop-ts-first", version: "1" });
  const second = new Client({ name: "synapse-interop-ts-second", version: "1" });
  try {
    let pair: [Transport, Transport];
    try {
      pair = transports(config);
    } catch (error) {
      throw steps.fail("session-open", "connect", errorCode(error));
    }
    await open(steps, first, pair[0], "initialize-first");
    await open(steps, second, pair[1], "initialize-second");
    const claim = { task_id: config.task_id, task_only: true };
    if (config.operation_ids) {
      await call(steps, first, config, "task-declared", "synapse_task_declare",
        { task_id: config.task_id, title: "interop journey" },
        (error, text) => !error && text.startsWith("declared"));
    }
    await call(steps, first, config, "claim-granted", "synapse_claim", claim,
      (error, text) => !error && text.startsWith("claim granted"));
    await call(steps, second, config, "claim-conflict-refused", "synapse_claim", claim,
      (error, text) =>
        config.operation_ids
          ? error && text === REMOTE_REFUSAL
          : !error && text.startsWith("claim denied") && text.includes(config.first));
    await call(steps, first, config, "message-sent", "synapse_send",
      { target: config.second, message: config.marker },
      (error, text) => !error && text.startsWith("sent to"));
    try {
      const board = await first.readResource({ uri: "synapse://board" });
      if (board.contents.length === 0) throw steps.fail("board-read", "resources/read", "empty");
    } catch (error) {
      if (error instanceof StepFailure) throw error;
      throw steps.fail("board-read", "resources/read", errorCode(error));
    }
    steps.ok("board-read", "resources/read synapse://board");
    await call(steps, first, config, "released", "synapse_release", { task_id: config.task_id },
      (error, text) => !error && text.startsWith("released"));
    return "";
  } catch (error) {
    if (error instanceof StepFailure) return error.step;
    throw error;
  } finally {
    await first.close().catch(() => undefined);
    await second.close().catch(() => undefined);
  }
}

async function main(): Promise<void> {
  const path = process.argv[2];
  if (path === undefined) throw new Error("usage: journey.js CONFIG.json");
  const config = JSON.parse(readFileSync(path, "utf8")) as Configuration;
  const steps = new Steps();
  const failed = await journey(config, steps);
  const sdk = JSON.parse(
    readFileSync(new URL("../node_modules/@modelcontextprotocol/sdk/package.json", import.meta.url), "utf8"),
  ) as { version: string };
  process.stdout.write(`${JSON.stringify({ client_version: sdk.version, failed_step: failed, steps: steps.records })}\n`);
}

await main();
