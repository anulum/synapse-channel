// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — tests for the VS Code extension fleet model

import { describe, expect, it } from "vitest";
import {
  HUB_STALE_AFTER_MS,
  acceptWelcome,
  disconnectedConnection,
  refreshConnectionFreshness,
} from "../src/connectionState.js";
import {
  boardItems,
  claimMarks,
  hubHealth,
  hubProjectionChanged,
  statusBarText,
} from "../src/fleetModel.js";

describe("hubHealth", () => {
  it("is down when the connection is closed", () => {
    expect(hubHealth(disconnectedConnection(), ["a"])).toEqual({
      state: "down",
      label: "hub offline",
    });
  });

  it("is degraded when up but only waiters are present", () => {
    const connection = acceptWelcome(disconnectedConnection(), 2, 1_000);
    expect(hubHealth(connection, ["alpha-rx", "beta-rx"])).toEqual({
      state: "degraded",
      label: "hub up · no live agents",
    });
  });

  it("is ok and counts only live agents", () => {
    const connection = acceptWelcome(disconnectedConnection(), 2, 1_000);
    expect(hubHealth(connection, ["alpha", "alpha-rx", "beta"])).toEqual({
      state: "ok",
      label: "hub up · 2 live",
    });
  });

  it("makes stale last-good state explicit", () => {
    const live = acceptWelcome(disconnectedConnection(), 2, 1_000);
    const stale = refreshConnectionFreshness(live, 1_001 + HUB_STALE_AFTER_MS);
    expect(hubHealth(stale, ["alpha"], 6_000 + HUB_STALE_AFTER_MS)).toEqual({
      state: "warning",
      label: "hub stale · last update 70s old",
    });
  });

  it("names trust, wire, negotiation, and retained-state transitions", () => {
    const disconnected = disconnectedConnection();
    expect(hubHealth({ ...disconnected, phase: "identity-mismatch" }, [])).toEqual({
      state: "down",
      label: "identity trust mismatch",
    });
    expect(hubHealth({ ...disconnected, phase: "incompatible" }, [])).toEqual({
      state: "down",
      label: "wire contract incompatible",
    });
    expect(hubHealth({ ...disconnected, phase: "negotiating" }, [])).toEqual({
      state: "warning",
      label: "negotiating hub protocol",
    });
    expect(hubHealth({ ...disconnected, lastFrameAt: 1_000 }, [])).toEqual({
      state: "down",
      label: "hub offline · last-good state retained",
    });
  });

  it("renders unknown age when stale evidence has no timestamp", () => {
    const stale = { ...disconnectedConnection(), phase: "stale" as const };
    expect(hubHealth(stale, [])).toEqual({
      state: "warning",
      label: "hub stale · last update unknown age",
    });
  });
});

describe("boardItems", () => {
  it("sorts validated tasks by id", () => {
    const items = boardItems([
      { taskId: "T2", status: "open", title: "Two" },
      { taskId: "T1", status: "open", title: "" },
    ]);
    expect(items.map((i) => i.id)).toEqual(["T1", "T2"]);
  });

  it("labels with the title when present and defaults the status", () => {
    const [withTitle, bare] = boardItems([
      { taskId: "T1", status: "open", title: "Build" },
      { taskId: "T2", status: "open", title: "" },
    ]);
    expect(withTitle).toEqual({ id: "T1", status: "open", label: "T1 — Build" });
    expect(bare).toEqual({ id: "T2", status: "open", label: "T2" });
  });
});

describe("claimMarks", () => {
  it("flattens paths, flags own claims, and keeps the first owner per path", () => {
    const marks = claimMarks(
      [
        { taskId: "mine", owner: "me", worktree: "default", paths: ["src/a.ts", ""] },
        { taskId: "theirs", owner: "you", worktree: "default", paths: ["src/b.ts", "src/a.ts"] },
        { taskId: "unknown", owner: "", worktree: "default", paths: ["src/c.ts"] },
      ],
      "me",
    );
    expect(marks).toEqual([
      { worktree: "default", path: "src/a.ts", owner: "me", mine: true },
      { worktree: "default", path: "src/b.ts", owner: "you", mine: false },
      { worktree: "default", path: "src/c.ts", owner: "", mine: false },
    ]);
  });

  it("keeps identical paths in different worktrees distinct", () => {
    expect(claimMarks([
      { taskId: "one", owner: "me", worktree: "/repo-one", paths: ["src/a.ts"] },
      { taskId: "two", owner: "me", worktree: "/repo-two", paths: ["src/a.ts"] },
    ], "me")).toHaveLength(2);
  });

  it("tolerates an empty validated path set", () => {
    expect(claimMarks([
      { taskId: "mine", owner: "me", worktree: "default", paths: [] },
    ], "me")).toEqual([]);
  });
});

describe("hubProjectionChanged", () => {
  const current = { uri: "ws://hub-one", identity: "editor/seat" };

  it("retains last-good data only for the same hub and identity", () => {
    expect(hubProjectionChanged(current, { ...current })).toBe(false);
    expect(hubProjectionChanged(undefined, current)).toBe(true);
    expect(hubProjectionChanged(current, { ...current, uri: "ws://hub-two" })).toBe(true);
    expect(hubProjectionChanged(current, { ...current, identity: "editor/other" })).toBe(true);
  });
});

describe("statusBarText", () => {
  it("uses an ok icon and appends own claim count", () => {
    expect(statusBarText({ state: "ok", label: "hub up · 1 live" }, 2)).toBe(
      "$(broadcast) SYNAPSE: hub up · 1 live · 2 mine",
    );
  });

  it("uses a warning icon when degraded and omits a zero claim count", () => {
    expect(statusBarText({ state: "degraded", label: "x" }, 0)).toBe("$(warning) SYNAPSE: x");
  });

  it("uses an error icon when down", () => {
    expect(statusBarText({ state: "down", label: "hub offline" }, 0)).toBe(
      "$(error) SYNAPSE: hub offline",
    );
  });
});
