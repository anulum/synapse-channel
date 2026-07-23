// @vitest-environment jsdom
// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — activity-spine pixel projection contracts

import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  ACTIVITY_SPINE_WINDOW_SECONDS,
  drawActivitySpine,
  resizeActivitySpineCanvas,
  resolveActivitySpineTokens,
  type ActivitySpineTokens,
} from "../src/lib/activitySpineCanvas";
import type { CockpitEvent, EventKind } from "../src/types";

function contextStub(): CanvasRenderingContext2D {
  return {
    setTransform: vi.fn(),
    clearRect: vi.fn(),
    beginPath: vi.fn(),
    moveTo: vi.fn(),
    lineTo: vi.fn(),
    stroke: vi.fn(),
    fill: vi.fn(),
    arc: vi.fn(),
    fillRect: vi.fn(),
    strokeStyle: "",
    fillStyle: "",
    lineWidth: 0,
    globalAlpha: 1,
  } as unknown as CanvasRenderingContext2D;
}

const TOKENS: ActivitySpineTokens = {
  "--info": "#01",
  "--warn": "#02",
  "--healthy": "#03",
  "--critical": "#04",
  "--dim": "#05",
  "--now": "#06",
  "--line": "#07",
  "--line-soft": "#08",
  "--panel": "#09",
  "--ink": "#10",
};

function eventOf(
  seq: number,
  kind: EventKind,
  nowSeconds: number,
  overrides: Partial<CockpitEvent> = {},
): CockpitEvent {
  return {
    seq,
    ts: nowSeconds - seq,
    kind,
    lane: kind === "chat" ? "presence" : kind === "conflict" ? "risk" : "claims",
    severity: 0.5,
    actor: `agent-${seq}`,
    label: `event ${seq}`,
    taskId: `task-${seq}`,
    ...overrides,
  };
}

beforeEach(() => document.documentElement.removeAttribute("style"));

describe("activitySpineCanvas", () => {
  it("resolves every CSS token with an explicit honest fallback", () => {
    document.documentElement.style.setProperty("--info", "#123456");
    const tokens = resolveActivitySpineTokens(document.documentElement);
    expect(tokens["--info"]).toBe("#123456");
    expect(tokens["--warn"]).toBe("#ffffff");
    expect(Object.keys(tokens)).toHaveLength(10);
  });

  it("sizes the backing bitmap at the device ratio and falls back at zero", () => {
    const canvas = document.createElement("canvas");
    Object.defineProperties(canvas, {
      clientWidth: { value: 320 },
      clientHeight: { value: 120 },
    });
    const context = contextStub();
    expect(resizeActivitySpineCanvas(canvas, context, 2)).toEqual({ width: 320, height: 120 });
    expect(canvas.width).toBe(640);
    expect(canvas.height).toBe(240);
    expect(context.setTransform).toHaveBeenLastCalledWith(2, 0, 0, 2, 0, 0);
    resizeActivitySpineCanvas(canvas, context, 0);
    expect(context.setTransform).toHaveBeenLastCalledWith(1, 0, 0, 1, 0, 0);
  });

  it("draws structure, clipped drag selection, impulses, caps, and selected evidence", () => {
    const now = 1_800_000_000;
    const context = contextStub();
    const events = [
      eventOf(1, "claim", now, { severity: 0.8 }),
      eventOf(2, "conflict", now, { severity: 0.2 }),
      eventOf(3, "chat", now, { severity: 0 }),
      eventOf(99, "release", now, { ts: now - ACTIVITY_SPINE_WINDOW_SECONDS - 1 }),
    ];
    const kept = drawActivitySpine(context, { width: 300, height: 100 }, TOKENS, {
      events,
      nowMs: now * 1000,
      selection: null,
      drag: { left: -20, right: 340 },
      workspaceSelection: { kind: "event", seq: 1 },
    });

    expect(kept.map((event) => event.seq)).toEqual([1, 2, 3]);
    expect(context.fillRect).toHaveBeenCalledWith(0, 0, 300, 100);
    expect(context.arc).toHaveBeenCalledWith(expect.any(Number), expect.any(Number), 1.8, 0, Math.PI * 2);
    expect(context.arc).toHaveBeenCalledWith(expect.any(Number), expect.any(Number), 2.6, 0, Math.PI * 2);
    expect(context.arc).toHaveBeenCalledWith(expect.any(Number), expect.any(Number), 5.5, 0, Math.PI * 2);
  });

  it("draws a clock-addressed selection and ignores ranges outside the viewport", () => {
    const now = 1_800_000_000;
    const context = contextStub();
    drawActivitySpine(context, { width: 300, height: 100 }, TOKENS, {
      events: [eventOf(1, "release", now, { ts: now + 1, severity: 0.7 })],
      nowMs: now * 1000,
      selection: { fromTs: now - 20, toTs: now - 10 },
      drag: null,
      workspaceSelection: null,
    });
    expect(context.fillRect).toHaveBeenCalledTimes(1);

    const before = (context.fillRect as ReturnType<typeof vi.fn>).mock.calls.length;
    drawActivitySpine(context, { width: 300, height: 100 }, TOKENS, {
      events: [],
      nowMs: now * 1000,
      selection: { fromTs: now - 200, toTs: now - 100 },
      drag: null,
      workspaceSelection: null,
    });
    drawActivitySpine(context, { width: 300, height: 100 }, TOKENS, {
      events: [],
      nowMs: now * 1000,
      selection: null,
      drag: null,
      workspaceSelection: null,
    });
    expect(context.fillRect).toHaveBeenCalledTimes(before);
  });
});
