// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — the HUD strip: mark, KPI triad, liveness beacon

import type { JSX, ReactNode, Ref } from "react";
import type { LiveConnectionStatus } from "../lib/liveTransport";

/** One headline metric with a redundant delta (arrow + colour + sign). */
export interface Kpi {
  readonly label: string;
  readonly value: number;
  /** Signed change since the last window; 0 renders as flat. */
  readonly delta: number;
}

interface HudProps {
  readonly kpis: readonly Kpi[];
  readonly live: boolean;
  /** Freshness stamp, e.g. the last event's wall-clock time. */
  readonly stamp: string;
  /** Primary transport posture; polling fallback remains explicit. */
  readonly transport?: LiveConnectionStatus;
  /** Drill-down: clicking a KPI filters the signal log to its event kinds. */
  readonly onSelect?: ((label: string) => void) | undefined;
  /** The active palette, for the toggle's label. */
  readonly theme?: "dark" | "light";
  /** Theme toggle; the control renders only when provided. */
  readonly onToggleTheme?: (() => void) | undefined;
  /** The focus lens: an identity narrowing claims and board, "" = off. */
  readonly focus?: string;
  /** Focus changes from the picker. */
  readonly onFocusChange?: ((focus: string) => void) | undefined;
  /** Live roster names for the picker's suggestions. */
  readonly rosterNames?: readonly string[];
  /** Whether the compact density is on. */
  readonly density?: "cozy" | "compact";
  /** Density toggle; the control renders only when provided. */
  readonly onToggleDensity?: (() => void) | undefined;
  /** Server-authored role orientation, supplied by the shell. */
  readonly accessControl?: ReactNode;
  readonly commandTriggerRef?: Ref<HTMLButtonElement>;
  readonly onOpenPalette?: (() => void) | undefined;
}

function deltaClass(delta: number): string {
  if (delta > 0) return "kpi__delta kpi__delta--up";
  if (delta < 0) return "kpi__delta kpi__delta--down";
  return "kpi__delta kpi__delta--flat";
}

function deltaText(delta: number): string {
  // Redundant coding: arrow + sign + colour, so state never rides on colour alone.
  if (delta > 0) return `▲ +${delta}`;
  if (delta < 0) return `▼ ${delta}`;
  return "• 0";
}

function transportLabel(status: LiveConnectionStatus): string {
  if (status === "live") return "stream";
  if (status === "unsupported" || status === "fallback") return "poll fallback";
  if (status === "gap") return "gap detected";
  return status;
}

export function Hud({ kpis, live, stamp, transport = "connecting", onSelect, theme = "dark", onToggleTheme, focus = "", onFocusChange, rosterNames = [], density = "cozy", onToggleDensity, accessControl, commandTriggerRef, onOpenPalette }: HudProps): JSX.Element {
  return (
    <header className="hud">
      <div className="hud__mark">
        <h1>SYNAPSE</h1>
        <span>channel · cockpit</span>
      </div>

      <div className="hud__spacer" />

      <div className="hud__kpis">
        {kpis.map((kpi) =>
          onSelect !== undefined ? (
            <button
              type="button"
              className="kpi kpi--link"
              key={kpi.label}
              title={`Filter the signal log to ${kpi.label}`}
              onClick={() => onSelect(kpi.label)}
            >
              <span className="kpi__label">{kpi.label}</span>
              <span className="kpi__row">
                <span className="kpi__value">{kpi.value}</span>
                <span className={deltaClass(kpi.delta)}>{deltaText(kpi.delta)}</span>
              </span>
            </button>
          ) : (
            <div className="kpi" key={kpi.label}>
              <span className="kpi__label">{kpi.label}</span>
              <span className="kpi__row">
                <span className="kpi__value">{kpi.value}</span>
                <span className={deltaClass(kpi.delta)}>{deltaText(kpi.delta)}</span>
              </span>
            </div>
          ),
        )}
      </div>

      {accessControl}
      {onOpenPalette !== undefined && (
        <button
          ref={commandTriggerRef}
          type="button"
          className="hud__commands"
          aria-label="Open command palette"
          onClick={onOpenPalette}
        >
          commands <kbd>Ctrl K</kbd>
        </button>
      )}
      {onFocusChange !== undefined && (
        <span className={`hud__focus${focus !== "" ? " hud__focus--on" : ""}`}>
          <input
            className="hud__focus-input"
            list="hud-focus-roster"
            value={focus}
            onChange={(change) => onFocusChange(change.target.value)}
            placeholder="focus identity…"
            aria-label="Focus the claims and board on one identity"
            title="Narrow the claims board and task board to one identity's work"
          />
          <datalist id="hud-focus-roster">
            {rosterNames.map((name) => (
              <option key={name} value={name} />
            ))}
          </datalist>
          {focus !== "" && (
            <button
              type="button"
              className="panel__clear"
              onClick={() => onFocusChange("")}
              aria-label="Clear the focus lens"
            >
              clear
            </button>
          )}
        </span>
      )}
      {onToggleDensity !== undefined && (
        <button
          type="button"
          className="hud__theme"
          onClick={onToggleDensity}
          title={density === "cozy" ? "Tighten the rows for a big fleet" : "Relax the rows"}
          aria-label="Toggle display density"
        >
          {density === "cozy" ? "compact" : "cozy"}
        </button>
      )}
      {onToggleTheme !== undefined && (
        <button
          type="button"
          className="hud__theme"
          onClick={onToggleTheme}
          title={theme === "dark" ? "Switch to the light instrument" : "Switch to the graphite instrument"}
          aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
        >
          {theme === "dark" ? "light" : "dark"}
        </button>
      )}
      <div className={`beacon ${live ? "beacon--live" : "beacon--stale"}`}>
        <span className="beacon__dot" />
        <span>{live ? "live" : "stale"}</span>
        <span
          className={`beacon__transport beacon__transport--${transport}`}
          aria-label={`Live transport: ${transportLabel(transport)}`}
        >
          {transportLabel(transport)}
        </span>
        <span className="beacon__stamp">{stamp}</span>
      </div>
    </header>
  );
}
