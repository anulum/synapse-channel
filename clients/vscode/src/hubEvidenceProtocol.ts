// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — strict editor projection of coordination evidence

/** Decode read-only evidence fields without widening the core wire projection. */

import { isJsonRecord, nonEmptyString } from "./hubJson.js";

/** One retained blackboard progress note. */
export interface HubProgressNote {
  taskId: string;
  author: string;
  kind: string;
  text: string;
  postedAt: number;
}

/** Durable mailbox count for one logical identity. */
export interface HubMailboxCount {
  identity: string;
  count: number;
}

/** Consume-liveness proof for one present agent. */
export interface HubAgentLiveness {
  agent: string;
  provenLive: boolean;
  hasLiveWaiter: boolean;
  lastReactionAge: number | null;
}

/** Aggregate undeliverable-message evidence for one target. */
export interface HubDeadLetter {
  target: string;
  count: number;
  lastTs: number;
  lastSender: string;
}

/** Operator relay action still awaiting quorum. */
export interface HubRelayApproval {
  action: string;
  namespace: string;
  taskId: string;
  requester: string;
}

/** Optional evidence attached to an authoritative roster snapshot. */
export interface HubRosterEvidence {
  mailbox: HubMailboxCount[];
  liveness: HubAgentLiveness[];
  mailboxAvailable: boolean;
  livenessAvailable: boolean;
}

type OptionalProjection<T> = { ok: true; value: T } | { ok: false };

function finiteNonNegative(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) && value >= 0
    ? value
    : undefined;
}

function nonNegativeInteger(value: unknown): number | undefined {
  return Number.isInteger(value) && typeof value === "number" && value >= 0
    ? value
    : undefined;
}

function arrayProjection<T>(
  value: unknown,
  project: (item: unknown) => T | undefined,
): T[] | undefined {
  if (!Array.isArray(value)) {
    return undefined;
  }
  const items = value.map(project);
  return items.some((item) => item === undefined) ? undefined : items as T[];
}

function progressNote(value: unknown): HubProgressNote | undefined {
  if (!isJsonRecord(value)) {
    return undefined;
  }
  const taskId = typeof value["task_id"] === "string" ? value["task_id"] : undefined;
  const author = nonEmptyString(value["author"]);
  const kind = nonEmptyString(value["kind"]);
  const text = typeof value["text"] === "string" ? value["text"] : undefined;
  const postedAt = finiteNonNegative(value["posted_at"]);
  return taskId === undefined || author === undefined || kind === undefined
      || text === undefined || postedAt === undefined
    ? undefined
    : { taskId, author, kind, text, postedAt };
}

/** Project the retained progress stream from a board object. */
export function projectProgress(board: Record<string, unknown>): OptionalProjection<HubProgressNote[]> {
  if (!("progress" in board)) {
    return { ok: true, value: [] };
  }
  const value = arrayProjection(board["progress"], progressNote);
  return value === undefined ? { ok: false } : { ok: true, value };
}

function mailboxCounts(value: unknown): HubMailboxCount[] | undefined {
  if (value === null) {
    return [];
  }
  if (!isJsonRecord(value)) {
    return undefined;
  }
  const counts: HubMailboxCount[] = [];
  for (const [identity, rawCount] of Object.entries(value)) {
    const cleanIdentity = nonEmptyString(identity);
    const count = nonNegativeInteger(rawCount);
    if (cleanIdentity === undefined || count === undefined) {
      return undefined;
    }
    counts.push({ identity: cleanIdentity, count });
  }
  return counts.sort((a, b) => a.identity.localeCompare(b.identity));
}

function livenessEntries(value: unknown): HubAgentLiveness[] | undefined {
  if (!isJsonRecord(value)) {
    return undefined;
  }
  const entries: HubAgentLiveness[] = [];
  for (const [agent, raw] of Object.entries(value)) {
    if (!isJsonRecord(raw)) {
      return undefined;
    }
    const cleanAgent = nonEmptyString(agent);
    const provenLive = raw["proven_live"];
    const hasLiveWaiter = raw["has_live_waiter"];
    const rawAge = raw["last_reaction_age"];
    const lastReactionAge = rawAge === null ? null : finiteNonNegative(rawAge);
    if (cleanAgent === undefined || typeof provenLive !== "boolean"
        || typeof hasLiveWaiter !== "boolean" || lastReactionAge === undefined
        || (hasLiveWaiter && !provenLive)) {
      return undefined;
    }
    entries.push({ agent: cleanAgent, provenLive, hasLiveWaiter, lastReactionAge });
  }
  return entries.sort((a, b) => a.agent.localeCompare(b.agent));
}

/** Project optional mailbox and liveness fields; null means the frame omitted both. */
export function projectRosterEvidence(
  envelope: Record<string, unknown>,
  roster: readonly string[],
): OptionalProjection<HubRosterEvidence | null> {
  const hasMailbox = "mailbox_pending" in envelope;
  const hasLiveness = "agent_liveness" in envelope;
  if (!hasMailbox && !hasLiveness) {
    return { ok: true, value: null };
  }
  const mailbox = hasMailbox ? mailboxCounts(envelope["mailbox_pending"]) : [];
  const liveness = hasLiveness ? livenessEntries(envelope["agent_liveness"]) : [];
  const rosterAgents = new Set(roster);
  return mailbox === undefined || liveness === undefined
      || liveness.some((entry) => !rosterAgents.has(entry.agent))
    ? { ok: false }
    : {
        ok: true,
        value: {
          mailbox,
          liveness,
          mailboxAvailable: hasMailbox && envelope["mailbox_pending"] !== null,
          livenessAvailable: hasLiveness,
        },
      };
}

function deadLetter(value: unknown): HubDeadLetter | undefined {
  if (!isJsonRecord(value)) {
    return undefined;
  }
  const target = nonEmptyString(value["target"]);
  const count = nonNegativeInteger(value["count"]);
  const lastTs = finiteNonNegative(value["last_ts"]);
  const lastSender = nonEmptyString(value["last_sender"]);
  return target === undefined || count === undefined || lastTs === undefined
      || lastSender === undefined
    ? undefined
    : { target, count, lastTs, lastSender };
}

function relayApproval(value: unknown): HubRelayApproval | undefined {
  if (!isJsonRecord(value)) {
    return undefined;
  }
  const action = nonEmptyString(value["action"]);
  const namespace = nonEmptyString(value["namespace"]);
  const taskId = nonEmptyString(value["task_id"]);
  const requester = nonEmptyString(value["requester"]);
  return action === undefined || namespace === undefined || taskId === undefined
      || requester === undefined
    ? undefined
    : { action, namespace, taskId, requester };
}

/** Project the evidence fields attached to an authoritative state snapshot. */
export function projectStateEvidence(snapshot: Record<string, unknown>): OptionalProjection<{
  deadLetters: HubDeadLetter[];
  relayApprovals: HubRelayApproval[];
}> {
  const deadLetters = "dead_letters" in snapshot
    ? arrayProjection(snapshot["dead_letters"], deadLetter)
    : [];
  const relayApprovals = "pending_relay_approvals" in snapshot
    ? arrayProjection(snapshot["pending_relay_approvals"], relayApproval)
    : [];
  return deadLetters === undefined || relayApprovals === undefined
    ? { ok: false }
    : { ok: true, value: { deadLetters, relayApprovals } };
}
