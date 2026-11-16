// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — the operations feed on the wire: its URLs, its documents and the tab's reads

// The always-loaded inspector owns these reads and hands them to the tab, as
// the shell owns the other feeds and the panels render what they are given.
// The tab's chunk so reaches no network code of its own, and the entry chunk
// keeps the authenticated fetch. A row that is not readable is counted and left
// out, and a field the row does not carry is empty or null, never a guess.

import { authenticatedFetch } from "./auth";
import { createEndpointFeed, type EndpointFeed } from "./feed";
import { fetchAndVerify } from "./merkleVerify";
import { parseOperatorOutcome } from "./operatorActionValidation";
import type {
  ApprovalDecideInput,
  ApprovalDecideResult,
  AttemptRow,
  ChainLink,
  ChainResult,
  IntentField,
  OperationChain,
  OperationRow,
  OperationsPage,
  OperationsSource,
  ScopeNote,
} from "./operations";
import { appendQuery, withScope, type CockpitScope } from "./scope";

/** The feed's path on the dashboard. */
export const OPERATIONS_URL = "/operations.json";

/** How often the list is read again while the tab is open. */
export const OPERATIONS_POLL_MS = 10_000;

/** How often the attention queue reads the operations again; each read folds the whole store. */
export const OPERATIONS_ATTENTION_POLL_MS = 30_000;

/** The newest declared operations the attention queue looks at: the feed's largest page. */
export const OPERATIONS_ATTENTION_LIMIT = 200;

type Fields = Record<string, unknown>;

function record(value: unknown): Fields | null {
  return typeof value === "object" && value !== null && !Array.isArray(value) ? (value as Fields) : null;
}

