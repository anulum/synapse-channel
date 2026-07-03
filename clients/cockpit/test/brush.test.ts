// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — brush-to-correlate geometry and filtering tests

import { describe, expect, it } from "vitest";
import {
  actorsInWindow,
  eventsInWindow,
  inWindow,
  laneAtY,
  nearestEvent,
  resizeWindow,
  shiftWindow,
  tsAtX,
  windowEdgeLabel,
  windowFromDrag,
} from "../src/lib/brush";
import { LANES } from "../src/lib/events";
import type { CockpitEvent } from "../src/types";

const NOW_MS = 1_000_000; // now = 1000 s
const WIDTH = 750;
const WINDOW_SECONDS = 75; // 10 px per second

function event(ts: number, overrides: Partial<CockpitEvent> = {}): CockpitEvent {
  return {
    seq: Math.round(ts * 10),
    ts,
    kind: "claim",
    lane: "claims",
    severity: 0.5,
    actor: "a",
    label: "claimed t",
    taskId: "t",
    ...overrides,
  };
}

describe("tsAtX", () => {
  it("maps the right edge to now, the left edge to windowSeconds ago, and clamps", () => {
    expect(tsAtX(WIDTH, WIDTH, WINDOW_SECONDS, NOW_MS)).toBe(1000);
    expect(tsAtX(0, WIDTH, WINDOW_SECONDS, NOW_MS)).toBe(925);
    expect(tsAtX(375, WIDTH, WINDOW_SECONDS, NOW_MS)).toBe(962.5);
    expect(tsAtX(-50, WIDTH, WINDOW_SECONDS, NOW_MS)).toBe(925);
    expect(tsAtX(9_999, WIDTH, WINDOW_SECONDS, NOW_MS)).toBe(1000);
    expect(tsAtX(10, 0, WINDOW_SECONDS, NOW_MS)).toBe(1000);
  });
});

describe("windowFromDrag", () => {
  it("normalises either drag direction into an ordered window", () => {
    const forward = windowFromDrag(100, 200, WIDTH, WINDOW_SECONDS, NOW_MS);
    const backward = windowFromDrag(200, 100, WIDTH, WINDOW_SECONDS, NOW_MS);
    expect(forward).toEqual({ fromTs: 935, toTs: 945 });
    expect(backward).toEqual(forward);
  });

  it("treats a sub-threshold drag as a click (null)", () => {
    expect(windowFromDrag(100, 103, WIDTH, WINDOW_SECONDS, NOW_MS)).toBeNull();
    expect(windowFromDrag(100, 104, WIDTH, WINDOW_SECONDS, NOW_MS)).not.toBeNull();
  });
});

describe("window filtering", () => {
  const events = [event(995), event(960), event(930, { actor: "b" }), event(930, { actor: "" })];

  it("keeps only events inside the window, inclusive at both edges", () => {
    const window = { fromTs: 930, toTs: 960 };
    expect(inWindow(event(930), window)).toBe(true);
    expect(inWindow(event(960), window)).toBe(true);
    expect(inWindow(event(929.9), window)).toBe(false);
    expect(eventsInWindow(events, window).map((item) => item.ts)).toEqual([960, 930, 930]);
  });

  it("passes everything through with no window and never mutates the input", () => {
    const copy = eventsInWindow(events, null);
    expect(copy).toEqual(events);
    expect(copy).not.toBe(events);
  });

  it("collects distinct named actors inside the window, sorted", () => {
    expect(actorsInWindow(events, { fromTs: 900, toTs: 1000 })).toEqual(["a", "b"]);
    expect(actorsInWindow(events, { fromTs: 900, toTs: 931 })).toEqual(["b"]);
  });
});

