// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — the activity spine, the cockpit's signature instrument

import { useEffect, useRef, useState } from "react";
import {
  laneAtY,
  nearestEvent,
  resizeWindow,
  shiftWindow,
  windowFromDrag,
  type TimeWindow,
} from "../lib/brush";
import { COLOUR_OF, LANES } from "../lib/events";
import type { CockpitEvent, EventSource, Lane } from "../types";

/** Seconds of history held on the spine before an impulse scrolls off the left. */
const WINDOW_SECONDS = 75;

/** Tokens the canvas resolves once from the stylesheet, keeping one palette source. */
const TOKENS = [
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

type TokenName = (typeof TOKENS)[number];

interface SpineProps {
  /**
   * Feed of real events to plot; the caller owns the source's lifecycle. When
   * omitted the spine draws only its structure — a genuinely flat baseline is
   * the honest rendering of "nothing observed".
   */
  readonly source?: EventSource | undefined;
  /** Called when the operator hovers an impulse, for the detail panel. */
  readonly onInspect?: (event: CockpitEvent | null) => void;
  /**
   * Called when the operator brushes a time window (drag) or clears it
   * (click). The window is absolute time, so it scrolls with the spine.
   */
  readonly onBrush?: (window: TimeWindow | null) => void;
  /** The current brushed window (controlled by the caller), or null. */
  readonly brush?: TimeWindow | null;
}

interface HoverState {
  readonly event: CockpitEvent;
  readonly x: number;
  readonly y: number;
}

function resolveTokens(root: HTMLElement): Record<TokenName, string> {
  const style = getComputedStyle(root);
  const out = {} as Record<TokenName, string>;
  for (const token of TOKENS) out[token] = style.getPropertyValue(token).trim() || "#ffffff";
  return out;
}

/** Resolve a `var(--x)` reference emitted by the event colour map to a hex value. */
function colourFor(kind: CockpitEvent["kind"], tokens: Record<TokenName, string>): string {
  const raw = COLOUR_OF[kind]; // e.g. "var(--critical)"
  const name = raw.slice(4, -1) as TokenName;
  return tokens[name] ?? tokens["--dim"];
}

function timeOf(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

export function ActivitySpine({ source, onInspect, onBrush, brush }: SpineProps): JSX.Element {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const events = useRef<CockpitEvent[]>([]);
  const selection = useRef<TimeWindow | null>(null);
  const dragOrigin = useRef<number | null>(null);
  const dragCurrent = useRef<number | null>(null);
  const [hover, setHover] = useState<HoverState | null>(null);

  // The brushed window is caller-owned state: mirror it into the draw loop's
  // ref so clearing it from the signal log also clears the canvas highlight.
  useEffect(() => {
    selection.current = brush ?? null;
  }, [brush]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (canvas === null) return;
    const ctx = canvas.getContext("2d");
    if (ctx === null) return;

    // Tokens re-resolve when the theme attribute flips, so the canvas
    // re-colours on toggle without a remount.
    let tokens = resolveTokens(document.documentElement);
    const themeWatcher = new MutationObserver(() => {
      tokens = resolveTokens(document.documentElement);
    });
    themeWatcher.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let width = 0;
    let height = 0;

    const resize = (): void => {
      const dpr = window.devicePixelRatio || 1;
      width = canvas.clientWidth;
      height = canvas.clientHeight;
      canvas.width = Math.floor(width * dpr);
      canvas.height = Math.floor(height * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    resize();
    const observer = new ResizeObserver(resize);
    observer.observe(canvas);

    const laneBand = (lane: Lane): { top: number; base: number } => {
      const index = LANES.indexOf(lane);
      const band = height / LANES.length;
      const top = index * band;
      return { top, base: top + band * 0.86 };
    };

    const xOf = (ts: number, now: number): number =>
      width - ((now - ts) / WINDOW_SECONDS) * width;

    const draw = (nowMs: number): void => {
      const now = nowMs / 1000;
      const cutoff = now - WINDOW_SECONDS;
      const kept = events.current.filter((event) => event.ts >= cutoff);
      events.current = kept;

      ctx.clearRect(0, 0, width, height);

      // Recessed structural layer: lane baselines + a faint time gridline every 15s.
      ctx.strokeStyle = tokens["--line-soft"];
      ctx.lineWidth = 1;
      for (const lane of LANES) {
        const { base } = laneBand(lane);
        ctx.beginPath();
        ctx.moveTo(0, base + 0.5);
        ctx.lineTo(width, base + 0.5);
        ctx.stroke();
      }
      ctx.strokeStyle = tokens["--line"];
      for (let s = 0; s <= WINDOW_SECONDS; s += 15) {
        const x = width - (s / WINDOW_SECONDS) * width;
        ctx.globalAlpha = 0.4;
        ctx.beginPath();
        ctx.moveTo(x + 0.5, 0);
        ctx.lineTo(x + 0.5, height);
        ctx.stroke();
      }
      ctx.globalAlpha = 1;

      // The brushed window (or the drag in progress): a recessed highlight the
      // impulses render on top of. Absolute time, so it scrolls with the spine.
      const liveDrag =
        dragOrigin.current !== null && dragCurrent.current !== null
          ? {
              left: Math.min(dragOrigin.current, dragCurrent.current),
              right: Math.max(dragOrigin.current, dragCurrent.current),
            }
          : selection.current !== null
            ? {
                left: xOf(selection.current.fromTs, now),
                right: xOf(selection.current.toTs, now),
              }
            : null;
      if (liveDrag !== null && liveDrag.right > 0 && liveDrag.left < width) {
        const left = Math.max(0, liveDrag.left);
        const right = Math.min(width, liveDrag.right);
        ctx.fillStyle = tokens["--now"];
        ctx.globalAlpha = 0.08;
        ctx.fillRect(left, 0, right - left, height);
        ctx.globalAlpha = 0.5;
        ctx.strokeStyle = tokens["--now"];
        ctx.beginPath();
        ctx.moveTo(left + 0.5, 0);
        ctx.lineTo(left + 0.5, height);
        ctx.moveTo(right - 0.5, 0);
        ctx.lineTo(right - 0.5, height);
        ctx.stroke();
        ctx.globalAlpha = 1;
      }

      // Foreground: one discrete impulse per event; height = severity, age = fade.
      for (const event of kept) {
        const age = now - event.ts;
        const x = width - (age / WINDOW_SECONDS) * width;
        const { top, base } = laneBand(event.lane);
        const span = base - top;
        const impulse = Math.max(3, event.severity * span);
        const alpha = 0.25 + 0.75 * (1 - age / WINDOW_SECONDS);
        ctx.globalAlpha = Math.max(0, Math.min(1, alpha));
        ctx.strokeStyle = colourFor(event.kind, tokens);
        ctx.lineWidth = event.kind === "conflict" ? 2.4 : 1.6;
        ctx.beginPath();
        ctx.moveTo(x, base);
        ctx.lineTo(x, base - impulse);
        ctx.stroke();
        // Promote tall/critical spikes with a cap dot so they read at a glance.
        if (event.severity > 0.6 || event.kind === "conflict") {
          ctx.fillStyle = colourFor(event.kind, tokens);
          ctx.beginPath();
          ctx.arc(x, base - impulse, event.kind === "conflict" ? 2.6 : 1.8, 0, Math.PI * 2);
          ctx.fill();
        }
      }
      ctx.globalAlpha = 1;

      // The master-clock now-edge: the only always-on motion cue.
      ctx.strokeStyle = tokens["--now"];
      ctx.globalAlpha = 0.85;
      ctx.beginPath();
      ctx.moveTo(width - 0.5, 0);
      ctx.lineTo(width - 0.5, height);
      ctx.stroke();
      ctx.globalAlpha = 1;
    };

    let frame = 0;
    const loop = (): void => {
      draw(Date.now());
      frame = window.requestAnimationFrame(loop);
    };

    if (reduce) {
      // Reduced motion: redraw only when something changes, no continuous scroll.
      draw(Date.now());
    } else {
      frame = window.requestAnimationFrame(loop);
    }

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
        WINDOW_SECONDS,
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
      const window_ = windowFromDrag(from, to, canvas.clientWidth, WINDOW_SECONDS, Date.now());
      selection.current = window_;
      onBrush?.(window_);
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
      // Keyboard brushing: arrows shift the window (Shift = coarse), the
      // bracket keys resize it; with no window an arrow seeds a ten-second one.
      let next: TimeWindow | null = null;
      if (key.key === "ArrowLeft" || key.key === "ArrowRight") {
        const step = (key.shiftKey ? 5 : 1) * (key.key === "ArrowLeft" ? -1 : 1);
        next = shiftWindow(selection.current, step, Date.now(), WINDOW_SECONDS);
      } else if ((key.key === "[" || key.key === "]") && selection.current !== null) {
        next = resizeWindow(
          selection.current,
          key.key === "[" ? -2 : 2,
          Date.now(),
          WINDOW_SECONDS,
        );
      } else {
        return;
      }
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

    const unsubscribe =
      source?.subscribe((event) => {
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
      if (frame !== 0) window.cancelAnimationFrame(frame);
    };
  }, [source, onInspect, onBrush]);

  return (
    <section className="spine" aria-label="Fleet activity spine">
      <canvas
        ref={canvasRef}
        className="spine__canvas"
        tabIndex={0}
        aria-label="Activity spine. Drag or use the arrow keys to brush a time window that filters the signal log; brackets resize it, Escape clears it. The signal log table is the accessible reading of the same events."
      />
      <div className="spine__lanes" aria-hidden="true">
        {LANES.map((lane) => (
          <div key={lane} className="spine__lane-label">
            {lane}
          </div>
        ))}
      </div>
      <div className="spine__legend" aria-hidden="true">
        <span className="spine-key spine-key--info">claim · finding</span>
        <span className="spine-key spine-key--healthy">release · done</span>
        <span className="spine-key spine-key--warn">lease expiry</span>
        <span className="spine-key spine-key--dim">chatter</span>
        <span className="spine-key spine-key--critical">conflict</span>
      </div>
      {hover !== null && (
        <div
          className="spine__tooltip"
          style={{
            left: Math.min(hover.x + 12, Math.max(0, (canvasRef.current?.clientWidth ?? 0) - 260)),
            top: Math.min(hover.y + 10, 96),
          }}
          role="status"
        >
          <span className="spine__tooltip-meta">
            {timeOf(hover.event.ts)} · {hover.event.kind}
            {hover.event.actor !== "" && ` · ${hover.event.actor}`}
          </span>
          <span className="spine__tooltip-label">{hover.event.label}</span>
        </div>
      )}
    </section>
  );
}
