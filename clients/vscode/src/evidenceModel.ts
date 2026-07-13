// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — pure editor evidence model

/** Convert authoritative read-only snapshots into bounded IDE evidence items. */

import { createHash } from "node:crypto";
import { type HubConnectionState } from "./connectionState.js";
import {
  type HubAgentLiveness,
  type HubDeadLetter,
  type HubMailboxCount,
  type HubProgressNote,
  type HubRelayApproval,
} from "./hubEvidenceProtocol.js";

export type EvidenceCategory =
  | "approval"
  | "receipt"
  | "delivery"
  | "mailbox"
  | "wake"
  | "connection";
export type EvidenceSeverity = "critical" | "warning" | "info" | "ok";

/** One safe, display-ready coordination fact. */
export interface EvidenceItem {
  id: string;
  category: EvidenceCategory;
  severity: EvidenceSeverity;
  label: string;
  description: string;
  detail: string;
}

/** Complete input retained by the controller. */
export interface EvidenceInput {
  connection: HubConnectionState;
  progress: readonly HubProgressNote[];
  mailbox: readonly HubMailboxCount[];
  liveness: readonly HubAgentLiveness[];
  deadLetters: readonly HubDeadLetter[];
  relayApprovals: readonly HubRelayApproval[];
  mailboxAvailable: boolean;
  livenessAvailable: boolean;
}

interface ApprovalState {
  subject: string;
  state: "requested" | "approved" | "rejected";
  actor: string;
  reason: string;
  postedAt: number;
}

const APPROVAL = /^approval subject=(\S+) state=(requested|approved|rejected)(?: :: (.*))?$/;
const UNSAFE_DISPLAY = /[\u0000-\u001F\u007F-\u009F\u061C\u200B-\u200F\u202A-\u202E\u2060-\u206F\uFEFF]/g;
const SEVERITY_ORDER: Record<EvidenceSeverity, number> = {
  critical: 0,
  warning: 1,
  info: 2,
  ok: 3,
};

function clean(value: string, limit: number): string {
  const singleLine = value.replace(UNSAFE_DISPLAY, " ").replace(/\s+/g, " ").trim();
  return singleLine.length <= limit ? singleLine : `${singleLine.slice(0, limit - 1)}…`;
}

function stableId(prefix: string, ...values: string[]): string {
  const digest = createHash("sha256")
    .update(JSON.stringify(values))
    .digest("hex")
    .slice(0, 20);
  return `${prefix}:${digest}`;
}

function connectionEvidence(connection: HubConnectionState): EvidenceItem[] {
  if (connection.phase === "live" && connection.warning === undefined) {
    return [];
  }
  const retained = connection.lastFrameAt === undefined ? "" : " Last-good evidence is retained.";
  const labels: Record<Exclude<typeof connection.phase, "live">, string> = {
    disconnected: "Hub offline",
    negotiating: "Hub reconnecting",
    stale: "Hub evidence is stale",
    incompatible: "Hub wire contract incompatible",
    "identity-mismatch": "Hub identity trust mismatch",
  };
  const severity: EvidenceSeverity = connection.phase === "disconnected"
      || connection.phase === "incompatible" || connection.phase === "identity-mismatch"
    ? "critical"
    : "warning";
  const label = connection.phase === "live" ? "Hub compatibility warning" : labels[connection.phase];
  return [{
    id: "connection",
    category: "connection",
    severity,
    label,
    description: connection.phase,
    detail: clean(`${connection.warning ?? label}.${retained}`, 500),
  }];
}

function approvalEvidence(progress: readonly HubProgressNote[]): EvidenceItem[] {
  const latest = new Map<string, ApprovalState>();
  for (const note of progress) {
    if (note.kind !== "approval") {
      continue;
    }
    const parsed = APPROVAL.exec(note.text);
    if (parsed?.[1] === undefined || parsed[2] === undefined) {
      continue;
    }
    const current = latest.get(parsed[1]);
    if (current !== undefined && current.postedAt > note.postedAt) {
      continue;
    }
    latest.set(parsed[1], {
      subject: parsed[1],
      state: parsed[2] as ApprovalState["state"],
      actor: note.author,
      reason: parsed[3] ?? "",
      postedAt: note.postedAt,
    });
  }
  return [...latest.values()].map((approval) => ({
    id: stableId("approval", approval.subject),
    category: "approval" as const,
    severity: approval.state === "rejected"
      ? "critical" as const
      : approval.state === "requested" ? "warning" as const : "ok" as const,
    label: `Ledger approval claim ${approval.state}: ${clean(approval.subject, 100)}`,
    description: `self-attested by ${clean(approval.actor, 80)}`,
    detail: approval.reason
      ? clean(approval.reason, 500)
      : "Structured self-attested progress note; no authorisation is inferred.",
  }));
}

