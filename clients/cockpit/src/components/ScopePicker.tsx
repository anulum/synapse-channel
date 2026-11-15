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
}

/**
 * Choose the part of the fleet the cockpit shows: the whole hub, the work
 * without a project, or one namespace, each with its task and agent counts.
 * The counts are the hub's, so a choice never reads as a bare zero. The
 * observed peer hubs follow under their own heading: each shows that hub's
 * folded state, advisory and read-only.
 */
export function ScopePicker({ scope, counts, onChange }: ScopePickerProps): JSX.Element {
  const { t } = useCockpitI18n();
  const label = (option: ScopeOption): string => {
    const values = { tasks: option.tasks, agents: option.agents };
    if (option.scope.kind === "all") return t("hud.scopeAll", values);
    if (option.scope.kind === "none") return t("hud.scopeNone", values);
    return t("hud.scopeProject", { ...values, namespace: option.scope.namespace });
  };
  const peers = peerScopes(counts, scope);
  const marked = scope.kind === "all" ? "" : scope.kind === "peer" ? " hud__scope--on hud__scope--peer" : " hud__scope--on";
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
        {peers.length > 0 && (
          <optgroup label={t("hud.scopePeers")}>
            {peers.map((peer) => (
              <option key={scopeText(peer)} value={scopeText(peer)}>
                {t("hud.scopePeer", { hub: peer.hub })}
              </option>
            ))}
          </optgroup>
        )}
      </select>
    </span>
  );
}
