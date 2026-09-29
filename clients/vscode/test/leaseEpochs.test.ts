// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — the editor's release names the epoch of its own grant

import { describe, expect, it } from "vitest";

import { decodeHubFrame, type HubStateChangedFrame } from "../src/hubProtocol.js";
import { LeaseEpochMemory, releaseFields } from "../src/leaseEpochs.js";

function changed(frame: Record<string, unknown>): HubStateChangedFrame {
  const decoded = decodeHubFrame(JSON.stringify(frame));
  if (!decoded.ok || decoded.frame.kind !== "state-changed") {
    throw new Error("expected a state-changed frame");
  }
  return decoded.frame;
}

describe("LeaseEpochMemory", () => {
  it("keeps only epochs granted to this identity and forgets ended leases", () => {
    const memory = new LeaseEpochMemory();
    const me = "P/editor";
    memory.observe(changed({ type: "claim_granted", task_id: "t", owner: me, epoch: 4 }), me);
    // foreign or malformed grants change nothing
    memory.observe(changed({ type: "claim_granted", task_id: "t", owner: "P/bob", epoch: 9 }), me);
    memory.observe(changed({ type: "claim_granted", task_id: "t", owner: me, epoch: "9" }), me);
    memory.observe(changed({ type: "claim_granted", task_id: "t", owner: me, epoch: -1 }), me);
    memory.observe(changed({ type: "claim_granted", task_id: "t", owner: me, epoch: 1.5 }), me);
    memory.observe(changed({ type: "claim_granted", task_id: "t", epoch: 9 }), me);
    expect(memory.epochFor("t")).toBe(4);
    expect(releaseFields("t", memory)).toEqual({ task_id: "t", epoch: 4 });

    memory.observe(changed({ type: "handoff_granted", task_id: "h", owner: me, epoch: 6 }), me);
    expect(memory.epochFor("h")).toBe(6);
    memory.observe(changed({ type: "handoff_granted", task_id: "h", owner: "P/bob", epoch: 7 }), me);
    expect(memory.epochFor("h")).toBeUndefined();

    memory.observe(changed({ type: "release_granted", task_id: "t", owner: me }), me);
    expect(releaseFields("t", memory)).toEqual({ task_id: "t" });

    memory.observe(changed({ type: "claim_granted", task_id: "u", owner: me, epoch: 0 }), me);
    expect(memory.epochFor("u")).toBe(0);
    memory.clear();
    expect(memory.epochFor("u")).toBeUndefined();
  });

  it("decodes grant owners and epochs, including a handoff", () => {
    expect(changed({ type: "handoff_granted", task_id: "h", owner: "P/a", epoch: 3 })).toEqual({
      kind: "state-changed",
      operation: "handoff",
      taskId: "h",
      owner: "P/a",
      epoch: 3,
    });
    expect(changed({ type: "release_granted", task_id: "t" })).toEqual({
      kind: "state-changed",
      operation: "release",
      taskId: "t",
      owner: null,
      epoch: null,
    });
    expect(decodeHubFrame(JSON.stringify({ type: "handoff_granted" }))).toEqual({
      ok: false,
      error: "invalid-known-frame",
    });
  });
});
