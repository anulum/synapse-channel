// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — bounded JSON envelope parsing for editor clients

/** Enforce the hub's size, depth, and envelope boundaries before projection. */

import { Buffer } from "node:buffer";

/** Largest inbound frame accepted by the hub and editor client. */
export const MAX_HUB_FRAME_BYTES = 1024 * 1024;

/** Deepest JSON nesting accepted by the matching Python decoder. */
export const MAX_HUB_JSON_DEPTH = 64;

/** JSON object after the outer envelope has passed validation. */
export type JsonRecord = Record<string, unknown>;

/** Safe envelope failures; peer-controlled text never crosses this boundary. */
export type HubEnvelopeError =
  | "frame-too-large"
  | "frame-too-deep"
  | "invalid-json"
  | "invalid-envelope";

/** Result of parsing one bounded JSON envelope. */
export type HubEnvelopeResult =
  | { ok: true; value: JsonRecord }
  | { ok: false; error: HubEnvelopeError };

/** Narrow an unknown JSON value to an object with no array semantics. */
export function isJsonRecord(value: unknown): value is JsonRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Return a non-empty string without modifying peer-controlled content. */
export function nonEmptyString(value: unknown): string | undefined {
  return typeof value === "string" && value.trim().length > 0 ? value : undefined;
}

function jsonDepthExceeded(text: string): boolean {
  let depth = 0;
  let inString = false;
  let escaped = false;
  for (const character of text) {
    if (inString) {
      if (escaped) {
        escaped = false;
      } else if (character === "\\") {
        escaped = true;
      } else if (character === '"') {
        inString = false;
      }
    } else if (character === '"') {
      inString = true;
    } else if (character === "{" || character === "[") {
      depth += 1;
      if (depth > MAX_HUB_JSON_DEPTH) {
        return true;
      }
    } else if (character === "}" || character === "]") {
      depth = Math.max(0, depth - 1);
    }
  }
  return false;
}

/** Parse a complete WebSocket text frame into a bounded hub envelope. */
export function parseHubEnvelope(raw: string): HubEnvelopeResult {
  if (Buffer.byteLength(raw, "utf8") > MAX_HUB_FRAME_BYTES) {
    return { ok: false, error: "frame-too-large" };
  }
  if (jsonDepthExceeded(raw)) {
    return { ok: false, error: "frame-too-deep" };
  }
  let value: unknown;
  try {
    value = JSON.parse(raw) as unknown;
  } catch {
    return { ok: false, error: "invalid-json" };
  }
  if (!isJsonRecord(value) || nonEmptyString(value["type"]) === undefined) {
    return { ok: false, error: "invalid-envelope" };
  }
  return { ok: true, value };
}
