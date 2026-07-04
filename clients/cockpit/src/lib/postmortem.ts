// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — post-mortem: the signal log opened over an exported document

// The export button hands the shown window over as a self-describing JSON
// document; this module is the way back in. An incident review opens that
// file in the cockpit — no hub, no infrastructure, the same table and
// filters over exactly what was captured — and the banner states what the
// document says about itself (its provenance, when it was exported, what
// query produced it). A SaaS cockpit cannot do this; a local-first one must.

import type { CockpitEvent, EventKind, Lane } from "../types";

/** A validated, loaded export ready to render. */
export interface PostMortem {
  /** The document's events, newest first (re-sorted defensively). */
  readonly events: readonly CockpitEvent[];
  /** What the document claims about its own origin. */
  readonly provenance: "hub" | "derived";
  readonly exportedAt: string;
  readonly count: number;
}

const KINDS: readonly EventKind[] = [
  "presence",
  "claim",
  "lease",
  "release",
  "task",
  "chat",
  "finding",
  "conflict",
];
const LANES: readonly Lane[] = ["presence", "claims", "task", "risk"];

function asEvent(value: unknown): CockpitEvent | null {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return null;
  const record = value as Record<string, unknown>;
  const kind = record["kind"];
  const lane = record["lane"];
  if (typeof record["seq"] !== "number" || typeof record["ts"] !== "number") return null;
  if (!KINDS.includes(kind as EventKind) || !LANES.includes(lane as Lane)) return null;
  const payload = record["payload"];
  const carriesPayload =
    typeof payload === "object" && payload !== null && !Array.isArray(payload);
  return {
    seq: record["seq"],
    ts: record["ts"],
    kind: kind as EventKind,
    lane: lane as Lane,
    severity: typeof record["severity"] === "number" ? record["severity"] : 0,
    actor: typeof record["actor"] === "string" ? record["actor"] : "",
    label: typeof record["label"] === "string" ? record["label"] : "",
    taskId: typeof record["taskId"] === "string" ? record["taskId"] : "",
    ...(carriesPayload ? { payload: payload as Record<string, unknown> } : {}),
  };
}

/**
 * Validate a parsed export document into a {@link PostMortem}. Returns null
 * when the document is not an export at all; individually malformed events
 * are dropped, not repaired — a post-mortem never invents data.
 */
export function parseLogExport(raw: unknown): PostMortem | null {
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) return null;
  const record = raw as Record<string, unknown>;
  const provenance = record["provenance"];
  if (provenance !== "hub" && provenance !== "derived") return null;
  if (!Array.isArray(record["events"])) return null;
  const events = record["events"]
    .map(asEvent)
    .filter((event): event is CockpitEvent => event !== null)
    .sort((a, b) => b.seq - a.seq);
  return {
    events,
    provenance,
    exportedAt: typeof record["exported_at"] === "string" ? record["exported_at"] : "",
    count: events.length,
  };
}

/**
 * Read and validate an export from a picked file. Resolves null for a file
 * that is not valid JSON or not an export document.
 */
export async function readLogExportFile(file: Blob): Promise<PostMortem | null> {
  try {
    return parseLogExport(JSON.parse(await file.text()) as unknown);
  } catch {
    return null;
  }
}
