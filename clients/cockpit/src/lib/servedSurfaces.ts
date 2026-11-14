// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — what this dashboard start serves, in its own words
//
// `/capabilities.json` lists every optional surface of the dashboard: its
// path, whether this start serves it, and the exact flag that turns it on.
// The server builds the list from the same table its "not configured"
// answers come from, so the cockpit reads one statement instead of guessing
// from which feeds answered 404. A dashboard older than the summary answers
// 404 here too; the setup assistant then says it cannot tell.

import { createEndpointFeed, type EndpointFeed, type FeedState } from "./feed";

/** The dashboard path of the capability summary. */
export const SERVED_SURFACES_PATH = "/capabilities.json";

/** One optional surface and whether this start serves it. */
export interface ServedSurface {
  readonly name: string;
  readonly path: string;
  /** The start flag that turns the surface on. */
  readonly flag: string;
  readonly configured: boolean;
}

/** The whole capability summary, with counts taken from the rows themselves. */
export interface ServedSurfaces {
  readonly surfaces: readonly ServedSurface[];
  readonly configured: number;
  readonly total: number;
}

function servedSurfaceOf(entry: unknown): ServedSurface | null {
  if (typeof entry !== "object" || entry === null || Array.isArray(entry)) return null;
  const record = entry as Record<string, unknown>;
  const { name, path, flag, configured } = record;
  if (typeof name !== "string" || typeof path !== "string" || typeof flag !== "string") return null;
  if (typeof configured !== "boolean") return null;
  return { name, path, flag, configured };
}

/** Shape the summary payload; null = not a summary at all. Malformed rows are dropped. */
export function parseServedSurfaces(raw: unknown): ServedSurfaces | null {
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) return null;
  const rows = (raw as Record<string, unknown>)["surfaces"];
  if (!Array.isArray(rows)) return null;
  const surfaces = rows
    .map(servedSurfaceOf)
    .filter((surface): surface is ServedSurface => surface !== null);
  return {
    surfaces,
    configured: surfaces.filter((surface) => surface.configured).length,
    total: surfaces.length,
  };
}

/** The summary feed's state; `absent` = a dashboard without the summary. */
export type ServedSurfacesState = FeedState<ServedSurfaces>;

/** Read the summary with the shared endpoint lifecycle (60 s; a start's surfaces do not change). */
export function createServedSurfacesStore(options: {
  readonly url?: string;
  readonly pollMs?: number;
  readonly fetcher?: typeof fetch;
  readonly now?: () => number;
} = {}): EndpointFeed<ServedSurfaces> {
  return createEndpointFeed({
    url: options.url ?? SERVED_SURFACES_PATH,
    pollMs: options.pollMs ?? 60_000,
    parse: parseServedSurfaces,
    ...(options.fetcher !== undefined ? { fetcher: options.fetcher } : {}),
    ...(options.now !== undefined ? { now: options.now } : {}),
  });
}
