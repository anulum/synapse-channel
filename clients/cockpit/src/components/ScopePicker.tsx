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
  FLEET_MIRROR,
  groupOptions,
  parseScope,
  peerScopes,
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
  /** Whether the dashboard offers this principal the Fleet mirror's scope. */
  readonly fleetOffered?: boolean;
}

/**
 * Choose the part of the fleet the cockpit shows: the whole hub, the work
 * without a project, or one namespace, each with its task and agent counts.
 * The counts are the hub's, so a choice never reads as a bare zero. The groups
 * the dashboard declares follow under their own heading, then the observed
 * peer hubs: each of those shows that hub's folded state, advisory and read-only.
 */
export function ScopePicker({ scope, counts, onChange, fleetOffered = false }: ScopePickerProps): JSX.Element {
  const { t } = useCockpitI18n();
  const label = (option: ScopeOption): string => {
    const values = { tasks: option.tasks, agents: option.agents };
    if (option.scope.kind === "all") return t("hud.scopeAll", values);
    if (option.scope.kind === "none") return t("hud.scopeNone", values);
    if (option.scope.kind === "group") return t("hud.scopeGroup", { ...values, group: option.scope.name });
    return t("hud.scopeProject", { ...values, namespace: option.scope.namespace });
  };
  const groups = groupOptions(counts, scope);
  const peers = peerScopes(counts, scope);
  const observed = scope.kind === "peer" || scope.kind === "fleet";
  const marked = scope.kind === "all" ? "" : observed ? " hud__scope--on hud__scope--peer" : " hud__scope--on";
  return (
    <span className={`hud__scope${marked}`} title={t("hud.scopeTitle")}>
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
        {groups.length > 0 && (
          <optgroup label={t("hud.scopeGroups")}>
            {groups.map((option) => (
              <option key={scopeText(option.scope)} value={scopeText(option.scope)}>
                {label(option)}
              </option>
            ))}
          </optgroup>
        )}
        {peers.length > 0 && (
          <optgroup label={t("hud.scopePeers")}>
            {peers.map((peer) => (
              <option key={scopeText(peer)} value={scopeText(peer)}>
                {t("hud.scopePeer", { hub: peer.hub })}
              </option>
            ))}
          </optgroup>
        )}
        {(fleetOffered || scope.kind === "fleet") && (
          <optgroup label={t("hud.scopeMirrorGroup")}>
            <option value={scopeText(FLEET_MIRROR)}>{t("hud.scopeFleet")}</option>
          </optgroup>
        )}
      </select>
    </span>
  );
}
