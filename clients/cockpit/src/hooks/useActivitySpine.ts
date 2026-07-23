// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — activity-spine source and interaction lifecycle

import type { RefCallback } from "react";
import { useCallback, useEffect, useRef, useState } from "react";

import {
  laneAtY,
  nearestEvent,
  resizeWindow,
  shiftWindow,
  windowFromDrag,
  type TimeWindow,
} from "../lib/brush";
import { LANES } from "../lib/events";
import {
  ACTIVITY_SPINE_WINDOW_SECONDS,
  drawActivitySpine,
  resizeActivitySpineCanvas,
  resolveActivitySpineTokens,
  type ActivitySpineDrag,
  type ActivitySpineGeometry,
} from "../lib/activitySpineCanvas";
import type { CockpitSelection } from "../lib/workspace";
import type { CockpitEvent, EventSource } from "../types";

export interface ActivitySpineHover {
  readonly event: CockpitEvent;
  readonly x: number;
  readonly y: number;
}

interface ActivitySpineOptions {
  readonly source?: EventSource | undefined;
  readonly onInspect?: ((event: CockpitEvent | null) => void) | undefined;
  readonly onBrush?: ((window: TimeWindow | null) => void) | undefined;
  readonly brush?: TimeWindow | null | undefined;
  readonly workspaceSelection?: CockpitSelection | null | undefined;
}

export interface ActivitySpineController {
  readonly canvasRef: RefCallback<HTMLCanvasElement>;
  readonly canvas: HTMLCanvasElement | null;
  readonly hover: ActivitySpineHover | null;
}

/**
 * Own the activity spine's live source, animation, and input lifecycle. Pixel
 * projection is delegated to `activitySpineCanvas`; the component only lays
 * out the accessible canvas, labels, legend, and tooltip.
 */
