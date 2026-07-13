// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — tests for the strict editor wire projection

import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import {
  MAX_HUB_FRAME_BYTES,
  decodeHubFrame,
} from "../src/hubProtocol.js";

const capturedFrames = readFileSync(
  new URL("./fixtures/hub_wire_v2.jsonl", import.meta.url),
  "utf8",
).trim().split("\n");

describe("decodeHubFrame real hub capture", () => {
  it("projects every editor-facing frame captured from a disposable 0.99.5 hub", () => {
    const decoded = capturedFrames.map((frame) => decodeHubFrame(frame));
    expect(decoded.every((result) => result.ok)).toBe(true);
    expect(decoded.map((result) => result.ok ? result.frame.kind : "error")).toEqual([
      "welcome",
      "roster",
      "board",
      "state-changed",
      "state",
      "state-changed",
    ]);
  });

  it("preserves only the board and claim fields the editor is authorised to render", () => {
    const board = decodeHubFrame(capturedFrames[2] ?? "");
    const state = decodeHubFrame(capturedFrames[4] ?? "");
    expect(board).toEqual({
      ok: true,
      frame: {
        kind: "board",
        tasks: [{
          taskId: "fixture-task",
          status: "open",
          title: "Captured editor contract",
        }],
      },
    });
    expect(state).toEqual({
      ok: true,
      frame: {
        kind: "state",
        claims: [{
          taskId: "vscode/fixture",
          owner: "fixture/vscode",
          worktree: "fixture-root",
          paths: ["src/example.ts"],
        }],
        generatedAt: 1783966376.2166939,
      },
    });
  });

  it("reads the current wire version from the real welcome frame", () => {
    expect(decodeHubFrame(capturedFrames[0] ?? "")).toEqual({
      ok: true,
      frame: {
        kind: "welcome",
        agents: [],
        peerProtocolVersion: 2,
      },
    });
  });
});

describe("decodeHubFrame fail-closed boundaries", () => {
  it("rejects malformed and non-object envelopes without reflecting their content", () => {
    expect(decodeHubFrame("not-json")).toEqual({ ok: false, error: "invalid-json" });
    expect(decodeHubFrame("[]")).toEqual({ ok: false, error: "invalid-envelope" });
    expect(decodeHubFrame('{"payload":"missing type"}')).toEqual({
      ok: false,
      error: "invalid-envelope",
    });
  });

  it("rejects frames wider or deeper than the Python hub accepts", () => {
    const wide = JSON.stringify({ type: "system", payload: "x".repeat(MAX_HUB_FRAME_BYTES) });
    const deep = `{"type":"system","payload":${"[".repeat(65)}0${"]".repeat(65)}}`;
    expect(decodeHubFrame(wide)).toEqual({ ok: false, error: "frame-too-large" });
    expect(decodeHubFrame(deep)).toEqual({ ok: false, error: "frame-too-deep" });
  });

  it("does not count escaped quotes, slashes, or brackets as JSON structure", () => {
    expect(decodeHubFrame(JSON.stringify({
      type: "future_editor_hint",
      payload: '\\" [ { still text',
    }))).toEqual({
      ok: true,
      frame: { kind: "ignored", wireType: "future_editor_hint" },
    });
  });

  it.each([
    { type: "welcome", online_agents: "not-an-array", protocol_version: 2 },
    { type: "who_snapshot", online_agents: ["valid", 7] },
    { type: "board_snapshot", board: { tasks: [{ title: "missing id" }] } },
    { type: "board_snapshot", board: [] },
    { type: "board_snapshot", board: { tasks: [7] } },
    { type: "board_snapshot", board: {} },
    {
      type: "state_snapshot",
      snapshot: { active_claims: [{ task_id: "t", owner: "a", paths: "src" }] },
    },
    { type: "state_snapshot", snapshot: [] },
    { type: "state_snapshot", snapshot: { active_claims: [7] } },
    { type: "state_snapshot", snapshot: {} },
    { type: "claim_granted", task_id: "" },
    { type: "release_granted" },
  ])("rejects a malformed known frame: $type", (frame) => {
    expect(decodeHubFrame(JSON.stringify(frame))).toEqual({
      ok: false,
      error: "invalid-known-frame",
    });
  });

  it("ignores an additive unknown type without projecting unknown fields", () => {
    expect(decodeHubFrame(JSON.stringify({
      type: "future_editor_hint",
      token: "must not cross the projection",
    }))).toEqual({
      ok: true,
      frame: { kind: "ignored", wireType: "future_editor_hint" },
    });
  });

  it("maps an absent or malformed advertised version to the compatibility path", () => {
    for (const protocolVersion of [undefined, true, "2", 1.5]) {
      const frame = { type: "welcome", online_agents: [] } as Record<string, unknown>;
      if (protocolVersion !== undefined) {
        frame["protocol_version"] = protocolVersion;
      }
      expect(decodeHubFrame(JSON.stringify(frame))).toEqual({
        ok: true,
        frame: { kind: "welcome", agents: [], peerProtocolVersion: null },
      });
    }
  });

  it("accepts the hub's empty label for the shared default worktree", () => {
    expect(decodeHubFrame(JSON.stringify({
      type: "state_snapshot",
      snapshot: {
        active_claims: [{
          task_id: "default-root",
          owner: "editor/seat",
          worktree: "",
          paths: ["src/live.ts"],
        }],
      },
    }))).toEqual({
      ok: true,
      frame: {
        kind: "state",
        claims: [{
          taskId: "default-root",
          owner: "editor/seat",
          worktree: "",
          paths: ["src/live.ts"],
        }],
        generatedAt: null,
      },
    });
  });
});
