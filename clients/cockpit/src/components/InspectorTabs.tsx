// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — accessible inspector tab chrome

import type { JSX } from "react";

import { useCockpitI18n } from "../context/CockpitI18n";
import { useInspectorNavigation } from "../hooks/useInspectorNavigation";
import { windowEdgeLabel } from "../lib/brush";
import { INSPECTOR_TABS } from "../lib/workspace";
import { InspectorPanel, type InspectorPanelProps } from "./InspectorPanel";

interface InspectorTabsProps extends Omit<InspectorPanelProps, "onSelectTask" | "prefill"> {
  /** External trace request; nonce permits repeating the same subject. */
  readonly traceRequest?: { readonly subject: string; readonly nonce: number } | undefined;
}

/** Public inspector composition: tab chrome delegates navigation and panel routing. */
export function InspectorTabs({
  onTabChange,
  onSelectionChange,
  traceRequest,
  ...panelProps
}: InspectorTabsProps): JSX.Element {
  const { t } = useCockpitI18n();
  const { tab, events, onClearWindow } = panelProps;
  const window = panelProps.window ?? null;
  const navigation = useInspectorNavigation({ onTabChange, onSelectionChange, traceRequest });
  return (
    <div className="inspector" role="region" aria-label={t("tabs.label")}>
      <div className="inspector__tabs" role="tablist" aria-label={t("tabs.label")}>
        {INSPECTOR_TABS.map((candidate, index) => (
          <button
            key={candidate}
            ref={(element) => { navigation.tabRefs.current[index] = element; }}
            id={`inspector-tab-${candidate}`}
            type="button"
            role="tab"
            tabIndex={tab === candidate ? 0 : -1}
            aria-selected={tab === candidate}
            aria-controls="inspector-panel"
            className={`inspector__tab${tab === candidate ? " inspector__tab--active" : ""}`}
            onClick={() => onTabChange(candidate)}
            onKeyDown={(event) => navigation.onTabKeyDown(event, index)}
          >
            {t(`tab.${candidate}`)}
            {candidate === "log" && <span className="inspector__tab-count">{events.length}</span>}
          </button>
        ))}
        {window !== null && (
          <span className="inspector__brush">
            {`${windowEdgeLabel(window.fromTs)}–${windowEdgeLabel(window.toTs)}`}
            <button type="button" className="panel__clear" onClick={() => onClearWindow?.()} aria-label={t("tabs.clearWindow")}>
              {t("hud.clear")}
            </button>
          </span>
        )}
      </div>
      <InspectorPanel
        {...panelProps}
        onSelectionChange={onSelectionChange}
        onTabChange={onTabChange}
        onSelectTask={navigation.onSelectTask}
        prefill={navigation.prefill}
      />
    </div>
  );
}
