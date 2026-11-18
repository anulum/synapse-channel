// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — the setup writes and receipt reads on the wire, and what the cockpit may say of them

// The dashboard plans a setup change with the command line's own planner and
// applies it once, through the command line's executor, after a person
// confirms the plan. Every answer is a receipt in the dashboard's setup chain.
// The cockpit says a change was applied only when it reads that receipt back
// from the chain, the receipt is `applied` and every effect's `after` is
// `satisfied`; it never takes the apply's own answer for it. A plan is read
// whole or not at all, because a person confirms all of it.

import { authenticatedFetch } from "./auth";

/** The served profile's read-only preflight; it names the profile the dashboard plans. */
export const SETUP_PREFLIGHT_URL = "/setup/preflight.json";

/** Issue one plan; a POST, because the dashboard stores a one-use record. */
export const SETUP_PLAN_URL = "/setup/plan";

/** Apply one issued plan once. */
export const SETUP_APPLY_URL = "/setup/apply";

/** The setup receipt chain: the newest receipts, or one by `?sequence=N`. */
export const SETUP_RECEIPTS_URL = "/setup/receipts.json";

/** One planned change, as a person confirms it: what it does, never where. */
export interface SetupPlanEffect {
  readonly id: string;
  readonly disposition: string;
  readonly disruption: string;
  readonly reversible: boolean;
  /** The running hub's process id when the change restarts it, else null. */
  readonly processId: number | null;
}

/** One issued plan and the one-use confirmation that applies it. */
export interface IssuedSetupPlan {
  readonly planId: string;
  readonly planDigest: string;
  readonly confirmationNonce: string;
  readonly requestId: string;
  readonly profile: string;
  /** Seconds since the epoch. */
  readonly expiresAt: number;
  readonly effects: readonly SetupPlanEffect[];
}

/** One effect of a receipt: the unit it concerns, and its verdict before and after. */
export interface SetupEffectReceipt {
  readonly kind: string;
  readonly target: string;
  readonly before: string;
  readonly after: string;
}

/** One receipt of the setup chain, as the dashboard lets a browser read it. */
export interface SetupReceipt {
  readonly sequence: number;
  readonly requestId: string;
  readonly planId: string | null;
  readonly capability: string;
  readonly outcome: string;
  readonly reason: string;
  readonly effects: readonly SetupEffectReceipt[];
  readonly receiptDigest: string;
}

/** A setup write's answer: its document, the dashboard's refusal in its own words, or no answer. */
export type SetupAnswer<T> =
  | { readonly kind: "ok"; readonly value: T }
  | { readonly kind: "refused"; readonly status: number; readonly detail: string }
  | { readonly kind: "failed"; readonly detail: string };

/** What the receipt read back from the chain confirms. */
export type SetupReadBack = "applied" | "unchanged" | "refused" | "unknown";

/** The reads and writes the apply step asks for. */
export interface SetupApplySource {
  readonly plan: () => Promise<SetupAnswer<IssuedSetupPlan>>;
  readonly apply: (plan: IssuedSetupPlan) => Promise<SetupAnswer<SetupReceipt>>;
  /** Read the apply's receipt back from the chain; null when the chain does not show it. */
  readonly readBack: (plan: IssuedSetupPlan, answered: SetupReceipt | null) => Promise<SetupReceipt | null>;
}

type Fields = Record<string, unknown>;

const OPAQUE_ID = /^[A-Za-z0-9_-]{22,128}$/u;
const SHA256 = /^[0-9a-f]{64}$/u;
const MAX_DETAIL = 300;
const REFUSAL_OUTCOMES: ReadonlySet<string> = new Set(["refused", "denied", "rate_limited", "expired", "drifted"]);
const JSON_HEADERS = { "Content-Type": "application/json" } as const;

function record(value: unknown): Fields | null {
  return typeof value === "object" && value !== null && !Array.isArray(value) ? (value as Fields) : null;
}

function text(value: unknown): string | null {
  return typeof value === "string" && value !== "" ? value : null;
}

function count(value: unknown, least: number): number | null {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= least ? value : null;
}

/** Read every entry with `read`, or none: one unreadable entry refuses the list. */
function every<T>(values: unknown, read: (value: unknown) => T | null): T[] | null {
  if (!Array.isArray(values)) return null;
  const rows = values.map(read);
  return rows.every((row) => row !== null) ? (rows as T[]) : null;
}