function receiptEvidence(progress: readonly HubProgressNote[]): EvidenceItem[] {
  const latest = new Map<string, HubProgressNote>();
  for (const note of progress) {
    if (note.kind === "assessment" && note.text.startsWith("release receipt:")) {
      const current = latest.get(note.taskId);
      if (current === undefined || current.postedAt <= note.postedAt) {
        latest.set(note.taskId, note);
      }
    }
  }
  return [...latest.values()].map((note) => ({
    id: stableId("receipt", note.taskId),
    category: "receipt" as const,
    severity: "info" as const,
    label: `Retained release-receipt claim: ${clean(note.taskId || "board-wide", 100)}`,
    description: `self-attested by ${clean(note.author, 80)}`,
    detail: clean(`${note.text} No release authority is inferred.`, 500),
  }));
}

function livenessByAgent(input: EvidenceInput): Map<string, HubAgentLiveness> {
  return new Map(input.liveness.map((entry) => [entry.agent, entry]));
}

function wakeEvidence(liveness: readonly HubAgentLiveness[]): EvidenceItem[] {
  return liveness.filter((entry) => !entry.provenLive).map((entry) => {
    const age = entry.lastReactionAge === null
      ? "reaction age unavailable"
      : `${Math.floor(entry.lastReactionAge)}s since reaction`;
    return {
      id: stableId("wake", entry.agent),
      category: "wake" as const,
      severity: "critical" as const,
      label: `Wake capability not proven: ${clean(entry.agent, 100)}`,
      description: entry.hasLiveWaiter ? "waiter present" : "no live waiter",
      detail: `Present on the roster but not proven wake-capable (${age}).`,
    };
  });
}

function mailboxEvidence(input: EvidenceInput): EvidenceItem[] {
  const liveness = livenessByAgent(input);
  return input.mailbox.filter((entry) => entry.count > 0).map((entry) => {
    const provenLive = liveness.get(entry.identity)?.provenLive;
    const status = provenLive === true
      ? "The target is currently proven wake-capable."
      : provenLive === false
        ? "The target is not currently proven wake-capable."
        : "Current wake capability is unavailable.";
    return {
      id: stableId("mailbox", entry.identity),
      category: "mailbox" as const,
      severity: provenLive === true ? "info" as const : "warning" as const,
      label: `Mailbox pending: ${clean(entry.identity, 100)}`,
      description: `${entry.count} pending`,
      detail: `${entry.count} directed message(s) remain pending. ${status}`,
    };
  });
}

function deliveryEvidence(input: EvidenceInput): EvidenceItem[] {
  const liveness = livenessByAgent(input);
  return input.deadLetters.filter((entry) => entry.count > 0).map((entry) => {
    const provenLive = liveness.get(entry.target)?.provenLive;
    const current = provenLive === true
      ? "The target is currently proven wake-capable."
      : provenLive === false
        ? "The target is not currently proven wake-capable."
        : "Current wake capability is unavailable.";
    return {
      id: stableId("delivery", entry.target),
      category: "delivery" as const,
      severity: provenLive === false ? "critical" as const : "warning" as const,
      label: `Undeliverable messages retained: ${clean(entry.target, 100)}`,
      description: `${entry.count} recorded`,
      detail: clean(
        `Last hub timestamp ${entry.lastTs} by ${entry.lastSender}. `
          + `This is retained historical delivery evidence. ${current}`,
        500,
      ),
    };
  });
}

function availabilityEvidence(input: EvidenceInput): EvidenceItem[] {
  if (input.connection.phase !== "live" && input.connection.phase !== "stale") {
    return [];
  }
  const missing = [
    input.mailboxAvailable ? "" : "mailbox counts",
    input.livenessAvailable ? "" : "consume-liveness",
  ].filter(Boolean);
  return missing.length === 0 ? [] : [{
    id: "availability:roster",
    category: "connection" as const,
    severity: "warning" as const,
    label: "Roster evidence partly unavailable",
    description: "hub compatibility",
    detail: `The authoritative roster did not provide ${missing.join(" or ")}; prior values were cleared.`,
  }];
}

function relayEvidence(approvals: readonly HubRelayApproval[]): EvidenceItem[] {
  return approvals.map((approval) => ({
    id: stableId("relay", approval.namespace, approval.action, approval.taskId),
    category: "approval" as const,
    severity: "warning" as const,
    label: `Relay approval pending: ${clean(approval.namespace, 60)}/${clean(approval.action, 60)}`,
    description: clean(approval.taskId, 100),
    detail: `Requested by ${clean(approval.requester, 100)}; operator quorum is incomplete.`,
  }));
}

/** Build a stable, severity-first evidence projection. */
export function evidenceItems(input: EvidenceInput): EvidenceItem[] {
  return [
    ...connectionEvidence(input.connection),
    ...availabilityEvidence(input),
    ...relayEvidence(input.relayApprovals),
    ...approvalEvidence(input.progress),
    ...receiptEvidence(input.progress),
    ...deliveryEvidence(input),
    ...mailboxEvidence(input),
    ...wakeEvidence(input.liveness),
  ].sort((a, b) =>
    SEVERITY_ORDER[a.severity] - SEVERITY_ORDER[b.severity]
      || a.category.localeCompare(b.category)
      || a.label.localeCompare(b.label),
  );
}
