// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — multiplexed cockpit frame projection contracts

import { describe, expect, it } from "vitest";

import type { OperatorActionsState, ReceiptsState } from "../src/lib/auditFeeds";
import {
  projectEventsFrame,
  projectOperatorActionsFrame,
  projectReceiptsFrame,
  projectSnapshotFrame,
} from "../src/lib/cockpitLiveFrames";
import type {
  LiveChannel,
  LiveChannelFrame,
  LiveChannelStatus,
} from "../src/lib/liveTransport";

function frame(
  channel: LiveChannel,
  status: LiveChannelStatus,
  data?: unknown,
  detail?: string,
): LiveChannelFrame {
  return {
    version: 1,
    connectionId: "projection-test",
    sequence: 1,
    kind: "channel",
    sentAt: 12_345,
    channel,
    status,
    ...(data === undefined ? {} : { data }),
    ...(detail === undefined ? {} : { detail }),
  };
}

function receipt(seq: number, summary = `receipt ${seq}`): Record<string, unknown> {
  return {
    seq,
    ts: seq * 10,
    receipt_id: `delivery:${seq}`,
    kind: "delivery",
    subject: `target-${seq}`,
    actor: "operator/test",
    status: "delivered",
    summary,
    source_event_kind: "delivery_receipt_immediate",
  };
}

function action(seq: number): Record<string, unknown> {
  return {
    seq,
    ts: seq * 10,
    action: "release",
    direction: "out",
    status: "applied",
    applied: true,
    pending: false,
    namespace: "TEAM",
    task_id: `T-${seq}`,
    operator: "operator/test",
    agent: "",
    requester: "",
    approver: "",
    reason: "",
    detail: "released",
  };
}

describe("snapshot and event channel projections", () => {
  it("publishes a parsed snapshot and preserves heartbeat timing", () => {
    const projected = projectSnapshotFrame(
      frame("snapshot", "live", {
        fleet: { agents: { live: ["alpha"] }, claims: { active: 2 } },
      }),
    );
    expect(projected.kind).toBe("publish");
    if (projected.kind === "publish") {
      expect(projected.state.snapshot?.fleet.agents.live).toEqual(["alpha"]);
      expect(projected.state.snapshot?.fleet.claims.active).toBe(2);
      expect(projected.state.fetchedAt).toBe(12_345);
    }
    expect(projectSnapshotFrame(frame("snapshot", "unchanged"))).toEqual({
      kind: "heartbeat",
      sentAt: 12_345,
    });
  });

  it("makes stream and malformed snapshot failures explicit", () => {
    expect(projectSnapshotFrame(frame("snapshot", "error", undefined, "hub stopped"))).toEqual({
      kind: "error",
      error: "hub stopped",
    });
    expect(projectSnapshotFrame(frame("snapshot", "absent"))).toEqual({
      kind: "error",
      error: "snapshot stream is absent",
    });
    expect(projectSnapshotFrame(frame("snapshot", "live", null))).toEqual({
      kind: "error",
      error: "stream snapshot was not an object",
    });
  });

  it("selects exact, derived, and error event provenance", () => {
    expect(projectEventsFrame(frame("events", "absent"))).toEqual({
      mode: "derived",
      provenance: "absent",
      events: [],
    });
    expect(projectEventsFrame(frame("events", "error"))).toEqual({
      mode: null,
      provenance: "error",
      events: [],
    });
    expect(projectEventsFrame(frame("events", "live", null))).toEqual({
      mode: null,
      provenance: "error",
      events: [],
    });

    const live = projectEventsFrame(
      frame("events", "live", {
        events: [
          {
            seq: 7,
            ts: 70,
            kind: "chat",
            payload: { sender: "alpha", payload: "hello" },
          },
        ],
        next_cursor: 7,
        history_included: true,
      }),
    );
    expect(live.mode).toBe("tail");
    expect(live.provenance).toBe("hub");
    expect(live.events).toMatchObject([{ seq: 7, actor: "alpha", label: "hello" }]);
  });
});

describe("audit channel projections", () => {
  const receipts: ReceiptsState = {
    data: null,
    status: "connecting",
    fetchedAt: null,
    error: null,
  };
  const operatorActions: OperatorActionsState = {
    data: null,
    status: "connecting",
    fetchedAt: null,
    error: null,
  };

  it("distinguishes absent, transport-error, and malformed receipt frames", () => {
    expect(projectReceiptsFrame(receipts, frame("receipts", "absent"))).toEqual({
      ...receipts,
      status: "absent",
    });
    expect(projectReceiptsFrame(receipts, frame("receipts", "error", undefined, "offline"))).toEqual({
      ...receipts,
      status: "error",
      error: "offline",
    });
    expect(projectReceiptsFrame(receipts, frame("receipts", "error"))).toEqual({
      ...receipts,
      status: "error",
      error: "stream error",
    });
    expect(projectReceiptsFrame(receipts, frame("receipts", "live", null))).toEqual({
      ...receipts,
      status: "error",
      error: "stream payload was not parseable",
    });
  });

  it("merges, updates, orders, and bounds live receipt history", () => {
    const first = projectReceiptsFrame(
      receipts,
      frame("receipts", "live", {
        present: true,
        receipts: Array.from({ length: 100 }, (_, index) => receipt(index + 1)),
        next_cursor: 100,
      }),
    );
    const next = projectReceiptsFrame(
      first,
      frame("receipts", "live", {
        present: true,
        receipts: [receipt(50, "updated"), receipt(101)],
        next_cursor: 101,
      }),
    );
    expect(next.status).toBe("live");
    expect(next.fetchedAt).toBe(12_345);
    expect(next.data).toHaveLength(100);
    expect(next.data?.[0]?.seq).toBe(101);
    expect(next.data?.find((row) => row.seq === 50)?.summary).toBe("updated");
    expect(next.data?.at(-1)?.seq).toBe(2);
  });

  it("projects operator actions through the same bounded state contract", () => {
    expect(projectOperatorActionsFrame(operatorActions, frame("operator_actions", "absent"))).toEqual({
      ...operatorActions,
      status: "absent",
    });
    expect(
      projectOperatorActionsFrame(
        operatorActions,
        frame("operator_actions", "error", undefined, "relay offline"),
      ),
    ).toEqual({ ...operatorActions, status: "error", error: "relay offline" });
    expect(projectOperatorActionsFrame(operatorActions, frame("operator_actions", "live", []))).toEqual({
      ...operatorActions,
      status: "error",
      error: "stream payload was not parseable",
    });

    const live = projectOperatorActionsFrame(
      operatorActions,
      frame("operator_actions", "live", {
        present: true,
        actions: [action(4), action(2)],
        next_cursor: 4,
      }),
    );
    expect(live).toMatchObject({ status: "live", fetchedAt: 12_345, error: null });
    expect(live.data?.map((row) => row.seq)).toEqual([4, 2]);
  });
});
