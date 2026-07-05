// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — detail selector tests

import { describe, expect, it } from "vitest";
import type { BoardTask } from "../src/lib/board";
import type { ClaimView } from "../src/lib/claims";
import type { DeadLetterView } from "../src/lib/deadLetters";
import { agentDetail, DETAIL_EVENTS_SHOWN, taskDetail } from "../src/lib/detail";
import type { RosterEntry } from "../src/lib/roster";
import type { CockpitEvent, ClaimRecord } from "../src/types";

function event(seq: number, overrides: Partial<CockpitEvent> = {}): CockpitEvent {
  return {
    seq,
    ts: seq,
    kind: "claim",
    lane: "claims",
    severity: 0.5,
    actor: "a",
    label: "x",
    taskId: "t",
    ...overrides,
  };
}

function claimView(owner: string, taskId: string): ClaimView {
  const record: ClaimRecord = {
    task_id: taskId,
    owner,
    lease_expires_at: null,
    paths: ["src/x.ts"],
    stale: false,
    git: null,
  };
  return { claim: record, urgency: "held", inConflict: false, secondsToExpiry: null };
}

function rosterEntry(agent: string): RosterEntry {
  return {
    agent,
    status: "idle",
    online: true,
    activeClaims: [],
    staleClaims: [],
    paths: [],
    inConflict: false,
    wakerMissing: false,
  };
}

function boardTask(taskId: string): BoardTask {
  return { taskId, title: "T", status: "open", bucket: "open", dependsOn: [], unblocks: [] };
}

const LETTER: DeadLetterView = { target: "a", count: 2, lastSender: "b", lastTs: 5 };

describe("agentDetail", () => {
  it("joins the roster row, its claims, its unread mailbox, and its events", () => {
    const detail = agentDetail(
      "a",
      [rosterEntry("a"), rosterEntry("b")],
      [claimView("a", "t1"), claimView("b", "t2")],
      [LETTER, { ...LETTER, target: "b" }],
      [event(3, { actor: "a" }), event(2, { actor: "b" }), event(1, { actor: "a" })],
    );
    expect(detail.entry?.agent).toBe("a");
    expect(detail.claims.map((view) => view.claim.task_id)).toEqual(["t1"]);
    expect(detail.deadLetters).toEqual([LETTER]);
    expect(detail.recentEvents.map((entry) => entry.seq)).toEqual([3, 1]);
    expect(detail.moreEvents).toBe(0);
  });

  it("caps the event list and states the honest remainder; unknown agent is null-entry", () => {
    const events = Array.from({ length: DETAIL_EVENTS_SHOWN + 4 }, (_, index) =>
      event(index + 1, { actor: "ghost" }),
    );
    const detail = agentDetail("ghost", [], [], [], events);
    expect(detail.entry).toBeNull();
    expect(detail.recentEvents).toHaveLength(DETAIL_EVENTS_SHOWN);
    expect(detail.moreEvents).toBe(4);
  });
});

describe("taskDetail", () => {
  it("joins the board card, the holding claim, and the task's history", () => {
    const detail = taskDetail(
      "t1",
      [boardTask("t1"), boardTask("t2")],
      [claimView("a", "t1")],
      [event(2, { taskId: "t1" }), event(1, { taskId: "other" })],
    );
    expect(detail.task?.taskId).toBe("t1");
    expect(detail.claim?.claim.owner).toBe("a");
    expect(detail.recentEvents.map((entry) => entry.seq)).toEqual([2]);
  });

  it("answers honestly for a task the board does not carry", () => {
    const detail = taskDetail("missing", [], [], []);
    expect(detail.task).toBeNull();
    expect(detail.claim).toBeNull();
    expect(detail.recentEvents).toEqual([]);
    expect(detail.moreEvents).toBe(0);
  });
});
