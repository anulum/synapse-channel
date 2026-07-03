// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — export the shown event window as a self-describing JSON document

// Temporal downloads a workflow's full history as JSON; the cockpit analogue
// exports exactly what the operator is looking at — the filtered, possibly
// brushed window — as a document that STATES its own provenance, its query,
// and its coverage, so a pasted export can never masquerade as the full log.

import type { TimeWindow } from "./brush";
import type { LogQuery } from "./logQuery";
import type { CockpitEvent } from "../types";

/** The exported document: the shown events plus what they are. */
export interface LogExport {
  readonly exported_at: string;
  /** `hub` = attested seq+ts from the durable log; `derived` = snapshot diffs. */
  readonly provenance: "hub" | "derived";
  /** The query that produced this view (text/kinds/order/view). */
  readonly query: LogQuery;
  /** The brushed window's epoch-second edges, or null for the whole view. */
  readonly window: TimeWindow | null;
  readonly count: number;
  readonly events: readonly CockpitEvent[];
}

/**
 * Build the export document for the shown events. `nowMs` is injected so the
 * document is reproducible in tests; events pass through verbatim, payloads
 * included where the provenance carried them.
 */
export function buildLogExport(
  events: readonly CockpitEvent[],
  provenance: "hub" | "derived",
  query: LogQuery,
  window: TimeWindow | null,
  nowMs: number,
): LogExport {
  return {
    exported_at: new Date(nowMs).toISOString(),
    provenance,
    query,
    window,
    count: events.length,
    events,
  };
}

/** A timestamped, provenance-stamped filename for the export download. */
export function exportFilename(provenance: "hub" | "derived", nowMs: number): string {
  const stamp = new Date(nowMs).toISOString().replace(/[:.]/g, "-").slice(0, 19);
  return `cockpit-events-${provenance}-${stamp}.json`;
}
