// @vitest-environment jsdom
// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — activity-spine source and interaction lifecycle contracts

import { act, cleanup, fireEvent, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useActivitySpine } from "../../src/hooks/useActivitySpine";
import type { CockpitEvent, EventSource } from "../../src/types";

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

let resizeCallback: (() => void) | null = null;
let resizeDisconnect = vi.fn();
class ResizeObserverStub {
  constructor(callback: () => void) {
    resizeCallback = callback;
  }
  observe(): void {}
  unobserve(): void {}
  disconnect(): void { resizeDisconnect(); }
}

let mutationCallback: (() => void) | null = null;
let mutationDisconnect = vi.fn();
class MutationObserverStub {
  constructor(callback: () => void) {
    mutationCallback = callback;
  }
  observe(): void {}
  takeRecords(): MutationRecord[] { return []; }
  disconnect(): void { mutationDisconnect(); }
}

function eventOf(seq: number): CockpitEvent {
  return {
    seq,
    ts: Date.now() / 1000,
    kind: "claim",
    lane: "claims",
    severity: 0.8,
    actor: "agent-one",
    label: `event ${seq}`,
    taskId: `task-${seq}`,
  };
}

function canvasWith(context: CanvasRenderingContext2D | null): HTMLCanvasElement {
  const canvas = document.createElement("canvas");
  Object.defineProperties(canvas, {
    clientWidth: { value: 100 },
    clientHeight: { value: 100 },
  });
  vi.spyOn(canvas, "getContext").mockReturnValue(context);
  canvas.setPointerCapture = vi.fn();
  canvas.getBoundingClientRect = () => ({
    x: 0,
    y: 0,
    top: 0,
    left: 0,
    right: 100,
    bottom: 100,
    width: 100,
    height: 100,
    toJSON: () => ({}),
  });
  return canvas;
}

beforeEach(() => {
  resizeCallback = null;
  mutationCallback = null;
  resizeDisconnect = vi.fn();
  mutationDisconnect = vi.fn();
  vi.stubGlobal("ResizeObserver", ResizeObserverStub);
  vi.stubGlobal("MutationObserver", MutationObserverStub);
  vi.stubGlobal("matchMedia", vi.fn().mockReturnValue({ matches: true }));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("useActivitySpine", () => {
  it("stays inert without a canvas or a 2d context", () => {
    const { result } = renderHook(() => useActivitySpine({}));
    expect(result.current.canvas).toBeNull();
    const canvas = canvasWith(null);
    act(() => result.current.canvasRef(canvas));
    expect(result.current.canvas).toBe(canvas);
  });

  it("owns reduced-motion source, hover, brush, theme, resize, and cleanup lifecycle", () => {
    const context = contextStub();
    const canvas = canvasWith(context);
    const unsubscribe = vi.fn();
    let push: ((event: CockpitEvent) => void) | null = null;
    const source: EventSource = {
      subscribe(listener) {
        push = listener;
        return unsubscribe;
      },
      stop() {},
    };
    const onInspect = vi.fn();
    const onBrush = vi.fn();
    const { result, rerender, unmount } = renderHook(
      ({ brush, selected }) => useActivitySpine({
        source,
        onInspect,
        onBrush,
        brush,
        workspaceSelection: selected,
      }),
      {
        initialProps: {
          brush: null as { fromTs: number; toTs: number } | null,
          selected: null as { kind: "event"; seq: number } | null,
        },
      },
    );
    act(() => result.current.canvasRef(canvas));
    expect(push).not.toBeNull();
    act(() => (push as ((event: CockpitEvent) => void) | null)?.(eventOf(1)));

    act(() => fireEvent.pointerMove(canvas, { clientX: 100, clientY: 40 }));
    expect(result.current.hover?.event.seq).toBe(1);
    expect(onInspect).toHaveBeenLastCalledWith(expect.objectContaining({ seq: 1 }));
    act(() => fireEvent.pointerLeave(canvas));
    expect(result.current.hover).toBeNull();
    expect(onInspect).toHaveBeenLastCalledWith(null);
    act(() => fireEvent.pointerMove(canvas, { clientX: 0, clientY: 10 }));
    expect(result.current.hover).toBeNull();
    expect(onInspect).toHaveBeenLastCalledWith(null);

    fireEvent.pointerUp(canvas, { clientX: 50, pointerId: 1 });
    expect(onBrush).not.toHaveBeenCalled();
    fireEvent.pointerDown(canvas, { clientX: 80, pointerId: 1 });
    fireEvent.pointerMove(canvas, { clientX: 20, clientY: 40, pointerId: 1 });
    expect(result.current.hover).toBeNull();
    fireEvent.pointerUp(canvas, { clientX: 20, pointerId: 1 });
    expect(onBrush).toHaveBeenCalledTimes(1);

    fireEvent.keyDown(canvas, { key: "ArrowRight", shiftKey: true });
    fireEvent.keyDown(canvas, { key: "[" });
    fireEvent.keyDown(canvas, { key: "]" });
    fireEvent.keyDown(canvas, { key: "unused" });
    fireEvent.keyDown(canvas, { key: "Escape" });
    expect(onBrush).toHaveBeenLastCalledWith(null);

    rerender({
      brush: { fromTs: Date.now() / 1000 - 10, toTs: Date.now() / 1000 },
      selected: { kind: "event", seq: 1 },
    });
    act(() => resizeCallback?.());
    act(() => mutationCallback?.());
    expect(context.clearRect).toHaveBeenCalled();

    unmount();
    expect(unsubscribe).toHaveBeenCalledTimes(1);
    expect(resizeDisconnect).toHaveBeenCalledTimes(1);
    expect(mutationDisconnect).toHaveBeenCalledTimes(1);
  });

  it("runs and cancels the animation loop when motion is allowed", () => {
    vi.stubGlobal("matchMedia", vi.fn().mockReturnValue({ matches: false }));
    let animate: FrameRequestCallback | null = null;
    const request = vi.spyOn(window, "requestAnimationFrame").mockImplementation((callback) => {
      animate ??= callback;
      return 17;
    });
    const cancel = vi.spyOn(window, "cancelAnimationFrame").mockImplementation(() => {});
    let push: ((event: CockpitEvent) => void) | null = null;
    const source: EventSource = {
      subscribe(listener) {
        push = listener;
        return () => {};
      },
      stop() {},
    };
    const onBrush = vi.fn();
    const { result, unmount } = renderHook(() => useActivitySpine({ source, onBrush }));
    const canvas = canvasWith(contextStub());
    act(() => result.current.canvasRef(canvas));
    expect(request).toHaveBeenCalledTimes(1);
    act(() => (animate as FrameRequestCallback | null)?.(0));
    act(() => resizeCallback?.());
    act(() => (push as ((event: CockpitEvent) => void) | null)?.(eventOf(2)));
    fireEvent.pointerDown(canvas, { clientX: 80, pointerId: 1 });
    fireEvent.pointerMove(canvas, { clientX: 20, clientY: 40, pointerId: 1 });
    fireEvent.keyDown(canvas, { key: "Escape" });
    expect(onBrush).toHaveBeenLastCalledWith(null);
    unmount();
    expect(cancel).toHaveBeenCalledWith(17);
  });
});
