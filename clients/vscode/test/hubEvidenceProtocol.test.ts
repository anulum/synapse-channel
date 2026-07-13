// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — evidence fields through the editor wire decoder

import { describe, expect, it } from "vitest";
import { decodeHubFrame } from "../src/hubProtocol.js";

function decode(frame: Record<string, unknown>) {
  return decodeHubFrame(JSON.stringify(frame));
}

describe("coordination evidence wire projection", () => {
  it("projects mailbox and consume-liveness evidence from a who snapshot", () => {
    expect(decode({
      type: "who_snapshot",
      online_agents: ["alpha", "alpha-rx"],
      mailbox_pending: { alpha: 2, beta: 0 },
      agent_liveness: {
        alpha: {
          proven_live: false,
          has_live_waiter: false,
          last_reaction_age: 94.5,
        },
        "alpha-rx": {
          proven_live: true,
          has_live_waiter: true,
          last_reaction_age: null,
        },
      },
    })).toEqual({
      ok: true,
      frame: {
        kind: "roster",
        agents: ["alpha", "alpha-rx"],
        evidence: {
          mailbox: [{ identity: "alpha", count: 2 }, { identity: "beta", count: 0 }],
          liveness: [
            { agent: "alpha", provenLive: false, hasLiveWaiter: false, lastReactionAge: 94.5 },
            { agent: "alpha-rx", provenLive: true, hasLiveWaiter: true, lastReactionAge: null },
          ],
          mailboxAvailable: true,
          livenessAvailable: true,
        },
      },
    });
  });

  it("distinguishes an additive presence event from an authoritative evidence snapshot", () => {
    expect(decode({ type: "presence_update", online_agents: ["alpha"] })).toEqual({
      ok: true,
      frame: { kind: "roster", agents: ["alpha"], evidence: null },
    });
    expect(decode({
      type: "who_snapshot",
      online_agents: ["alpha"],
      mailbox_pending: null,
    })).toEqual({
      ok: true,
      frame: {
        kind: "roster",
        agents: ["alpha"],
        evidence: {
          mailbox: [],
          liveness: [],
          mailboxAvailable: false,
          livenessAvailable: false,
        },
      },
    });
    expect(decode({ type: "who_snapshot", online_agents: ["alpha"] })).toEqual({
      ok: true,
      frame: {
        kind: "roster",
        agents: ["alpha"],
        evidence: {
          mailbox: [],
          liveness: [],
          mailboxAvailable: false,
          livenessAvailable: false,
        },
      },
    });
  });

  it("projects retained approvals and release receipts from the board", () => {
    expect(decode({
      type: "board_snapshot",
      board: {
        tasks: [{ task_id: "release-1", status: "done", title: "Release" }],
        progress: [
          {
            task_id: "release-1",
            author: "reviewer",
            kind: "approval",
            text: "approval subject=release-1 state=approved",
            posted_at: 10,
          },
          {
            task_id: "release-1",
            author: "owner",
            kind: "assessment",
            text: "release receipt: evidence=focused tests",
            posted_at: 11,
          },
        ],
      },
    })).toEqual({
      ok: true,
      frame: {
        kind: "board",
        tasks: [{ taskId: "release-1", status: "done", title: "Release" }],
        progress: [
          {
            taskId: "release-1",
            author: "reviewer",
            kind: "approval",
            text: "approval subject=release-1 state=approved",
            postedAt: 10,
          },
          {
            taskId: "release-1",
            author: "owner",
            kind: "assessment",
            text: "release receipt: evidence=focused tests",
            postedAt: 11,
          },
        ],
      },
    });
  });

  it("projects dark letters and pending relay quorum from state", () => {
    expect(decode({
      type: "state_snapshot",
      snapshot: {
        active_claims: [],
        generated_at: 12,
        dead_letters: [{ target: "dark", count: 3, last_ts: 11, last_sender: "operator" }],
        pending_relay_approvals: [{
          action: "release",
          namespace: "repo",
          task_id: "T1",
          requester: "operator-a",
        }],
      },
    })).toEqual({
      ok: true,
      frame: {
        kind: "state",
        claims: [],
        generatedAt: 12,
        deadLetters: [{ target: "dark", count: 3, lastTs: 11, lastSender: "operator" }],
        relayApprovals: [{
          action: "release",
          namespace: "repo",
          taskId: "T1",
          requester: "operator-a",
        }],
      },
    });
  });

  it.each([
    { type: "who_snapshot", online_agents: [], mailbox_pending: { alpha: -1 } },
    { type: "who_snapshot", online_agents: [], mailbox_pending: [] },
    { type: "who_snapshot", online_agents: [], agent_liveness: { alpha: { proven_live: "yes" } } },
    { type: "who_snapshot", online_agents: [], agent_liveness: { alpha: 7 } },
    {
      type: "who_snapshot",
      online_agents: [],
      agent_liveness: {
        alpha: { proven_live: true, has_live_waiter: false, last_reaction_age: 0 },
      },
    },
    {
      type: "who_snapshot",
      online_agents: ["alpha"],
      agent_liveness: {
        alpha: { proven_live: false, has_live_waiter: true, last_reaction_age: 100 },
      },
    },
    { type: "board_snapshot", board: { tasks: [], progress: [{ task_id: "T" }] } },
    { type: "state_snapshot", snapshot: { active_claims: [], dead_letters: [{ count: 1 }] } },
    { type: "state_snapshot", snapshot: { active_claims: [], dead_letters: [7] } },
    {
      type: "state_snapshot",
      snapshot: {
        active_claims: [],
        dead_letters: [{ target: "dark", count: 1, last_ts: -1, last_sender: "sender" }],
      },
    },
    {
      type: "state_snapshot",
      snapshot: {
        active_claims: [],
        dead_letters: [{ target: "dark", count: 1, last_ts: 1, last_sender: "" }],
      },
    },
    {
      type: "state_snapshot",
      snapshot: { active_claims: [], pending_relay_approvals: [{ action: "release" }] },
    },
    {
      type: "state_snapshot",
      snapshot: { active_claims: [], pending_relay_approvals: [7] },
    },
    {
      type: "state_snapshot",
      snapshot: {
        active_claims: [],
        pending_relay_approvals: [{
          action: "release",
          namespace: "repo",
          task_id: "T1",
          requester: "",
        }],
      },
    },
  ])("fails closed when a present evidence field is malformed", (frame) => {
    expect(decode(frame)).toEqual({ ok: false, error: "invalid-known-frame" });
  });

  it.each(["ledger_progress_posted", "ledger_task_posted", "ledger_task_updated"])(
    "maps %s to an authoritative board refresh",
    (type) => {
      expect(decode({ type })).toEqual({ ok: true, frame: { kind: "board-changed" } });
    },
  );
});
