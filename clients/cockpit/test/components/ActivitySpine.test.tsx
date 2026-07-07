// @vitest-environment jsdom
// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — activity spine behaviour tests (canvas stubbed; jsdom draws nothing)

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ActivitySpine } from "../../src/components/ActivitySpine";
import type { CockpitEvent, EventSource } from "../../src/types";

/** A 2d-context stand-in recording calls; jsdom ships no canvas at all. */
function contextStub(): CanvasRenderingContext2D {
  const stub = {
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
  };
  return stub as unknown as CanvasRenderingContext2D;
}

class ResizeObserverStub {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

beforeEach(() => {
  vi.stubGlobal("ResizeObserver", ResizeObserverStub);
  // Reduced motion keeps the draw loop off; frames happen only on changes.
  vi.stubGlobal(
    "matchMedia",
    vi.fn().mockReturnValue({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() }),
  );
  HTMLCanvasElement.prototype.setPointerCapture = vi.fn();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function eventOf(seq: number): CockpitEvent {
  return {
    seq,
    ts: Date.now() / 1000,
    kind: "claim",
    lane: "claims",
    severity: 0.8,
    actor: "quantum/claude",
    label: `claimed t-${seq}`,
    taskId: `t-${seq}`,
  };
}

describe("ActivitySpine", () => {
  it("renders the four lane labels, the legend, and an accessible canvas", () => {
    render(<ActivitySpine />);
    for (const lane of ["presence", "claims", "task", "risk"]) {
      expect(screen.getByText(lane)).toBeTruthy();
    }
    expect(screen.getByText("conflict")).toBeTruthy();
    expect(screen.getByLabelText(/Drag or use the arrow keys/)).toBeTruthy();
  });

  it("stays a flat structural baseline when jsdom offers no 2d context", () => {
    // getContext is undefined in jsdom: the effect must bail without throwing.
    render(<ActivitySpine />);
    expect(document.querySelector(".spine__canvas")).not.toBeNull();
  });

  it("draws events pushed by its source and stops the subscription on unmount", () => {
    const ctx = contextStub();
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(ctx);
    const unsubscribe = vi.fn();
    // A holder object dodges TS's control-flow narrowing (the assignment
    // happens inside the subscribe callback, invisible to the checker).
    const pushRef: { fn: ((event: CockpitEvent) => void) | null } = { fn: null };
    const source: EventSource = {
      subscribe(listener) {
        pushRef.fn = listener;
        return unsubscribe;
      },
      stop() {},
    };
    const { unmount } = render(<ActivitySpine source={source} />);
    const drawsBefore = (ctx.clearRect as ReturnType<typeof vi.fn>).mock.calls.length;
    expect(pushRef.fn).not.toBeNull();
    pushRef.fn?.(eventOf(1));
    // Reduced motion redraws per change: the push costs exactly one frame.
    expect((ctx.clearRect as ReturnType<typeof vi.fn>).mock.calls.length).toBe(drawsBefore + 1);
    unmount();
    expect(unsubscribe).toHaveBeenCalled();
  });

  it("brushes from the keyboard: arrows seed and shift, Escape clears", () => {
    const ctx = contextStub();
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(ctx);
    const onBrush = vi.fn();
    render(<ActivitySpine onBrush={onBrush} />);
    const canvas = document.querySelector(".spine__canvas") as HTMLCanvasElement;
    fireEvent.keyDown(canvas, { key: "ArrowLeft" });
    expect(onBrush).toHaveBeenCalledTimes(1);
    const seeded = onBrush.mock.calls[0]?.[0] as { fromTs: number; toTs: number };
    expect(seeded.toTs - seeded.fromTs).toBeGreaterThan(0);
    fireEvent.keyDown(canvas, { key: "]" });
    expect(onBrush).toHaveBeenCalledTimes(2);
    fireEvent.keyDown(canvas, { key: "Escape" });
    expect(onBrush).toHaveBeenLastCalledWith(null);
    // A key the spine does not own is left alone.
    fireEvent.keyDown(canvas, { key: "a" });
    expect(onBrush).toHaveBeenCalledTimes(3);
  });

  it("brushes from a pointer drag and hands the window to the caller", () => {
    const ctx = contextStub();
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(ctx);
    const onBrush = vi.fn();
    render(<ActivitySpine onBrush={onBrush} />);
    const canvas = document.querySelector(".spine__canvas") as HTMLCanvasElement;
    fireEvent.pointerDown(canvas, { clientX: 10, pointerId: 1 });
    fireEvent.pointerMove(canvas, { clientX: 60, pointerId: 1 });
    fireEvent.pointerUp(canvas, { clientX: 60, pointerId: 1 });
    expect(onBrush).toHaveBeenCalledTimes(1);
  });

  it("clears the hover inspection when the pointer leaves", () => {
    const ctx = contextStub();
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(ctx);
    const onInspect = vi.fn();
    render(<ActivitySpine onInspect={onInspect} />);
    const canvas = document.querySelector(".spine__canvas") as HTMLCanvasElement;
    fireEvent.pointerLeave(canvas);
    expect(onInspect).toHaveBeenLastCalledWith(null);
  });
});
