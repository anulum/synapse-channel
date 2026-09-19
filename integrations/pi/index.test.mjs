// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE CHANNEL — pi extension hook tests

import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, writeFileSync, chmodSync, rmSync, symlinkSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import synapsePiExtension from "./index.ts";

test("a denied write leaves its target unchanged and unknown tools stay blocked", async () => {
  const directory = mkdtempSync(join(tmpdir(), "synapse-pi-test-"));
  const binary = join(directory, "claim-fixture");
  const target = join(directory, "canary.txt");
  writeFileSync(target, "ORIGINAL\n");
  const outside = mkdtempSync(join(tmpdir(), "synapse-pi-outside-"));
  const secret = join(outside, "hub.token");
  writeFileSync(secret, "private-token\n");
  symlinkSync(secret, join(directory, "token-link"));
  mkdirSync(join(directory, "~"));
  writeFileSync(join(directory, "~", "hub.token"), "fake-inside-token\n");
  writeFileSync(binary, [
    "#!/usr/bin/env node",
    "let input = '';",
    "process.stdin.on('data', c => input += c.toString());",
    "process.stdin.on('end', () => {",
    "  const event = JSON.parse(input);",
    "  process.stdout.write(JSON.stringify({allowed: event.input.path === 'allowed.txt'}));",
    "});",
  ].join("\n"));
  chmodSync(binary, 0o700);
  Object.assign(process.env, {
    SYNAPSE_PI_BIN: binary,
    SYNAPSE_PI_IDENTITY: "PROJECT/seat",
    SYNAPSE_PI_PROJECT: "PROJECT",
    SYNAPSE_PI_REPOSITORY: directory,
    SYNAPSE_PI_TASK_ID: "TASK-1",
    SYNAPSE_PI_EPOCH: "7",
    SYNAPSE_PI_SESSION_ID: "session-1",
    SYNAPSE_PI_HUB_URI: "ws://127.0.0.1:8876",
  });
  let handler;
  synapsePiExtension({
    registerCommand: (name) => assert.equal(name, "synapse-claim-guard-health"),
    on: (_kind, callback) => { handler = callback; },
  });
  assert.equal(typeof handler, "function");
  const context = { cwd: directory, sessionManager: { getSessionId: () => "session-1" } };
  try {
    const denied = await handler({
      toolName: "write", toolCallId: "call-1", input: { path: target, content: "CHANGED" },
    }, context);
    if (!denied?.block) writeFileSync(target, "CHANGED");
    assert.equal(denied.block, true);
    assert.equal(readFileSync(target, "utf8"), "ORIGINAL\n");
    const unknown = await handler({ toolName: "custom", toolCallId: "call-2", input: {} }, context);
    assert.equal(unknown.block, true);
    const allowed = await handler({
      toolName: "write", toolCallId: "call-3", input: { path: "allowed.txt" },
    }, context);
    assert.equal(allowed, undefined);
    const stale = await handler({ toolName: "read", toolCallId: "call-4", input: {} }, {
      cwd: directory, sessionManager: { getSessionId: () => "other" },
    });
    assert.equal(stale.block, true);
    for (const path of [secret, join(directory, "token-link"), "../hub.token", "~/hub.token", `file://${secret}`]) {
      const refused = await handler({
        toolName: "read", toolCallId: "read-outside", input: { path },
      }, context);
      assert.equal(refused.block, true);
    }
    const inside = await handler({
      toolName: "read", toolCallId: "read-inside", input: { path: target },
    }, context);
    assert.equal(inside, undefined);
    const externalSearch = await handler({
      toolName: "grep", toolCallId: "search-outside", input: { pattern: "token", path: outside },
    }, context);
    assert.equal(externalSearch.block, true);
    const localSearch = await handler({
      toolName: "grep", toolCallId: "search-inside", input: { pattern: "ORIGINAL" },
    }, context);
    assert.equal(localSearch, undefined);
  } finally {
    rmSync(directory, { recursive: true, force: true });
    rmSync(outside, { recursive: true, force: true });
  }
});
