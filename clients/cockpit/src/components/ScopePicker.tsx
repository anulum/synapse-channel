// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE CHANNEL — the head-up display's scope selector

import type { JSX } from "react";

import { useCockpitI18n } from "../context/CockpitI18n";
import {
  parseScope,
  scopeOptions,
  scopeText,
  type CockpitScope,
  type ScopeCounts,
  type ScopeOption,
} from "../lib/scope";

interface ScopePickerProps {
  readonly scope: CockpitScope;
  /** The snapshot's scope block; null before the first snapshot or from a dashboard without one. */
  readonly counts: ScopeCounts | null;
  readonly onChange: (scope: CockpitScope) => void;
}

/**
 * Choose the part of the fleet the cockpit shows: the whole hub, the work
 * without a project, or one namespace, each with its task and agent counts.
 * The counts are the hub's, so a choice never reads as a bare zero.
 */
export function ScopePicker({ scope, counts, onChange }: ScopePickerProps): JSX.Element {
  const { t } = useCockpitI18n();
  const label = (option: ScopeOption): string => {
    const values = { tasks: option.tasks, agents: option.agents };
    if (option.scope.kind === "all") return t("hud.scopeAll", values);
    if (option.scope.kind === "none") return t("hud.scopeNone", values);
    return t("hud.scopeProject", { ...values, namespace: option.scope.namespace });
  };
  return (
    <span className={`hud__scope${scope.kind === "all" ? "" : " hud__scope--on"}`} title={t("hud.scopeTitle")}>
      <select
        className="hud__scope-select"
        aria-label={t("hud.scopeLabel")}
        value={scopeText(scope)}
        onChange={(event) => onChange(parseScope(event.target.value))}
      >
        {scopeOptions(counts, scope).map((option) => (
          <option key={scopeText(option.scope)} value={scopeText(option.scope)}>
            {label(option)}
          </option>
        ))}
      </select>
    </span>
  );
}
