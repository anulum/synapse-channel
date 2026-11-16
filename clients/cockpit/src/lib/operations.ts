// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — the declared operations and one chain, read from /operations.json

// `/operations.json` serves what the hub journalled about governed operations:
// the declared operations newest first, or one operation's whole chain. The
// state words and the hub's answer names are the report's own and are shown
// as the hub gives them, in every language. A state this cockpit does not know
// is shown verbatim and marked, never mapped onto a known one, and a field the
// row does not carry is "not recorded", never zero or empty success.
//
// This module is the model the tab draws, and only the tab's chunk loads it. The
// feed's documents and reads live in `operationsFeed`, which the always-loaded
// inspector owns and hands to the tab as an `OperationsSource`. A module both
// chunks load, or a path from the tab's chunk to the authenticated fetch, makes
// the bundler split shared code out of the entry chunk.

import type { EndpointFeed, FeedState } from "./feed";
import type { VerifyResult } from "./merkleVerify";
import type { CockpitScope } from "./scope";

/** Every answer the hub gives an attempt request, in the order it tries them. */
export const ANSWER_NAMES = [
  "not_claim_holder",
  "stale_epoch",
  "unknown",
  "not_approved",
  "approval_stale",
  "approval_expired",
  "approver_unauthorised",
  "operation_closed",
  "attempt_exists",
  "earlier_attempt_unreconciled",
  "approved_fresh",
] as const;
export type AnswerName = (typeof ANSWER_NAMES)[number];

/** The answer that opens an attempt; every other answer is a refusal. */
export const APPROVED_FRESH: AnswerName = "approved_fresh";

/** The report's states other than a refusal, most consequential first. */
export const OPERATION_STATES = [
  "verified",
  "sealed",
  "ended, unreconciled",
  "outcome reported, unknown",
  "attempt open, silent",
  "attempt open",
  "reconciled",
  "awaiting approval",
  "approved, no attempt",
  "approval rejected",
  "declared",
  "not recorded",
] as const;
export type OperationStateWord = (typeof OPERATION_STATES)[number];

const REFUSED_PREFIX = "refused: ";

/** How a state is drawn; it names the kind of state, never a judgement the rows do not make. */
export type StateTone =
  | "settled"
  | "sealed"
  | "attention"
  | "unknown"
  | "open"
  | "refused"
  | "waiting"
  | "neutral"
  | "absent"
  | "unrecognised";

/** A state as the hub gave it, with how to draw it and what it says. */
export interface StateReading {
  /** The hub's words, unchanged. */
  readonly text: string;
  readonly tone: StateTone;
  /** Whether the words are a state or an answer this cockpit knows. */
  readonly known: boolean;
  /** The refusing answer's name for a `refused: <answer>` state, else null. */
  readonly answer: string | null;
  /** Where the state sits in the display order; unknown states come last. */
  readonly rank: number;
}

const TONES: Readonly<Record<OperationStateWord, StateTone>> = {
  verified: "settled",
  sealed: "sealed",
  "ended, unreconciled": "attention",
  "outcome reported, unknown": "unknown",
  "attempt open, silent": "attention",
  "attempt open": "open",
  reconciled: "settled",
  "awaiting approval": "waiting",
  "approved, no attempt": "neutral",
  "approval rejected": "refused",
  declared: "neutral",
  "not recorded": "absent",
};

/** The rank of every refusal: after the attempt states, before the words without one. */
const REFUSED_RANK = OPERATION_STATES.indexOf("reconciled") + 0.5;

function isStateWord(text: string): text is OperationStateWord {
  return (OPERATION_STATES as readonly string[]).includes(text);
}

/** Whether `name` is an answer the hub refuses with. */
export function isRefusingAnswer(name: string): boolean {
  return name !== APPROVED_FRESH && (ANSWER_NAMES as readonly string[]).includes(name);
}

/** Read one state as the hub gave it: its tone, whether it is known, and its refusing answer. */
export function readState(text: string): StateReading {
  if (isStateWord(text)) {
    return { text, tone: TONES[text], known: true, answer: null, rank: OPERATION_STATES.indexOf(text) };
  }
  if (text.startsWith(REFUSED_PREFIX)) {
    const answer = text.slice(REFUSED_PREFIX.length);
    return { text, tone: "refused", known: isRefusingAnswer(answer), answer, rank: REFUSED_RANK };
  }
  return { text, tone: "unrecognised", known: false, answer: null, rank: OPERATION_STATES.length };
}

