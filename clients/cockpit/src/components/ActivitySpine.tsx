// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — the activity spine, the cockpit's signature instrument

import { useEffect, useRef } from "react";
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

export function ActivitySpine({ source, onInspect }: SpineProps): JSX.Element {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const events = useRef<CockpitEvent[]>([]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (canvas === null) return;
    const ctx = canvas.getContext("2d");
    if (ctx === null) return;

    const tokens = resolveTokens(document.documentElement);
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
      // Reduced motion: redraw only when an event lands, no continuous scroll.
      draw(Date.now());
    } else {
      frame = window.requestAnimationFrame(loop);
    }

    const unsubscribe =
      source?.subscribe((event) => {
        events.current.push(event);
        if (reduce) draw(Date.now());
      }) ?? null;

    return () => {
      unsubscribe?.();
      observer.disconnect();
      if (frame !== 0) window.cancelAnimationFrame(frame);
    };
  }, [source]);

  // The inspect handler is reserved for brush-to-correlate (next slice); reference
  // it so the prop contract is live without an unused-parameter error.
  void onInspect;

  return (
    <section className="spine" aria-label="Fleet activity spine">
      <canvas ref={canvasRef} className="spine__canvas" />
      <div className="spine__lanes" aria-hidden="true">
        {LANES.map((lane) => (
          <div key={lane} className="spine__lane-label">
            {lane}
          </div>
        ))}
      </div>
    </section>
  );
}