function planEffect(value: unknown): SetupPlanEffect | null {
  const fields = record(value);
  if (fields === null) return null;
  const id = text(fields["id"]);
  const disposition = text(fields["disposition"]);
  const disruption = text(fields["disruption"]);
  const reversible = fields["reversible"];
  const processId = fields["process_id"] === null ? null : count(fields["process_id"], 2);
  if (id === null || disposition === null || disruption === null || typeof reversible !== "boolean") return null;
  if (processId === null && fields["process_id"] !== null) return null;
  return { id, disposition, disruption, reversible, processId };
}

/** Shape one plan answer of `POST /setup/plan`; null unless every field and effect is readable. */
export function parseIssuedPlan(raw: unknown): IssuedSetupPlan | null {
  const fields = record(raw);
  if (fields === null || fields["version"] !== 1 || fields["mutates_local_state"] !== true) return null;
  const planId = text(fields["plan_id"]);
  const planDigest = text(fields["plan_digest"]);
  const confirmationNonce = text(fields["confirmation_nonce"]);
  const requestId = text(fields["request_id"]);
  const profile = text(fields["profile"]);
  const expiresAt = count(fields["expires_at"], 1);
  const effects = every(fields["effects"], planEffect);
  if (planId === null || !OPAQUE_ID.test(planId) || planDigest === null || !SHA256.test(planDigest)) return null;
  if (confirmationNonce === null || !OPAQUE_ID.test(confirmationNonce)) return null;
  if (requestId === null || profile === null || expiresAt === null || effects === null) return null;
  return { planId, planDigest, confirmationNonce, requestId, profile, expiresAt, effects };
}

function effectReceipt(value: unknown): SetupEffectReceipt | null {
  const fields = record(value);
  const kind = text(fields?.["kind"]);
  const target = text(fields?.["target"]);
  const before = text(fields?.["before"]);
  const after = text(fields?.["after"]);
  if (kind === null || target === null || before === null || after === null) return null;
  return { kind, target, before, after };
}

/** Shape one receipt's browser projection; null unless every field and effect is readable. */
export function parseSetupReceipt(raw: unknown): SetupReceipt | null {
  const fields = record(raw);
  if (fields === null) return null;
  const sequence = count(fields["sequence"], 1);
  const requestId = text(fields["request_id"]);
  const planId = fields["plan_id"] === null ? null : text(fields["plan_id"]);
  const capability = text(fields["capability"]);
  const outcome = text(fields["outcome"]);
  const reason = text(fields["reason"]);
  const receiptDigest = text(fields["receipt_digest"]);
  const effects = every(fields["effects"], effectReceipt);
  if (sequence === null || requestId === null || capability === null || outcome === null || reason === null) {
    return null;
  }
  if (planId === null && fields["plan_id"] !== null) return null;
  if (receiptDigest === null || !SHA256.test(receiptDigest) || effects === null) return null;
  return { sequence, requestId, planId, capability, outcome, reason, effects, receiptDigest };
}

/**
 * Say what a read-back receipt confirms: `applied` only for an `applied`
 * receipt whose every effect is `satisfied` after; `unchanged` when the host
 * already held the profile; `refused` for a refusal; otherwise `unknown`.
 */
export function readBackVerdict(receipt: SetupReceipt | null): SetupReadBack {
  if (receipt === null) return "unknown";
  if (receipt.outcome === "applied") {
    const confirmed = receipt.effects.length > 0 && receipt.effects.every((effect) => effect.after === "satisfied");
    return confirmed ? "applied" : "unknown";
  }
  if (receipt.outcome === "already_satisfied") return "unchanged";
  return REFUSAL_OUTCOMES.has(receipt.outcome) ? "refused" : "unknown";
}

/** The process ids of the hubs a plan restarts. */
export function restartedProcesses(plan: IssuedSetupPlan): readonly number[] {
  return plan.effects.flatMap((effect) => (effect.processId === null ? [] : [effect.processId]));
}

/** The dashboard's own words, when they are one short line of text. */
function detailOf(raw: string, fallback: string): string {
  const trimmed = raw.trim();
  return trimmed === "" || trimmed.startsWith("<") || trimmed.length > MAX_DETAIL ? fallback : trimmed;
}

