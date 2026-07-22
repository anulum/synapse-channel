// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — the HUD strip: mark, KPI triad, liveness beacon

import type { JSX, ReactNode, Ref } from "react";
import { useCockpitI18n } from "../context/CockpitI18n";
import type { CockpitLocale, MessageKey } from "../lib/i18n";
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
  readonly guideTriggerRef?: Ref<HTMLButtonElement>;
  readonly onOpenGuide?: (() => void) | undefined;
}

const KPI_KEYS: Readonly<Record<string, MessageKey>> = {
  "agents online": "kpi.agents",
  "claims held": "kpi.claims",
  "obs / min": "kpi.observations",
  "risk signals": "kpi.risk",
};

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

function transportKey(status: LiveConnectionStatus): MessageKey {
  if (status === "live") return "hud.transport.live";
  if (status === "unsupported" || status === "fallback") return "hud.transport.fallback";
  if (status === "gap") return "hud.transport.gap";
  return `hud.transport.${status}`;
}

export function Hud({ kpis, live, stamp, transport = "connecting", onSelect, theme = "dark", onToggleTheme, focus = "", onFocusChange, rosterNames = [], density = "cozy", onToggleDensity, accessControl, commandTriggerRef, onOpenPalette, guideTriggerRef, onOpenGuide }: HudProps): JSX.Element {
  const { locale, setLocale, t } = useCockpitI18n();
  const translatedKpi = (label: string): string => {
    const key = KPI_KEYS[label];
    return key === undefined ? label : t(key);
  };
  const shownTransport = t(transportKey(transport));
  return (
    <header className="hud">
      <div className="hud__mark">
        <h1>SYNAPSE</h1>
        <span>{t("hud.product")}</span>
      </div>

      <div className="hud__spacer" />

      <div className="hud__kpis">
        {kpis.map((kpi) =>
          onSelect !== undefined ? (
            <button
              type="button"
              className="kpi kpi--link"
              key={kpi.label}
              title={t("hud.filterKpi", { label: translatedKpi(kpi.label) })}
              onClick={() => onSelect(kpi.label)}
            >
              <span className="kpi__label">{translatedKpi(kpi.label)}</span>
              <span className="kpi__row">
                <span className="kpi__value">{kpi.value}</span>
                <span className={deltaClass(kpi.delta)}>{deltaText(kpi.delta)}</span>
              </span>
            </button>
          ) : (
            <div className="kpi" key={kpi.label}>
              <span className="kpi__label">{translatedKpi(kpi.label)}</span>
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
          aria-label={t("hud.openCommands")}
          onClick={onOpenPalette}
        >
          {t("hud.commands")} <kbd>Ctrl K</kbd>
        </button>
      )}
      {onOpenGuide !== undefined && (
        <button
          ref={guideTriggerRef}
          type="button"
          className="hud__commands hud__guide"
          aria-label={t("hud.openGuide")}
          onClick={onOpenGuide}
        >
          {t("hud.guide")} <kbd>?</kbd>
        </button>
      )}
      <label className="hud__locale">
        <span className="visually-hidden">{t("hud.locale")}</span>
        <select
          value={locale}
          onChange={(event) => setLocale(event.target.value as CockpitLocale)}
          aria-label={t("hud.locale")}
        >
          <option value="en">EN</option>
          <option value="sk">SK</option>
        </select>
      </label>
      {onFocusChange !== undefined && (
        <span className={`hud__focus${focus !== "" ? " hud__focus--on" : ""}`}>
          <input
            className="hud__focus-input"
            list="hud-focus-roster"
            value={focus}
            onChange={(change) => onFocusChange(change.target.value)}
            placeholder={t("hud.focusPlaceholder")}
            aria-label={t("hud.focusLabel")}
            title={t("hud.focusTitle")}
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
              aria-label={t("hud.clearFocus")}
            >
              {t("hud.clear")}
            </button>
          )}
        </span>
      )}
      {onToggleDensity !== undefined && (
        <button
          type="button"
          className="hud__theme"
          onClick={onToggleDensity}
          title={density === "cozy" ? t("hud.densityCompactTitle") : t("hud.densityCozyTitle")}
          aria-label={t("hud.toggleDensity")}
        >
          {density === "cozy" ? t("hud.compact") : t("hud.cozy")}
        </button>
      )}
      {onToggleTheme !== undefined && (
        <button
          type="button"
          className="hud__theme"
          onClick={onToggleTheme}
          title={theme === "dark" ? t("hud.switchLight") : t("hud.switchDark")}
          aria-label={theme === "dark" ? t("hud.switchLight") : t("hud.switchDark")}
        >
          {theme === "dark" ? t("hud.light") : t("hud.dark")}
        </button>
      )}
      <div className={`beacon ${live ? "beacon--live" : "beacon--stale"}`}>
        <span className="beacon__dot" />
        <span>{live ? t("hud.live") : t("hud.stale")}</span>
        <span
          className={`beacon__transport beacon__transport--${transport}`}
          aria-label={t("hud.transport", { status: shownTransport })}
        >
          {shownTransport}
        </span>
        <span className="beacon__stamp">{stamp}</span>
      </div>
    </header>
  );
}