function text(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function sequence(value: unknown): number | null {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0 ? value : null;
}

function instant(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/** Render one detail value as the row holds it: text as text, empty text and anything else as JSON. */
export function detailText(value: unknown): string {
  return typeof value === "string" && value !== "" ? value : JSON.stringify(value) ?? "null";
}

function scopeNote(value: unknown): ScopeNote | null {
  const fields = record(value);
  if (fields === null) return null;
  return { scope: text(fields["scope"]), applied: fields["applied"] === true, reason: text(fields["reason"]) };
}

function operationRow(value: unknown): OperationRow | null {
  const fields = record(value);
  const declaredSeq = sequence(fields?.["declared_seq"]);
  if (fields === null || declaredSeq === null || text(fields["operation_id"]) === "") return null;
  return {
    operationId: text(fields["operation_id"]),
    taskId: text(fields["task_id"]),
    declarer: text(fields["declarer"]),
    declaredSeq,
    state: text(fields["state"]),
    latestSeq: sequence(fields["latest_seq"]),
    latestKind: text(fields["latest_kind"]),
    latestAt: instant(fields["latest_at"]),
  };
}

function readable<T>(values: unknown, read: (value: unknown) => T | null): { rows: T[]; unreadable: number } {
  const entries = Array.isArray(values) ? values : [];
  const rows = entries.map(read).filter((row): row is T => row !== null);
  return { rows, unreadable: entries.length - rows.length };
}

/** Shape one page of `/operations.json`; null when the payload is not a list document. */
export function parseOperationsPage(raw: unknown): OperationsPage | null {
  const fields = record(raw);
  if (fields === null || !Array.isArray(fields["operations"])) return null;
  const { rows, unreadable } = readable(fields["operations"], operationRow);
  return {
    operations: rows,
    unreadable,
    total: sequence(fields["total"]),
    nextBefore: sequence(fields["next_before"]),
    asOfSeq: sequence(fields["as_of_seq"]),
    custody: text(fields["custody"]),
    scope: scopeNote(fields["scope"]),
  };
}

function attemptRow(value: unknown): AttemptRow | null {
  const fields = record(value);
  if (fields === null || text(fields["attempt_id"]) === "") return null;
  return { attemptId: text(fields["attempt_id"]), executor: text(fields["executor"]), state: text(fields["state"]) };
}

function intentField(value: unknown): IntentField | null {
  const fields = record(value);
  if (fields === null || text(fields["field"]) === "" || typeof fields["canonical"] !== "string") return null;
  return { field: text(fields["field"]), canonical: text(fields["canonical"]) };
}

function chainLink(value: unknown): ChainLink | null {
  const fields = record(value);
  const seq = sequence(fields?.["seq"]);
  if (fields === null || seq === null) return null;
  const detail = Object.entries(record(fields["detail"]) ?? {}).map(
    ([key, item]) => [key, detailText(item)] as const,
  );
  return { seq, at: instant(fields["at"]), kind: text(fields["kind"]), actor: text(fields["actor"]), detail };
}

/** Shape one chain of `/operations.json?operation=ID`; null when the payload is not a chain. */
export function parseOperationChain(raw: unknown): OperationChain | null {
  const fields = record(raw);
  if (fields === null || typeof fields["present"] !== "boolean" || !Array.isArray(fields["chain"])) return null;
  const links = readable(fields["chain"], chainLink);
  return {
    present: fields["present"],
    operationId: text(fields["operation_id"]),
    taskId: text(fields["task_id"]),
    state: text(fields["state"]),
    intentSha256: text(fields["intent_sha256"]),
    digestCheck: text(fields["digest_check"]),
    recomputedSha256: text(fields["recomputed_sha256"]),
    intentFields: readable(fields["intent_fields"], intentField).rows,
    attempts: readable(fields["attempts"], attemptRow).rows,
    chain: links.rows,
    unreadable: links.unreadable,
    asOfSeq: sequence(fields["as_of_seq"]),
    custody: text(fields["custody"]),
    scope: scopeNote(fields["scope"]),
  };
}

/** The list's URL: one page, before an optional cursor, asking for the scope. */
export function operationsUrl(scope: CockpitScope, before: number | null = null): string {
  const page = before === null ? OPERATIONS_URL : appendQuery(OPERATIONS_URL, `before=${before}`);
  return withScope(page, scope);
}

/** One chain's URL, asking for the scope so the feed can say it shows the chain whole. */
export function operationChainUrl(operationId: string, scope: CockpitScope): string {
  return withScope(appendQuery(OPERATIONS_URL, `operation=${encodeURIComponent(operationId)}`), scope);
}

/** Poll one page of the list. */
export function createOperationsFeed(
  scope: CockpitScope,
  before: number | null = null,
  fetcher?: typeof fetch,
): EndpointFeed<OperationsPage> {
  return createEndpointFeed({
    url: operationsUrl(scope, before),
    pollMs: OPERATIONS_POLL_MS,
    parse: parseOperationsPage,
    ...(fetcher === undefined ? {} : { fetcher }),
  });
}

/**
 * Poll the newest page of declared operations for the attention queue, which
 * turns their refusals and silent attempts into items. An older operation
 * beyond the page is not looked at; the operations tab pages back to it.
 */
export function createOperationsAttentionFeed(
  scope: CockpitScope,
  fetcher?: typeof fetch,
): EndpointFeed<OperationsPage> {
  return createEndpointFeed({
    url: withScope(appendQuery(OPERATIONS_URL, `limit=${OPERATIONS_ATTENTION_LIMIT}`), scope),
    pollMs: OPERATIONS_ATTENTION_POLL_MS,
    parse: parseOperationsPage,
    ...(fetcher === undefined ? {} : { fetcher }),
  });
}

/** Read one operation's chain once; the caller decides when to read it again. */
export async function fetchOperationChain(
  operationId: string,
  scope: CockpitScope,
  fetcher: typeof fetch = authenticatedFetch,
): Promise<ChainResult> {
  try {
    const response = await fetcher(operationChainUrl(operationId, scope));
    if (response.status === 404) return { kind: "absent" };
    if (!response.ok) return { kind: "error", message: `hub returned ${response.status}` };
    const chain = parseOperationChain(await response.json());
    if (chain === null) return { kind: "error", message: "the chain payload was not a chain document" };
    return { kind: "loaded", chain };
  } catch (cause) {
    return { kind: "error", message: cause instanceof Error ? cause.message : String(cause) };
  }
}

/** The route that records one approval decision. */
export const APPROVAL_DECIDE_URL = "/approval/decide";

const DECIDE_ACTION = "approval_decide";

/** Longest dashboard text shown as it came; a longer or markup-looking body gives the fallback. */
const MAX_LINE = 600;

function line(raw: string, fallback: string): string {
  const trimmed = raw.trim();
  return trimmed === "" || trimmed.startsWith("<") || trimmed.length > MAX_LINE ? fallback : trimmed;
}

function json(raw: string): unknown {
  try {
    return JSON.parse(raw) as unknown;
  } catch {
    return null;
  }
}

/** Read the route's plain-text answers: the dashboard's own gates before the route's document. */
function plainOutcome(status: number, raw: string): ApprovalDecideResult {
  if (status === 401) return { kind: "unauthorised" };
  if (status === 404) return { kind: "not-armed" };
  if (status === 403) return { kind: "refused", detail: line(raw, "dashboard capability denied") };
  if (status === 429) return { kind: "rate-limited", detail: line(raw, "operator rate limit exceeded") };
  if (status === 400) return { kind: "invalid", detail: line(raw, "the dashboard refused the request") };
  if (status === 503) return { kind: "unreachable", detail: line(raw, "the dashboard could not reach the hub") };
  return { kind: "error", detail: `dashboard returned ${status} without a decision outcome` };
}

/**
 * Map one answer of `POST /approval/decide`. A `refused` document is the
 * dashboard's own refusal before any relay; `denied` and `rejected` are the
 * hub's; only `recorded` with 200 is a recorded decision.
 */
export function decideOutcome(status: number, raw: string): ApprovalDecideResult {
  const document = parseOperatorOutcome(json(raw), DECIDE_ACTION);
  if (document === null) return plainOutcome(status, raw);
  const detail = line(document.detail, `dashboard returned ${status}`);
  if (document.status === "refused") return { kind: "refused", detail };
  if (document.status === "denied" || document.status === "rejected") return { kind: "hub-refused", detail };
  if (document.status === "unreachable") return { kind: "unreachable", detail };
  if (document.status === "recorded" && document.ok && status === 200) return { kind: "recorded", detail };
  return { kind: "error", detail: `dashboard answered '${document.status}' with ${status}` };
}

/** Ask the dashboard to record one decision; the body names no identity. */
export async function decideApproval(
  input: ApprovalDecideInput,
  fetcher: typeof fetch = authenticatedFetch,
): Promise<ApprovalDecideResult> {
  const body = {
    operation_id: input.operationId,
    intent_sha256: input.intentSha256,
    decision: input.decision,
    reason: input.reason,
  };
  try {
    const response = await fetcher(APPROVAL_DECIDE_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return decideOutcome(response.status, await response.text());
  } catch (cause) {
    return { kind: "error", detail: cause instanceof Error ? cause.message : String(cause) };
  }
}

/** The reads the inspector hands the operations tab, and the one write it may ask for. */
export const OPERATIONS_SOURCE: OperationsSource = {
  openList: (scope, before) => createOperationsFeed(scope, before),
  readChain: (operationId, scope) => fetchOperationChain(operationId, scope),
  verifyRow: (seq) => fetchAndVerify(seq),
  decide: (input) => decideApproval(input),
};