/** The part of the architecture's chain a link belongs to: intent, approval, answer, outcome. */
export type ChainPhase = "A" | "B" | "C" | "D" | "?";

/** The link kinds the report gives, and their place in the chain. */
export const LINK_PHASES: Readonly<Record<string, ChainPhase>> = {
  declared: "A",
  approval: "B",
  answer: "C",
  seal: "C",
  outcome: "D",
  verification: "D",
};

/** Return the chain part of a link kind; a kind the report does not give is `?`. */
export function phaseOf(kind: string): ChainPhase {
  return Object.hasOwn(LINK_PHASES, kind) ? (LINK_PHASES[kind] as ChainPhase) : "?";
}

/** One row of the list: an operation and where its chain stands. */
export interface OperationRow {
  readonly operationId: string;
  readonly taskId: string;
  readonly declarer: string;
  readonly declaredSeq: number;
  readonly state: string;
  readonly latestSeq: number | null;
  readonly latestKind: string;
  /** Epoch seconds of the latest row, or null when not recorded. */
  readonly latestAt: number | null;
}

/** What the feed says about the scope it was asked for. */
export interface ScopeNote {
  readonly scope: string;
  readonly applied: boolean;
  readonly reason: string;
}

/** One page of the declared operations. */
export interface OperationsPage {
  readonly operations: readonly OperationRow[];
  /** Rows the page held that were not readable and are not shown. */
  readonly unreadable: number;
  readonly total: number | null;
  readonly nextBefore: number | null;
  readonly asOfSeq: number | null;
  readonly custody: string;
  readonly scope: ScopeNote | null;
}

/** One attempt of a chain. */
export interface AttemptRow {
  readonly attemptId: string;
  readonly executor: string;
  readonly state: string;
}

/** One journalled row of a chain, with its fields as text. */
export interface ChainLink {
  readonly seq: number;
  readonly at: number | null;
  readonly kind: string;
  readonly actor: string;
  readonly detail: readonly (readonly [string, string])[];
}

/** One field of the kept intent, with its value as the canonical JSON text the digest covers. */
export interface IntentField {
  readonly field: string;
  readonly canonical: string;
}

/** One operation's chain as the report gives it. */
export interface OperationChain {
  readonly present: boolean;
  readonly operationId: string;
  readonly taskId: string;
  readonly state: string;
  readonly intentSha256: string;
  readonly digestCheck: string;
  /** The kept intent's digest as the dashboard recomputed it; empty without an intent. */
  readonly recomputedSha256: string;
  /** The kept intent, field by field in canonical order, as the dashboard encoded it. */
  readonly intentFields: readonly IntentField[];
  readonly attempts: readonly AttemptRow[];
  readonly chain: readonly ChainLink[];
  /** Links the chain held that were not readable and are not shown. */
  readonly unreadable: number;
  readonly asOfSeq: number | null;
  readonly custody: string;
  readonly scope: ScopeNote | null;
}

/** One group of the list: the rows that share a state, in the report's order. */
export interface StateGroup {
  readonly state: StateReading;
  readonly rows: readonly OperationRow[];
}

/** Group a page's rows by state, most consequential first; rows keep their order inside a group. */
export function groupByState(rows: readonly OperationRow[]): readonly StateGroup[] {
  const groups = new Map<string, OperationRow[]>();
  for (const row of rows) groups.set(row.state, [...(groups.get(row.state) ?? []), row]);
  return [...groups.entries()]
    .map(([state, grouped]) => ({ state: readState(state), rows: grouped }))
    .sort((a, b) => a.state.rank - b.state.rank || a.state.text.localeCompare(b.state.text));
}

/** The list feed's state before its first read. */
export const OPERATIONS_CONNECTING: FeedState<OperationsPage> = {
  data: null,
  status: "connecting",
  fetchedAt: null,
  error: null,
};

/** The outcome of reading one chain. `absent` = the dashboard serves no operations feed. */
export type ChainResult =
  | { readonly kind: "loaded"; readonly chain: OperationChain }
  | { readonly kind: "absent" }
  | { readonly kind: "error"; readonly message: string };

