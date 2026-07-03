// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — brush-to-correlate: the spine's time-window query model

// The spine is not ornament: selecting a window on it is the cockpit's primary
// query surface. This module holds the pure geometry and filtering — pixel ↔
// timestamp mapping against the scrolling now-edge, nearest-impulse hit
// testing for hover inspection, and window filtering with the actors involved —
// so every interaction the canvas layer forwards is testable without a DOM.

import type { CockpitEvent } from "../types";

/** A selected time window on the spine, in epoch seconds. */
export interface TimeWindow {
  readonly fromTs: number;
  readonly toTs: number;
}

/**
 * Map a canvas x offset to the timestamp it sits over, given the canvas
 * width, the spine's history window, and "now". The now-edge is the right
 * edge; x = 0 is `windowSeconds` ago.
 */
export function tsAtX(x: number, width: number, windowSeconds: number, nowMs: number): number {
  const now = nowMs / 1000;
  if (width <= 0) return now;
  const clamped = Math.max(0, Math.min(width, x));
  return now - ((width - clamped) / width) * windowSeconds;
}

/**
 * Normalise a drag between two x offsets into a {@link TimeWindow}. Returns
 * `null` for a degenerate drag (under `minPx` of travel), which callers treat
 * as a click, not a brush.
 */
export function windowFromDrag(
  x1: number,
  x2: number,
  width: number,
  windowSeconds: number,
  nowMs: number,
  minPx = 4,
): TimeWindow | null {
  if (Math.abs(x2 - x1) < minPx) return null;
  const a = tsAtX(Math.min(x1, x2), width, windowSeconds, nowMs);
  const b = tsAtX(Math.max(x1, x2), width, windowSeconds, nowMs);
  return { fromTs: a, toTs: b };
}

/** Whether an event's timestamp falls inside a window (inclusive). */
export function inWindow(event: CockpitEvent, window: TimeWindow): boolean {
  return event.ts >= window.fromTs && event.ts <= window.toTs;
}

/** The events inside a window, newest first (the signal log's order). */
export function eventsInWindow(
  events: readonly CockpitEvent[],
  window: TimeWindow | null,
): CockpitEvent[] {
  if (window === null) return [...events];
  return events.filter((event) => inWindow(event, window));
}

/** The distinct actors named by events inside a window, sorted. */
export function actorsInWindow(
  events: readonly CockpitEvent[],
  window: TimeWindow,
): string[] {
  const actors = new Set<string>();
  for (const event of events) {
    if (event.actor !== "" && inWindow(event, window)) actors.add(event.actor);
  }
  return [...actors].sort((a, b) => a.localeCompare(b));
}

/**
 * Find the impulse nearest to a canvas position for hover inspection: the
 * event whose timestamp is closest to the pointer's, within `toleranceSeconds`
 * and, when `lane` is given, on that lane. Returns `null` when nothing is
 * close enough — hovering empty baseline inspects nothing.
 */
export function nearestEvent(
  events: readonly CockpitEvent[],
  x: number,
  width: number,
  windowSeconds: number,
  nowMs: number,
  lane: CockpitEvent["lane"] | null,
  toleranceSeconds = 1.5,
): CockpitEvent | null {
  const target = tsAtX(x, width, windowSeconds, nowMs);
  let best: CockpitEvent | null = null;
  let bestDistance = toleranceSeconds;
  for (const event of events) {
    if (lane !== null && event.lane !== lane) continue;
    const distance = Math.abs(event.ts - target);
    if (distance <= bestDistance) {
      bestDistance = distance;
      best = event;
    }
  }
  return best;
}

/**
 * Map a canvas y offset to the lane band it falls in, mirroring the spine's
 * equal-height horizontal bands. Returns `null` outside the canvas.
 */
export function laneAtY(
  y: number,
  height: number,
  lanes: readonly CockpitEvent["lane"][],
): CockpitEvent["lane"] | null {
  if (height <= 0 || y < 0 || y >= height) return null;
  const index = Math.min(lanes.length - 1, Math.floor((y / height) * lanes.length));
  return lanes[index] ?? null;
}

/** Wall-clock HH:MM:SS label for a window edge. */
export function windowEdgeLabel(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}
