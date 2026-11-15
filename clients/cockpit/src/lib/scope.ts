// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE CHANNEL — the cockpit's scope: one project's part of the fleet, in the URL and each request

/**
 * A scope narrows what the cockpit shows to one project, or to the work and
 * names without one. It is a lens, not an access boundary: the hub decides what
 * this principal sees, and the scope only narrows the view. The dashboard
 * applies it to the snapshot, the live stream and the event-backed feeds.
 *
 * A peer scope is of another kind: it shows an observed peer hub's folded
 * state alone, advisory and read-only, and no feed of this hub.
 */
export type CockpitScope =
  | { readonly kind: "all" }
  | { readonly kind: "none" }
  | { readonly kind: "project"; readonly namespace: string }
  | { readonly kind: "peer"; readonly hub: string };

/** A scope of this hub's own records. */
export type LocalScope = Exclude<CockpitScope, { readonly kind: "peer" }>;

/** A scope that shows an observed peer hub. */
export type PeerScope = Extract<CockpitScope, { readonly kind: "peer" }>;

/** The default scope: every record the hub sends. */
export const WHOLE_HUB: LocalScope = { kind: "all" };

/** The URL key that carries the scope. */
export const SCOPE_PARAM = "scope";

const NAMESPACE_MAX_LENGTH = 256;
const UNPRINTABLE = /[\p{C}\p{Z}]/u;

function printable(text: string): boolean {
  return [...text].every((character) => character === " " || !UNPRINTABLE.test(character));
}

function validName(name: string): boolean {
  return name !== "" && name.length <= NAMESPACE_MAX_LENGTH && !name.includes("/") && printable(name);
}

/**
 * Read one scope value as the dashboard reads it: `all`, `none`,
 * `project:<namespace>` or `peer:<hub id>`, where the name is printable, at
 * most 256 characters and holds no slash. Any other value is the whole hub, as
 * an unknown value of every other workspace key is its default.
 */
export function parseScope(value: string | null): CockpitScope {
  if (value === "none") return { kind: "none" };
  const separator = value?.indexOf(":") ?? -1;
  if (value === null || separator < 0) return WHOLE_HUB;
  const kind = value.slice(0, separator);
  const name = value.slice(separator + 1);
  if (!validName(name)) return WHOLE_HUB;
  if (kind === "project") return { kind: "project", namespace: name };
  return kind === "peer" ? { kind: "peer", hub: name } : WHOLE_HUB;
}

/** Return the scope as the dashboard's query value, for example `project:<namespace>` or `peer:<hub>`. */
export function scopeText(scope: CockpitScope): string {
  if (scope.kind === "project") return `project:${scope.namespace}`;
  return scope.kind === "peer" ? `peer:${scope.hub}` : scope.kind;
}

/** Return whether two scopes keep the same part of the fleet. */
export function sameScope(a: CockpitScope, b: CockpitScope): boolean {
  return scopeText(a) === scopeText(b);
}

function searchParams(search: string): URLSearchParams {
  return new URLSearchParams(search.startsWith("?") ? search.slice(1) : search);
}

/** Read the scope from a location search string. */
export function scopeFromSearch(search: string): CockpitScope {
  return parseScope(searchParams(search).get(SCOPE_PARAM));
}

/** Write the scope into a search string, keeping every other key; the whole hub is omitted. */
export function scopeToSearch(scope: CockpitScope, currentSearch = ""): string {
  const params = searchParams(currentSearch);
  params.delete(SCOPE_PARAM);
  if (scope.kind !== "all") params.set(SCOPE_PARAM, scopeText(scope));
  const query = params.toString();
  return query === "" ? "" : `?${query}`;
}

/** Append `query` to `url`, after its own query when it has one. */
export function appendQuery(url: string, query: string): string {
  if (query === "") return url;
  return `${url}${url.includes("?") ? "&" : "?"}${query}`;
}

/** Return `url` asking for `scope`; the whole hub leaves it as it is. */
export function withScope(url: string, scope: CockpitScope): string {
  if (scope.kind === "all") return url;
  return appendQuery(url, `${SCOPE_PARAM}=${encodeURIComponent(scopeText(scope))}`);
}

/** One choice of the scope selector, with the counts its part of the fleet holds. */
export interface ScopeOption {
  readonly scope: LocalScope;
  readonly tasks: number;
  readonly agents: number;
}

/** The counts the selector needs from the snapshot's `scope` block. */
export interface ScopeCounts {
  readonly tasksTotal: number;
  readonly agentsTotal: number;
  readonly tasksWithoutProject: number;
  readonly agentsWithoutNamespace: number;
  readonly namespaces: readonly { readonly namespace: string; readonly tasks: number; readonly agents: number }[];
  /** The observed peer hubs; absent from a dashboard older than peer scopes. */
  readonly peers?: readonly string[];
}

/**
 * Return the selector's choices: the whole hub, the work without a project,
 * and each namespace present, in the order the dashboard names them. The
 * current scope is always a choice, with zero counts when the hub no longer
 * has it, so a selection never disappears from under the operator.
 */
export function scopeOptions(counts: ScopeCounts | null, current: CockpitScope): ScopeOption[] {
  const options: ScopeOption[] = [
    { scope: WHOLE_HUB, tasks: counts?.tasksTotal ?? 0, agents: counts?.agentsTotal ?? 0 },
    {
      scope: { kind: "none" },
      tasks: counts?.tasksWithoutProject ?? 0,
      agents: counts?.agentsWithoutNamespace ?? 0,
    },
  ];
  for (const row of counts?.namespaces ?? []) {
    options.push({
      scope: { kind: "project", namespace: row.namespace },
      tasks: row.tasks,
      agents: row.agents,
    });
  }
  if (current.kind !== "peer" && !options.some((option) => sameScope(option.scope, current))) {
    options.push({ scope: current, tasks: 0, agents: 0 });
  }
  return options;
}

/**
 * Return the observed peer hubs as scopes, in the order the dashboard lists
 * them; the current peer stays a choice when the dashboard no longer lists it.
 */
export function peerScopes(counts: ScopeCounts | null, current: CockpitScope): PeerScope[] {
  const peers: PeerScope[] = (counts?.peers ?? []).map((hub) => ({ kind: "peer", hub }));
  if (current.kind === "peer" && !peers.some((peer) => sameScope(peer, current))) peers.push(current);
  return peers;
}