/** The reads the operations tab makes, handed to it by the always-loaded inspector. */
export interface OperationsSource {
  /** Poll one page of the list, before an optional cursor, for the scope. */
  readonly openList: (scope: CockpitScope, before: number | null) => EndpointFeed<OperationsPage>;
  /** Read one operation's chain once. */
  readonly readChain: (operationId: string, scope: CockpitScope) => Promise<ChainResult>;
  /** Fetch one row's inclusion proof and check it in the browser. */
  readonly verifyRow: (seq: number) => Promise<VerifyResult>;
  /** Ask the dashboard to record one approval decision under the login's relay identity. */
  readonly decide: (input: ApprovalDecideInput) => Promise<ApprovalDecideResult>;
}

/** The state that an approval decision is asked for. */
export const AWAITING_APPROVAL: OperationStateWord = "awaiting approval";

/**
 * What the approval view may offer for one chain.
 *
 * `decidable` only when the operation awaits approval and the kept intent's
 * recomputed digest equals the recorded one; `differs` when they are not
 * equal, which is never decidable; `no intent` when nothing was kept to
 * decide on. Any other state has no approval view.
 */
export type ApprovalView =
  | { readonly kind: "decidable"; readonly digest: string; readonly reason: string | null }
  | { readonly kind: "differs"; readonly recorded: string; readonly recomputed: string }
  | { readonly kind: "no intent" };

/** The requester's own words: the reason of the latest `requested` approval note, or null without one. */
export function requestReason(chain: OperationChain): string | null {
  const requests = chain.chain.filter(
    (link) => link.kind === "approval" && link.detail.some(([key, value]) => key === "state" && value === "requested"),
  );
  const latest = requests.at(-1);
  if (latest === undefined) return null;
  const reason = latest.detail.find(([key]) => key === "reason")?.[1];
  // Empty text arrives as its JSON, `""`; absent and empty both mean no words were given.
  return reason === undefined || reason === '""' ? "" : reason;
}

/** Return the approval view of a chain, or null when the operation does not await approval. */
export function approvalView(chain: OperationChain): ApprovalView | null {
  if (chain.state !== AWAITING_APPROVAL) return null;
  if (chain.intentSha256 === "" || chain.recomputedSha256 === "" || chain.intentFields.length === 0) {
    return { kind: "no intent" };
  }
  if (chain.digestCheck !== "matches" || chain.recomputedSha256 !== chain.intentSha256) {
    return { kind: "differs", recorded: chain.intentSha256, recomputed: chain.recomputedSha256 };
  }
  return { kind: "decidable", digest: chain.intentSha256, reason: requestReason(chain) };
}

const DIGEST = /^[0-9a-f]{64}$/u;

/**
 * The two commands that decide a digest from a terminal, approve then reject.
 *
 * They carry the digest and a quoted placeholder for the approver, and never a
 * token: the hub's own command reads its credential where it always does.
 * A digest that is not 64 lowercase hex characters gives no command.
 */
export function decideCommands(digest: string): readonly string[] {
  if (!DIGEST.test(digest)) return [];
  const base = `synapse approvals decide --name "<approver>" --subject sha256:${digest}`;
  return [`${base} --approve`, `${base} --reject --reason "<why>"`];
}

/** The two decisions the cockpit can record. */
export type ApprovalDecision = "approved" | "rejected";

/** The largest reason the dashboard relays, in UTF-8 bytes; it refuses a longer one rather than trimming it. */
export const MAX_APPROVAL_REASON_BYTES = 4096;

/** One decision as the person gives it. The relay identity is never part of it: the dashboard takes it from the login. */
export interface ApprovalDecideInput {
  readonly operationId: string;
  readonly intentSha256: string;
  readonly decision: ApprovalDecision;
  readonly reason: string;
}

/**
 * The dashboard's answer to one decision.
 *
 * `recorded`: the hub journalled the note, and judges it only when an attempt
 * is asked. `refused`: the dashboard refused before relaying anything (the
 * digests, the declarer, the project, a live agent, the store or the roster).
 * `hub-refused`: the hub refused the note itself (its verdict gate or its
 * access rules). `invalid`: the dashboard refused the request's fields.
 */
export type ApprovalDecideResult =
  | { readonly kind: "recorded" | "refused" | "hub-refused" | "unreachable" | "invalid" | "rate-limited" | "error"; readonly detail: string }
  | { readonly kind: "not-armed" | "unauthorised" };

/** The reason's size as the dashboard counts it: UTF-8 bytes. */
export function reasonBytes(reason: string): number {
  return new TextEncoder().encode(reason).length;
}

/** Render epoch seconds as an exact UTC instant, or null when the row holds none. */
export function instantText(at: number | null): string | null {
  return at === null ? null : new Date(at * 1000).toISOString();
}
