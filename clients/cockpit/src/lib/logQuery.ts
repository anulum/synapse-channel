// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — the signal log's query model: text, kinds, order, shareable state

// The 2025/2026 observability field treats three interactions as table-stakes
// on any live event view: text search over event content, a pause control that
// freezes the view while the feed keeps recording, and a filter state an
// operator can share as a URL. This module is that query model — pure and
// URL-round-trippable; the log view applies it, the address bar carries it.

import type { CockpitEvent, EventKind } from "../types";

/** All kinds, in the event model's order — the kind filter's domain. */
export const ALL_KINDS: readonly EventKind[] = [
  "presence",
  "claim",
  "lease",
  "release",
  "task",
  "chat",
  "finding",
  "conflict",
];

/** The signal log's query: free text, a kind subset, render order, and view. */
export interface LogQuery {
  /** Case-insensitive substring over actor, label, task id, and kind. */
  readonly text: string;
  /** Kinds to keep; null = every kind (no filter). */
  readonly kinds: readonly EventKind[] | null;
  /** Newest-first is the live default; oldest-first reads a window as a story. */
  readonly order: "newest" | "oldest";
  /** Flat = one row per event; compact = one row per task with its lifecycle. */
  readonly view: "flat" | "compact";
}

/** The query that filters nothing: empty text, all kinds, newest first, flat. */
export const OPEN_QUERY: LogQuery = { text: "", kinds: null, order: "newest", view: "flat" };

/** Whether one event matches the query's text and kind constraints. */
export function matchesQuery(event: CockpitEvent, query: LogQuery): boolean {
  if (query.kinds !== null && !query.kinds.includes(event.kind)) return false;
  const text = query.text.trim().toLowerCase();
  if (text === "") return true;
  return (
    event.label.toLowerCase().includes(text) ||
    event.actor.toLowerCase().includes(text) ||
    event.taskId.toLowerCase().includes(text) ||
    event.kind.includes(text)
  );
}

/**
 * Apply a query to a newest-first event list: constraint filtering, then the
 * requested order. The input is never mutated.
 */
export function applyQuery(
  events: readonly CockpitEvent[],
  query: LogQuery,
): CockpitEvent[] {
  const kept = events.filter((event) => matchesQuery(event, query));
  if (query.order === "oldest") kept.reverse();
  return kept;
}

/** Whether the query constrains anything (drives the "clear query" control). */
export function isConstrained(query: LogQuery): boolean {
  return (
    query.text.trim() !== "" ||
    query.kinds !== null ||
    query.order !== "newest" ||
    query.view !== "flat"
  );
}

/**
 * Serialise a query into URL-hash parameters, omitting defaults so an
 * unconstrained view keeps a clean address. The result round-trips through
 * {@link queryFromHash}.
 */
export function queryToHash(query: LogQuery): string {
  const params = new URLSearchParams();
  if (query.text.trim() !== "") params.set("q", query.text.trim());
  if (query.kinds !== null) params.set("kinds", query.kinds.join(","));
  if (query.order !== "newest") params.set("order", query.order);
  if (query.view !== "flat") params.set("view", query.view);
  return params.toString();
}

/**
 * Parse a query from URL-hash parameters. Unknown kinds are dropped; a kinds
 * parameter that names none survives as null (no filter), never as an
 * accidentally-empty filter that hides everything.
 */
export function queryFromHash(hash: string): LogQuery {
  const params = new URLSearchParams(hash.startsWith("#") ? hash.slice(1) : hash);
  const rawKinds = params.get("kinds");
  let kinds: readonly EventKind[] | null = null;
  if (rawKinds !== null) {
    const parsed = rawKinds
      .split(",")
      .map((kind) => kind.trim())
      .filter((kind): kind is EventKind => (ALL_KINDS as readonly string[]).includes(kind));
    kinds = parsed.length > 0 ? parsed : null;
  }
  return {
    text: params.get("q") ?? "",
    kinds,
    order: params.get("order") === "oldest" ? "oldest" : "newest",
    view: params.get("view") === "compact" ? "compact" : "flat",
  };
}