describe("nearestEvent", () => {
  const events = [
    event(950, { lane: "claims" }),
    event(951, { lane: "task" }),
    event(940, { lane: "claims" }),
  ];

  it("finds the closest impulse on the pointer's lane within tolerance", () => {
    // x = 250 → ts 950 (10 px per second); 950 rides claims, 951 rides task.
    const found = nearestEvent(events, 250, WIDTH, WINDOW_SECONDS, NOW_MS, "claims");
    expect(found?.ts).toBe(950);
    const foundTask = nearestEvent(events, 250, WIDTH, WINDOW_SECONDS, NOW_MS, "task");
    expect(foundTask?.ts).toBe(951);
  });

  it("searches all lanes when lane is null and prefers the closest", () => {
    // x = 258 → ts 950.8: 951 (0.2 away) beats 950 (0.8 away).
    const found = nearestEvent(events, 258, WIDTH, WINDOW_SECONDS, NOW_MS, null);
    expect(found?.ts).toBe(951);
  });

  it("returns null over empty baseline (outside tolerance)", () => {
    expect(nearestEvent(events, 200, WIDTH, WINDOW_SECONDS, NOW_MS, null)).toBeNull();
    expect(nearestEvent([], 500, WIDTH, WINDOW_SECONDS, NOW_MS, null)).toBeNull();
  });
});

describe("laneAtY", () => {
  it("maps y bands to lanes top-to-bottom and rejects out-of-canvas points", () => {
    expect(laneAtY(0, 132, LANES)).toBe("presence");
    expect(laneAtY(40, 132, LANES)).toBe("claims");
    expect(laneAtY(70, 132, LANES)).toBe("task");
    expect(laneAtY(131, 132, LANES)).toBe("risk");
    expect(laneAtY(-1, 132, LANES)).toBeNull();
    expect(laneAtY(132, 132, LANES)).toBeNull();
    expect(laneAtY(10, 0, LANES)).toBeNull();
    expect(laneAtY(0, 132, [])).toBeNull();
  });
});

describe("windowEdgeLabel", () => {
  it("renders a 24-hour wall-clock stamp", () => {
    expect(windowEdgeLabel(0)).toMatch(/^\d{2}:\d{2}:\d{2}$/);
  });
});

describe("shiftWindow", () => {
  it("moves a window by the step, both directions", () => {
    const window = { fromTs: 950, toTs: 960 };
    expect(shiftWindow(window, 5, NOW_MS, WINDOW_SECONDS)).toEqual({ fromTs: 955, toTs: 965 });
    expect(shiftWindow(window, -5, NOW_MS, WINDOW_SECONDS)).toEqual({ fromTs: 945, toTs: 955 });
  });

  it("clamps at the now-edge and at the oldest visible second, span preserved", () => {
    expect(shiftWindow({ fromTs: 985, toTs: 995 }, 20, NOW_MS, WINDOW_SECONDS)).toEqual({
      fromTs: 990,
      toTs: 1000,
    });
    expect(shiftWindow({ fromTs: 930, toTs: 940 }, -20, NOW_MS, WINDOW_SECONDS)).toEqual({
      fromTs: 925,
      toTs: 935,
    });
  });

  it("seeds a ten-second window ending at now when none exists", () => {
    expect(shiftWindow(null, 1, NOW_MS, WINDOW_SECONDS)).toEqual({ fromTs: 990, toTs: 1000 });
  });
});

describe("resizeWindow", () => {
  it("grows and shrinks symmetrically about the centre", () => {
    const window = { fromTs: 950, toTs: 960 };
    expect(resizeWindow(window, 4, NOW_MS, WINDOW_SECONDS)).toEqual({ fromTs: 948, toTs: 962 });
    expect(resizeWindow(window, -4, NOW_MS, WINDOW_SECONDS)).toEqual({ fromTs: 952, toTs: 958 });
  });

  it("never shrinks below one second and clamps growth to the view", () => {
    const tiny = resizeWindow({ fromTs: 950, toTs: 951.5 }, -4, NOW_MS, WINDOW_SECONDS);
    expect(tiny.toTs - tiny.fromTs).toBeCloseTo(1);
    const huge = resizeWindow({ fromTs: 950, toTs: 960 }, 1000, NOW_MS, WINDOW_SECONDS);
    expect(huge).toEqual({ fromTs: 925, toTs: 1000 });
  });
});
