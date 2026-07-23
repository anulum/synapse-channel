// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — defensive parsing for the hub-attested event tail

/** One stored event as the multihub wire encodes it. */
export interface StoredEvent {
  readonly seq: number;
  readonly ts: number;
  readonly kind: string;
  readonly payload: Record<string, unknown>;
}

/** One parsed cursor page from the durable event endpoint. */
export interface ParsedEventsTail {
  readonly events: StoredEvent[];
  readonly nextCursor: number;
  readonly historyIncluded: boolean;
}

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function asString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function asNumber(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

/** Parse one wire event tolerantly; a malformed entry yields seq 0 (dropped). */
export function parseStoredEvent(raw: unknown): StoredEvent {
  const event = asRecord(raw);
  return {
    seq: Math.trunc(asNumber(event["seq"])),
    ts: asNumber(event["ts"]),
    kind: asString(event["kind"]),
    payload: asRecord(event["payload"]),
  };
}

/** Parse a tail response; `null` when the payload is not an object at all. */
export function parseTail(raw: unknown): ParsedEventsTail | null {
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) return null;
  const payload = asRecord(raw);
  const events = Array.isArray(payload["events"])
    ? payload["events"].map(parseStoredEvent).filter((event) => event.seq > 0)
    : [];
  return {
    events,
    nextCursor: Math.trunc(asNumber(payload["next_cursor"])),
    historyIncluded: payload["history_included"] === true,
  };
}
