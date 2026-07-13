// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — editor evidence projection behaviour

import { describe, expect, it } from "vitest";
import { disconnectedConnection, type HubConnectionState } from "../src/connectionState.js";
import { evidenceItems, type EvidenceInput } from "../src/evidenceModel.js";

function input(overrides: Partial<EvidenceInput> = {}): EvidenceInput {
  return {
    connection: {
      phase: "live",
      peerProtocolVersion: 2,
      effectiveProtocolVersion: 2,
      warning: undefined,
      lastFrameAt: 1,
    },
    progress: [],
    mailbox: [],
    liveness: [],
    deadLetters: [],
    relayApprovals: [],
    mailboxAvailable: true,
    livenessAvailable: true,
    ...overrides,
  };
}

describe("evidenceItems", () => {
  it("uses the latest canonical approval state for each subject", () => {
    const items = evidenceItems(input({
      progress: [
        {
          taskId: "release-1",
          author: "requester",
          kind: "approval",
          text: "approval subject=release-1 state=requested :: review required",
          postedAt: 1,
        },
        {
          taskId: "release-1",
          author: "reviewer",
          kind: "approval",
          text: "approval subject=release-1 state=approved :: exact SHA checked",
          postedAt: 2,
        },
        {
          taskId: "release-2",
          author: "reviewer",
          kind: "approval",
          text: "non-canonical approval wording",
          postedAt: 3,
        },
        {
          taskId: "release-1",
          author: "stale-reviewer",
          kind: "approval",
          text: "approval subject=release-1 state=rejected :: stale retained order",
          postedAt: 0,
        },
      ],
    }));
    expect(items).toEqual([{
      id: expect.stringMatching(/^approval:[0-9a-f]{20}$/),
      category: "approval",
      severity: "ok",
      label: "Ledger approval claim approved: release-1",
      description: "self-attested by reviewer",
      detail: "exact SHA checked",
    }]);
  });

  it("labels board receipts as retained rather than complete history", () => {
    const items = evidenceItems(input({
      progress: [
        {
          taskId: "T1",
          author: "owner-a",
          kind: "assessment",
          text: "release receipt: evidence=first",
          postedAt: 1,
        },
        {
          taskId: "T1",
          author: "owner-b",
          kind: "assessment",
          text: "release receipt: evidence=latest; confidence=high",
          postedAt: 2,
        },
        {
          taskId: "T2",
          author: "owner-c",
          kind: "note",
          text: "release receipt: not an assessment",
          postedAt: 3,
        },
        {
          taskId: "T1",
          author: "stale-owner",
          kind: "assessment",
          text: "release receipt: evidence=stale retained order",
          postedAt: 0,
        },
      ],
    }));
    expect(items).toEqual([{
      id: expect.stringMatching(/^receipt:[0-9a-f]{20}$/),
      category: "receipt",
      severity: "info",
      label: "Retained release-receipt claim: T1",
      description: "self-attested by owner-b",
      detail: "release receipt: evidence=latest; confidence=high No release authority is inferred.",
    }]);
  });

  it("keeps delivery history, mailbox backlog, and current wake proof distinct", () => {
    const items = evidenceItems(input({
      deadLetters: [{ target: "dark", count: 4, lastTs: 10, lastSender: "sender" }],
      mailbox: [{ identity: "dark", count: 6 }, { identity: "quiet", count: 2 }],
      liveness: [
        { agent: "dark", provenLive: false, hasLiveWaiter: false, lastReactionAge: 95.8 },
        { agent: "live", provenLive: true, hasLiveWaiter: true, lastReactionAge: null },
      ],
    }));
    expect(items).toHaveLength(4);
    expect(items.find((item) => item.category === "delivery")).toMatchObject({
      severity: "critical",
      label: "Undeliverable messages retained: dark",
      description: "4 recorded",
    });
    expect(items.find((item) => item.category === "wake")).toMatchObject({
      severity: "critical",
      label: "Wake capability not proven: dark",
    });
    expect(items.filter((item) => item.category === "mailbox")).toEqual([
      expect.objectContaining({
        label: "Mailbox pending: dark",
        severity: "warning",
      }),
      expect.objectContaining({
        label: "Mailbox pending: quiet",
        severity: "warning",
      }),
    ]);
  });

  it("does not call recovered targets dark because retained delivery evidence remains", () => {
    const items = evidenceItems(input({
      deadLetters: [{ target: "recovered", count: 2, lastTs: 10, lastSender: "sender" }],
      mailbox: [{ identity: "recovered", count: 1 }],
      liveness: [{
        agent: "recovered",
        provenLive: true,
        hasLiveWaiter: true,
        lastReactionAge: 1,
      }],
    }));
    expect(items).toEqual([
      expect.objectContaining({
        category: "delivery",
        severity: "warning",
        detail: expect.stringContaining("currently proven wake-capable"),
      }),
      expect.objectContaining({
        category: "mailbox",
        severity: "info",
        detail: expect.stringContaining("currently proven wake-capable"),
      }),
    ]);
  });

  it("keeps unknown reaction age explicit and ignores zero-only mailbox rows", () => {
    const items = evidenceItems(input({
      deadLetters: [{ target: "empty-letter", count: 0, lastTs: 1, lastSender: "sender" }],
      mailbox: [{ identity: "empty", count: 0 }],
      liveness: [{
        agent: "unknown-age",
        provenLive: false,
        hasLiveWaiter: false,
        lastReactionAge: null,
      }],
    }));
    expect(items).toEqual([expect.objectContaining({
      category: "wake",
      detail: "Present on the roster but not proven wake-capable (reaction age unavailable).",
    })]);
  });

  it("surfaces relay quorum before informational receipts", () => {
    const items = evidenceItems(input({
      relayApprovals: [{
        action: "release",
        namespace: "repository",
        taskId: "T1",
        requester: "operator-a",
      }],
      progress: [{
        taskId: "T1",
        author: "operator-a",
        kind: "assessment",
        text: "release receipt: evidence=focused tests",
        postedAt: 1,
      }],
    }));
    expect(items.map((item) => item.category)).toEqual(["approval", "receipt"]);
    expect(items[0]?.detail).toContain("operator quorum is incomplete");
  });

  it.each([
    [disconnectedConnection(), "critical", "Hub offline"],
    [{ ...disconnectedConnection(), phase: "negotiating" }, "warning", "Hub reconnecting"],
    [{ ...disconnectedConnection(), phase: "stale" }, "warning", "Hub evidence is stale"],
    [{ ...disconnectedConnection(), phase: "incompatible" }, "critical", "Hub wire contract incompatible"],
    [{ ...disconnectedConnection(), phase: "identity-mismatch" }, "critical", "Hub identity trust mismatch"],
  ] as Array<[HubConnectionState, string, string]>) (
    "renders connection phase $state.phase without discarding last-good evidence",
    (connection, severity, label) => {
      connection.lastFrameAt = 1;
      const item = evidenceItems(input({ connection }))[0];
      expect(item).toMatchObject({ id: "connection", severity, label });
      expect(item?.detail).toContain("Last-good evidence is retained.");
    },
  );

  it("surfaces live protocol skew and bounds peer-controlled display text", () => {
    const items = evidenceItems(input({
      connection: {
        phase: "live",
        peerProtocolVersion: 3,
        effectiveProtocolVersion: 2,
        warning: "newer protocol active",
        lastFrameAt: 1,
      },
      progress: [{
        taskId: "T1",
        author: "operator\u061C\u202E\nname",
        kind: "approval",
        text: `approval subject=T1\u200B state=rejected :: ${"x".repeat(800)}`,
        postedAt: 1,
      }],
    }));
    expect(items[0]).toMatchObject({
      id: expect.stringMatching(/^approval:[0-9a-f]{20}$/),
      severity: "critical",
      label: "Ledger approval claim rejected: T1",
    });
    expect(items[0]?.description).toBe("self-attested by operator name");
    expect(items[0]?.detail.length).toBe(500);
    expect(items[1]).toMatchObject({ id: "connection", severity: "warning" });
  });

  it("marks missing authoritative roster fields unavailable after clearing prior values", () => {
    const items = evidenceItems(input({
      mailboxAvailable: false,
      livenessAvailable: false,
    }));
    expect(items).toEqual([{
      id: "availability:roster",
      category: "connection",
      severity: "warning",
      label: "Roster evidence partly unavailable",
      description: "hub compatibility",
      detail: "The authoritative roster did not provide mailbox counts or consume-liveness; prior values were cleared.",
    }]);
  });
});
