// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — dead letters: messages addressed to identities nobody reads

// The hub records a dead letter when a message targets an identity whose
// cursor nobody advances — the coordination failure where a directive sits
// unread while its sender believes it was delivered. The hub counts them per
// target; the cockpit's job is to make that count impossible to miss.

import type { FleetSnapshot } from "../types";

/** One unread-target record as `state.dead_letters` carries it. */
export interface DeadLetterView {
  /** The identity whose messages nobody is reading. */
  readonly target: string;
  /** How many messages sit unread for that target. */
  readonly count: number;
  /** Who sent the most recent one, "" when unrecorded. */
  readonly lastSender: string;
  /** Epoch seconds of the most recent one, or null when unrecorded. */
  readonly lastTs: number | null;
}

function asString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function asCount(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? Math.trunc(value) : 0;
}

/**
 * Read the hub's dead-letter records from a snapshot, newest first. Tolerant
 * field-by-field; a hub without the surface (pre-0.95) yields an empty list.
 */
export function parseDeadLetters(snapshot: FleetSnapshot | null): DeadLetterView[] {
  if (snapshot === null) return [];
  const raw = snapshot.state["dead_letters"];
  if (!Array.isArray(raw)) return [];
  const letters: DeadLetterView[] = [];
  for (const entry of raw) {
    if (typeof entry !== "object" || entry === null || Array.isArray(entry)) continue;
    const record = entry as Record<string, unknown>;
    const ts = record["last_ts"];
    letters.push({
      target: asString(record["target"]),
      count: asCount(record["count"]),
      lastSender: asString(record["last_sender"]),
      lastTs: typeof ts === "number" && Number.isFinite(ts) ? ts : null,
    });
  }
  letters.sort((a, b) => (b.lastTs ?? 0) - (a.lastTs ?? 0) || a.target.localeCompare(b.target));
  return letters;
}