function failure(cause: unknown): { readonly kind: "failed"; readonly detail: string } {
  return { kind: "failed", detail: cause instanceof Error ? cause.message : String(cause) };
}

async function refusal(response: Response): Promise<{ kind: "refused"; status: number; detail: string }> {
  const detail = detailOf(await response.text(), `dashboard returned ${response.status}`);
  return { kind: "refused", status: response.status, detail };
}

/** A fresh version-4 request id; the dashboard refuses any other kind. */
export function newRequestId(): string {
  return globalThis.crypto.randomUUID();
}

/** Ask the preflight which profile the dashboard serves, then ask for one plan of it. */
export async function requestSetupPlan(
  fetcher: typeof fetch = authenticatedFetch,
  requestId: () => string = newRequestId,
): Promise<SetupAnswer<IssuedSetupPlan>> {
  try {
    const preflight = await fetcher(SETUP_PREFLIGHT_URL, { cache: "no-store" });
    if (!preflight.ok) return await refusal(preflight);
    const profile = text(record(await preflight.json())?.["profile"]);
    if (profile === null) return { kind: "failed", detail: "the preflight named no profile" };
    const body = { version: 1, profile, request_id: requestId() };
    const response = await fetcher(SETUP_PLAN_URL, {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify(body),
    });
    if (!response.ok) return await refusal(response);
    const plan = parseIssuedPlan(await response.json());
    return plan === null ? { kind: "failed", detail: "the answer was not a plan" } : { kind: "ok", value: plan };
  } catch (cause) {
    return failure(cause);
  }
}

/** Confirm one plan; the body carries the plan's own request id, digest and one-use nonce. */
export async function applySetupPlan(
  plan: IssuedSetupPlan,
  fetcher: typeof fetch = authenticatedFetch,
): Promise<SetupAnswer<SetupReceipt>> {
  const body = {
    version: 1,
    request_id: plan.requestId,
    plan_id: plan.planId,
    plan_digest: plan.planDigest,
    confirmation_nonce: plan.confirmationNonce,
    confirm: true,
  };
  try {
    const response = await fetcher(SETUP_APPLY_URL, {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify(body),
    });
    if (response.status !== 200) return await refusal(response);
    const receipt = parseSetupReceipt(await response.json());
    return receipt === null ? { kind: "failed", detail: "the answer was not a receipt" } : { kind: "ok", value: receipt };
  } catch (cause) {
    return failure(cause);
  }
}

async function readDocument(fetcher: typeof fetch, url: string): Promise<Fields | null> {
  const response = await fetcher(url, { cache: "no-store" });
  return response.ok ? record(await response.json()) : null;
}

/**
 * Read the apply's receipt back from the chain. An answered receipt is read by
 * its sequence and must carry the same digest; without one (a refusal, or no
 * answer at all), the newest apply receipt of the plan's request is read.
 */
export async function readBackReceipt(
  plan: IssuedSetupPlan,
  answered: SetupReceipt | null,
  fetcher: typeof fetch = authenticatedFetch,
): Promise<SetupReceipt | null> {
  try {
    if (answered !== null) {
      const document = await readDocument(fetcher, `${SETUP_RECEIPTS_URL}?sequence=${answered.sequence}`);
      const receipt = parseSetupReceipt(document?.["receipt"]);
      return receipt !== null && receipt.receiptDigest === answered.receiptDigest ? receipt : null;
    }
    const document = await readDocument(fetcher, SETUP_RECEIPTS_URL);
    const listed = Array.isArray(document?.["receipts"]) ? (document["receipts"] as unknown[]) : [];
    const own = listed
      .map(parseSetupReceipt)
      .find(
        (receipt): receipt is SetupReceipt =>
          receipt !== null && receipt.requestId === plan.requestId && receipt.capability === "setup_apply",
      );
    return own ?? null;
  } catch {
    return null;
  }
}

/** The setup step's reads and writes, through the cockpit's authenticated fetch. */
export const SETUP_APPLY_SOURCE: SetupApplySource = {
  plan: () => requestSetupPlan(),
  apply: (plan) => applySetupPlan(plan),
  readBack: (plan, answered) => readBackReceipt(plan, answered),
};
