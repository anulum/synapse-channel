// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — reconstructed fleet delta tests

import { describe, expect, it } from "vitest";
import type { BoardTask, DependencyChip } from "../src/lib/board";
import type { ClaimView } from "../src/lib/claims";
import { diffReplayStates } from "../src/lib/replayDiff";
import type { FleetStateAt } from "../src/lib/stateAt";
import type { CockpitEvent, EventKind } from "../src/types";

function claim(taskId: string, overrides: Partial<ClaimView> = {}): ClaimView {
  return {
    claim: {
      task_id: taskId,
      owner: "alpha",
      lease_expires_at: 20,
      paths: ["b", "a"],
      stale: false,
      git: { branch: "main", base: "main", auto_release_on: "manual" },
    },
    urgency: "held",
    inConflict: false,
    secondsToExpiry: 10,
    ...overrides,
  };
}

function dependency(taskId: string, overrides: Partial<DependencyChip> = {}): DependencyChip {
  return { taskId, status: "open", satisfied: false, missing: false, ...overrides };
}

function task(taskId: string, overrides: Partial<BoardTask> = {}): BoardTask {
  return {
    taskId,
    title: taskId,
    status: "open",
    bucket: "open",
    dependsOn: [dependency("B"), dependency("A")],
    unblocks: ["Y", "X"],
    ...overrides,
  };
}

function state(seq: number, claims: readonly ClaimView[], tasks: readonly BoardTask[]): FleetStateAt {
  return { asOfSeq: seq, logEndSeq: 99, asOfTs: seq, note: "presence omitted", claims, tasks };
}

function event(seq: number, taskId: string, kind: EventKind): CockpitEvent {
  return {
    seq,
    ts: seq,
    kind,
    lane: kind === "task" ? "task" : "claims",
    severity: 0.5,
    actor: "alpha",
    label: `${kind} ${taskId}`,
    taskId,
  };
}

describe("diffReplayStates", () => {
  it("finds added, removed, and changed claims and tasks with exact retained transitions", () => {
    const changedClaimBefore = claim("claim-changed");
    const changedClaimAfter = claim("claim-changed", {
      claim: {
        ...changedClaimBefore.claim,
        owner: "beta",
        paths: ["c"],
        lease_expires_at: 30,
        git: { branch: "anulum/work", base: "main", auto_release_on: "merge" },
      },
      urgency: "stale",
    });
    const changedTaskBefore = task("task-changed");
    const changedTaskAfter = task("task-changed", {
      title: "renamed",
      status: "done",
      bucket: "done",
      dependsOn: [dependency("C", { status: "done", satisfied: true })],
      unblocks: ["Z"],
    });
    const a = state(10, [claim("claim-removed"), changedClaimBefore], [task("task-removed"), changedTaskBefore]);
    const b = state(20, [claim("claim-added"), changedClaimAfter], [task("task-added"), changedTaskAfter]);
    const events = [
      event(9, "claim-added", "claim"),
      event(11, "claim-added", "task"),
      event(13, "claim-added", "lease"),
      event(12, "claim-added", "claim"),
      event(14, "claim-removed", "release"),
      event(15, "claim-changed", "lease"),
      event(16, "task-added", "task"),
      event(17, "task-removed", "task"),
      event(18, "task-changed", "task"),
      event(21, "task-changed", "task"),
    ];
    const diff = diffReplayStates(a, b, events);
    expect(diff).toMatchObject({ fromSeq: 10, toSeq: 20, added: 2, removed: 2, changed: 2, evidenced: 6 });
    expect(diff.deltas.find((delta) => delta.subject === "claim-added")?.eventSeq).toBe(13);
    expect(diff.deltas.find((delta) => delta.subject === "claim-removed")?.summary).toBe("claim absent from B");
    expect(diff.deltas.find((delta) => delta.subject === "claim-changed")?.summary).toContain("owner alpha → beta");
    expect(diff.deltas.find((delta) => delta.subject === "claim-changed")?.summary).toContain("path scope changed");
    expect(diff.deltas.find((delta) => delta.subject === "claim-changed")?.summary).toContain("lease changed");
    expect(diff.deltas.find((delta) => delta.subject === "claim-changed")?.summary).toContain("git binding changed");
    expect(diff.deltas.find((delta) => delta.subject === "task-added")?.summary).toBe("task appears in B");
    expect(diff.deltas.find((delta) => delta.subject === "task-changed")?.summary).toContain("dependencies changed");
  });

  it("does not invent a transition when retained events do not prove it", () => {
    const before = state(40, [claim("quiet")], [task("same")]);
    const after = state(30, [], [task("same", { dependsOn: [dependency("A"), dependency("B")], unblocks: ["X", "Y"] })]);
    const diff = diffReplayStates(before, after, [event(50, "quiet", "release")]);
    expect(diff.deltas).toEqual([
      { entity: "claim", subject: "quiet", change: "removed", summary: "claim absent from B", eventSeq: null },
    ]);
    expect(diff.evidenced).toBe(0);
  });

  it("reports an empty comparison and a bounded fallback detail", () => {
    const unchanged = state(1, [claim("same")], [task("same")]);
    expect(diffReplayStates(unchanged, state(2, [claim("same")], [task("same")]), []).deltas).toEqual([]);
    const unusual = claim("odd", {
      claim: { ...claim("odd").claim, stale: true },
      urgency: "held",
    });
    const diff = diffReplayStates(state(1, [claim("odd")], []), state(2, [unusual], []), []);
    expect(diff.deltas[0]?.summary).toBe("claim evidence changed");
  });

  it("renders absent owner and status fields without losing the direction of change", () => {
    const blankOwner = claim("owner", {
      claim: { ...claim("owner").claim, owner: "" },
    });
    const blankOwnerAfter = claim("owner-reverse", {
      claim: { ...claim("owner-reverse").claim, owner: "" },
    });
    const blankStatus = task("status", { status: "" });
    const a = state(
      1,
      [blankOwner, claim("owner-reverse")],
      [blankStatus, task("status-reverse", { status: "done" }), task("title-only")],
    );
    const b = state(
      2,
      [claim("owner"), blankOwnerAfter],
      [task("status", { status: "done" }), task("status-reverse", { status: "" }), task("title-only", { title: "renamed" })],
    );
    const diff = diffReplayStates(a, b, []);
    expect(diff.deltas.find((delta) => delta.subject === "owner")?.summary).toContain("owner — → alpha");
    expect(diff.deltas.find((delta) => delta.subject === "owner-reverse")?.summary).toContain("owner alpha → —");
    expect(diff.deltas.find((delta) => delta.subject === "status")?.summary).toContain("status — → done");
    expect(diff.deltas.find((delta) => delta.subject === "status-reverse")?.summary).toContain("status done → —");
    expect(diff.deltas.find((delta) => delta.subject === "title-only")?.summary).toBe("title changed");
  });
});
