// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — pure activity-spine canvas projection

import { COLOUR_OF, LANES } from "./events";
import { eventMatchesSelection } from "./selection";
import type { CockpitSelection } from "./workspace";
import type { CockpitEvent, Lane } from "../types";

/** Seconds of retained evidence visible before an impulse leaves the spine. */
export const ACTIVITY_SPINE_WINDOW_SECONDS = 75;

const TOKEN_NAMES = [
  "--info",
  "--warn",
  "--healthy",
  "--critical",
  "--dim",
  "--now",
  "--line",
  "--line-soft",
  "--panel",
  "--ink",
] as const;

type TokenName = (typeof TOKEN_NAMES)[number];
export type ActivitySpineTokens = Readonly<Record<TokenName, string>>;

export interface ActivitySpineGeometry {
  readonly width: number;
  readonly height: number;
}

export interface ActivitySpineDrag {
  readonly left: number;
  readonly right: number;
}

export interface ActivitySpineFrame {
  readonly events: readonly CockpitEvent[];
  readonly nowMs: number;
  readonly selection: { readonly fromTs: number; readonly toTs: number } | null;
  readonly drag: ActivitySpineDrag | null;
  readonly workspaceSelection: CockpitSelection | null;
}

/** Resolve the canvas palette from the same CSS custom properties as the DOM. */
export function resolveActivitySpineTokens(root: HTMLElement): ActivitySpineTokens {
  const style = getComputedStyle(root);
  const tokens = {} as Record<TokenName, string>;
  for (const name of TOKEN_NAMES) {
    tokens[name] = style.getPropertyValue(name).trim() || "#ffffff";
  }
  return tokens;
}

/** Resize the backing bitmap for a crisp CSS-sized canvas and return its geometry. */
export function resizeActivitySpineCanvas(
  canvas: HTMLCanvasElement,
  context: CanvasRenderingContext2D,
  devicePixelRatio: number,
): ActivitySpineGeometry {
  const width = canvas.clientWidth;
  const height = canvas.clientHeight;
  const ratio = devicePixelRatio || 1;
  canvas.width = Math.floor(width * ratio);
  canvas.height = Math.floor(height * ratio);
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  return { width, height };
}

function colourFor(kind: CockpitEvent["kind"], tokens: ActivitySpineTokens): string {
  const name = COLOUR_OF[kind].slice(4, -1) as TokenName;
  return tokens[name];
}

function laneBand(lane: Lane, height: number): { readonly top: number; readonly base: number } {
  const index = LANES.indexOf(lane);
  const band = height / LANES.length;
  const top = index * band;
  return { top, base: top + band * 0.86 };
}

function xOf(ts: number, now: number, width: number): number {
  return width - ((now - ts) / ACTIVITY_SPINE_WINDOW_SECONDS) * width;
}

/**
 * Paint one complete activity-spine frame and return the still-retained event
 * window. This function owns pixels only: subscriptions, animation, pointer
 * capture, keyboard navigation, and React state remain outside it.
 */
export function drawActivitySpine(
  context: CanvasRenderingContext2D,
  geometry: ActivitySpineGeometry,
  tokens: ActivitySpineTokens,
  frame: ActivitySpineFrame,
): readonly CockpitEvent[] {
  const { width, height } = geometry;
  const now = frame.nowMs / 1000;
  const cutoff = now - ACTIVITY_SPINE_WINDOW_SECONDS;
  const kept = frame.events.filter((event) => event.ts >= cutoff);

  context.clearRect(0, 0, width, height);

  context.strokeStyle = tokens["--line-soft"];
  context.lineWidth = 1;
  for (const lane of LANES) {
    const { base } = laneBand(lane, height);
    context.beginPath();
    context.moveTo(0, base + 0.5);
    context.lineTo(width, base + 0.5);
    context.stroke();
  }
  context.strokeStyle = tokens["--line"];
  for (let seconds = 0; seconds <= ACTIVITY_SPINE_WINDOW_SECONDS; seconds += 15) {
    const x = width - (seconds / ACTIVITY_SPINE_WINDOW_SECONDS) * width;
    context.globalAlpha = 0.4;
    context.beginPath();
    context.moveTo(x + 0.5, 0);
    context.lineTo(x + 0.5, height);
    context.stroke();
  }
  context.globalAlpha = 1;

  const selectedRange = frame.drag ?? (frame.selection === null
    ? null
    : {
        left: xOf(frame.selection.fromTs, now, width),
        right: xOf(frame.selection.toTs, now, width),
      });
  if (selectedRange !== null && selectedRange.right > 0 && selectedRange.left < width) {
    const left = Math.max(0, selectedRange.left);
    const right = Math.min(width, selectedRange.right);
    context.fillStyle = tokens["--now"];
    context.globalAlpha = 0.08;
    context.fillRect(left, 0, right - left, height);
    context.globalAlpha = 0.5;
    context.strokeStyle = tokens["--now"];
    context.beginPath();
    context.moveTo(left + 0.5, 0);
    context.lineTo(left + 0.5, height);
    context.moveTo(right - 0.5, 0);
    context.lineTo(right - 0.5, height);
    context.stroke();
    context.globalAlpha = 1;
  }

  for (const event of kept) {
    const age = now - event.ts;
    const x = width - (age / ACTIVITY_SPINE_WINDOW_SECONDS) * width;
    const { top, base } = laneBand(event.lane, height);
    const impulse = Math.max(3, event.severity * (base - top));
    const alpha = 0.25 + 0.75 * (1 - age / ACTIVITY_SPINE_WINDOW_SECONDS);
    context.globalAlpha = Math.min(1, alpha);
    context.strokeStyle = colourFor(event.kind, tokens);
    context.lineWidth = event.kind === "conflict" ? 2.4 : 1.6;
    context.beginPath();
    context.moveTo(x, base);
    context.lineTo(x, base - impulse);
    context.stroke();
    if (event.severity > 0.6 || event.kind === "conflict") {
      context.fillStyle = colourFor(event.kind, tokens);
      context.beginPath();
      context.arc(x, base - impulse, event.kind === "conflict" ? 2.6 : 1.8, 0, Math.PI * 2);
      context.fill();
    }
    if (eventMatchesSelection(event, frame.workspaceSelection)) {
      context.globalAlpha = 1;
      context.strokeStyle = tokens["--ink"];
      context.lineWidth = 2;
      context.beginPath();
      context.arc(x, base - impulse, 5.5, 0, Math.PI * 2);
      context.stroke();
    }
  }
  context.globalAlpha = 1;

  context.strokeStyle = tokens["--now"];
  context.globalAlpha = 0.85;
  context.beginPath();
  context.moveTo(width - 0.5, 0);
  context.lineTo(width - 0.5, height);
  context.stroke();
  context.globalAlpha = 1;
  return kept;
}
