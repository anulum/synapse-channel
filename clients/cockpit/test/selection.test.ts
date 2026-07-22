// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE CHANNEL — shared cockpit selection contract tests

import { describe, expect, it } from "vitest";

import {
  claimMatchesSelection,
  eventMatchesSelection,
  fleetSelectionOf,
  identityMatchesSelection,
  identityProject,
  selectionLabel,
  subjectMatchesSelection,
  taskMatchesSelection,
} from "../src/lib/selection";
import type { ClaimView } from "../src/lib/claims";
import type { CockpitEvent } from "../src/types";

const EVENT: CockpitEvent = {
  seq: 17,
  ts: 100,
  kind: "chat",
  lane: "task",
  severity: 0.4,
  actor: "SYNAPSE-CHANNEL/alpha",
  label: "message",
  taskId: "SCH-17",
  payload: { sender: "SYNAPSE-CHANNEL/alpha", target: "FLEET/beta" },
};

const CLAIM: ClaimView = {
  claim: {
    task_id: "SCH-17",
    owner: "SYNAPSE-CHANNEL/alpha",
    lease_expires_at: null,
    paths: ["clients/cockpit/src/App.tsx"],
    stale: false,
    git: null,
  },
  urgency: "held",
  inConflict: false,
  secondsToExpiry: null,
};

describe("selection labels and fleet projection", () => {
  it("renders each entity without hiding direction or sequence", () => {
    expect(selectionLabel({ kind: "agent", id: "alpha" })).toBe("alpha");
    expect(selectionLabel({ kind: "project", id: "SYNAPSE-CHANNEL" })).toBe("SYNAPSE-CHANNEL");
    expect(selectionLabel({ kind: "task", id: "SCH-17" })).toBe("SCH-17");
    expect(selectionLabel({ kind: "route", source: "alpha", target: "beta" })).toBe("alpha → beta");
    expect(selectionLabel({ kind: "event", seq: 17 })).toBe("sequence 17");
  });

  it("projects only fleet-supported selections", () => {
    expect(fleetSelectionOf({ kind: "agent", id: "alpha" })).toEqual({ kind: "agent", id: "alpha" });
    expect(fleetSelectionOf({ kind: "route", source: "alpha", target: "beta" })).toEqual({
      kind: "route",
      source: "alpha",
      target: "beta",
    });
    expect(fleetSelectionOf({ kind: "task", id: "SCH-17" })).toBeNull();
    expect(fleetSelectionOf(null)).toBeNull();
  });
});

describe("selection evidence matchers", () => {
  it("matches identities exactly, by project, and at either route end", () => {
    expect(identityProject("SYNAPSE-CHANNEL/alpha")).toBe("SYNAPSE-CHANNEL");
    expect(identityProject("CEO")).toBe("CEO");
    expect(identityMatchesSelection("SYNAPSE-CHANNEL/alpha", { kind: "agent", id: "SYNAPSE-CHANNEL/alpha" })).toBe(true);
    expect(identityMatchesSelection("SYNAPSE-CHANNEL/alpha", { kind: "project", id: "SYNAPSE-CHANNEL" })).toBe(true);
    expect(identityMatchesSelection("FLEET/beta", { kind: "route", source: "SYNAPSE-CHANNEL/alpha", target: "FLEET/beta" })).toBe(true);
    expect(identityMatchesSelection("FLEET/gamma", { kind: "agent", id: "FLEET/beta" })).toBe(false);
    expect(identityMatchesSelection("FLEET/gamma", null)).toBe(false);
  });

  it("matches task and claim evidence without free-text inference", () => {
    expect(taskMatchesSelection("SCH-17", { kind: "task", id: "SCH-17" })).toBe(true);
    expect(taskMatchesSelection("SCH-18", { kind: "task", id: "SCH-17" })).toBe(false);
    expect(claimMatchesSelection(CLAIM, { kind: "agent", id: "SYNAPSE-CHANNEL/alpha" })).toBe(true);
    expect(claimMatchesSelection(CLAIM, { kind: "task", id: "SCH-17" })).toBe(true);
    expect(claimMatchesSelection(CLAIM, { kind: "event", seq: 17 })).toBe(false);
    expect(subjectMatchesSelection("SCH-17", { kind: "task", id: "SCH-17" })).toBe(true);
  });

  it("matches event, actor, task, project, and directed-route evidence", () => {
    const { payload: _payload, ...withoutPayload } = EVENT;
    expect(eventMatchesSelection(EVENT, { kind: "event", seq: 17 })).toBe(true);
    expect(eventMatchesSelection(EVENT, { kind: "agent", id: "FLEET/beta" })).toBe(true);
    expect(eventMatchesSelection(EVENT, { kind: "task", id: "SCH-17" })).toBe(true);
    expect(eventMatchesSelection(EVENT, { kind: "project", id: "SYNAPSE-CHANNEL" })).toBe(true);
    expect(eventMatchesSelection(EVENT, { kind: "route", source: "SYNAPSE-CHANNEL/alpha", target: "FLEET/beta" })).toBe(true);
    expect(eventMatchesSelection(EVENT, { kind: "route", source: "FLEET/beta", target: "SYNAPSE-CHANNEL/alpha" })).toBe(false);
    expect(eventMatchesSelection(withoutPayload, { kind: "project", id: "FLEET" })).toBe(false);
    expect(eventMatchesSelection(EVENT, null)).toBe(false);
  });
});