export function useActivitySpine({
  source,
  onInspect,
  onBrush,
  brush,
  workspaceSelection,
}: ActivitySpineOptions): ActivitySpineController {
  const [canvas, setCanvas] = useState<HTMLCanvasElement | null>(null);
  const canvasRef = useCallback((node: HTMLCanvasElement | null) => setCanvas(node), []);
  const [hover, setHover] = useState<ActivitySpineHover | null>(null);
  const events = useRef<CockpitEvent[]>([]);
  const selection = useRef<TimeWindow | null>(null);
  const selectedContext = useRef<CockpitSelection | null>(null);
  const redraw = useRef<(() => void) | null>(null);
  const dragOrigin = useRef<number | null>(null);
  const dragCurrent = useRef<number | null>(null);

  useEffect(() => {
    selection.current = brush ?? null;
    redraw.current?.();
  }, [brush]);

  useEffect(() => {
    selectedContext.current = workspaceSelection ?? null;
    redraw.current?.();
  }, [workspaceSelection]);

  useEffect(() => {
    if (canvas === null) return;
    const context = canvas.getContext("2d");
    if (context === null) return;

    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let tokens = resolveActivitySpineTokens(document.documentElement);
    let geometry: ActivitySpineGeometry = resizeActivitySpineCanvas(
      canvas,
      context,
      window.devicePixelRatio,
    );

    const activeDrag = (): ActivitySpineDrag | null =>
      dragOrigin.current === null || dragCurrent.current === null
        ? null
        : {
            left: Math.min(dragOrigin.current, dragCurrent.current),
            right: Math.max(dragOrigin.current, dragCurrent.current),
          };

    const draw = (nowMs: number): void => {
      events.current = [...drawActivitySpine(context, geometry, tokens, {
        events: events.current,
        nowMs,
        selection: selection.current,
        drag: activeDrag(),
        workspaceSelection: selectedContext.current,
      })];
    };
    redraw.current = () => draw(Date.now());

    const themeWatcher = new MutationObserver(() => {
      tokens = resolveActivitySpineTokens(document.documentElement);
      draw(Date.now());
    });
    themeWatcher.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme"],
    });

    const resize = (): void => {
      geometry = resizeActivitySpineCanvas(canvas, context, window.devicePixelRatio);
      if (reduce) draw(Date.now());
    };
    const observer = new ResizeObserver(resize);
    observer.observe(canvas);

    let frame = 0;
    const loop = (): void => {
      draw(Date.now());
      frame = window.requestAnimationFrame(loop);
    };
    if (reduce) draw(Date.now());
    else frame = window.requestAnimationFrame(loop);

    const localX = (clientX: number): number => clientX - canvas.getBoundingClientRect().left;
    const localY = (clientY: number): number => clientY - canvas.getBoundingClientRect().top;

    const onPointerDown = (pointer: PointerEvent): void => {
      dragOrigin.current = localX(pointer.clientX);
      dragCurrent.current = dragOrigin.current;
      canvas.setPointerCapture(pointer.pointerId);
    };

    const onPointerMove = (pointer: PointerEvent): void => {
      const x = localX(pointer.clientX);
      const y = localY(pointer.clientY);
      if (dragOrigin.current !== null) {
        dragCurrent.current = x;
        setHover(null);
        if (reduce) draw(Date.now());
        return;
      }
      const lane = laneAtY(y, canvas.clientHeight, LANES);
      const found = nearestEvent(
        events.current,
        x,
        canvas.clientWidth,
        ACTIVITY_SPINE_WINDOW_SECONDS,
        Date.now(),
        lane,
      );
      setHover(found === null ? null : { event: found, x, y });
      onInspect?.(found);
    };

    const onPointerUp = (pointer: PointerEvent): void => {
      if (dragOrigin.current === null) return;
      const from = dragOrigin.current;
      const to = localX(pointer.clientX);
      dragOrigin.current = null;
      dragCurrent.current = null;
      const next = windowFromDrag(
        from,
        to,
        canvas.clientWidth,
        ACTIVITY_SPINE_WINDOW_SECONDS,
        Date.now(),
      );
      selection.current = next;
      onBrush?.(next);
      if (reduce) draw(Date.now());
    };

    const onPointerLeave = (): void => {
      setHover(null);
      onInspect?.(null);
    };

    const onKeyDown = (key: KeyboardEvent): void => {
      if (key.key === "Escape") {
        dragOrigin.current = null;
        dragCurrent.current = null;
        selection.current = null;
        onBrush?.(null);
        if (reduce) draw(Date.now());
        return;
      }
      let next: TimeWindow | null = null;
      if (key.key === "ArrowLeft" || key.key === "ArrowRight") {
        const direction = key.key === "ArrowLeft" ? -1 : 1;
        next = shiftWindow(
          selection.current,
          (key.shiftKey ? 5 : 1) * direction,
          Date.now(),
          ACTIVITY_SPINE_WINDOW_SECONDS,
        );
      } else if ((key.key === "[" || key.key === "]") && selection.current !== null) {
        next = resizeWindow(
          selection.current,
          key.key === "[" ? -2 : 2,
          Date.now(),
          ACTIVITY_SPINE_WINDOW_SECONDS,
        );
      } else return;
      key.preventDefault();
      selection.current = next;
      onBrush?.(next);
      if (reduce) draw(Date.now());
    };

    canvas.addEventListener("pointerdown", onPointerDown);
    canvas.addEventListener("pointermove", onPointerMove);
    canvas.addEventListener("pointerup", onPointerUp);
    canvas.addEventListener("pointerleave", onPointerLeave);
    canvas.addEventListener("keydown", onKeyDown);
    const unsubscribe = source?.subscribe((event) => {
      events.current.push(event);
      if (reduce) draw(Date.now());
    }) ?? null;

    return () => {
      unsubscribe?.();
      canvas.removeEventListener("pointerdown", onPointerDown);
      canvas.removeEventListener("pointermove", onPointerMove);
      canvas.removeEventListener("pointerup", onPointerUp);
      canvas.removeEventListener("pointerleave", onPointerLeave);
      canvas.removeEventListener("keydown", onKeyDown);
      observer.disconnect();
      themeWatcher.disconnect();
      redraw.current = null;
      if (frame !== 0) window.cancelAnimationFrame(frame);
    };
  }, [canvas, source, onInspect, onBrush]);

  return { canvasRef, canvas, hover };
}
