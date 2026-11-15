// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE CHANNEL — a panel header's statement of the part of the fleet it shows

import { createContext, useContext, type JSX, type ReactNode } from "react";

import { useCockpitI18n } from "../context/CockpitI18n";
import type { MessageKey } from "../lib/i18n";
import { WHOLE_HUB, scopeText, type CockpitScope } from "../lib/scope";

/** The scope the cockpit's requests carry; the whole hub until one is chosen. */
export const ScopeContext = createContext<CockpitScope>(WHOLE_HUB);

/** Whether the cockpit shows a past state rebuilt from the log, which the scope does not narrow. */
export const ReplayContext = createContext<boolean>(false);

interface ScopeProviderProps {
  readonly scope: CockpitScope;
  readonly travelling: boolean;
  readonly children: ReactNode;
}

/** Give every panel beneath it the active scope and whether the cockpit is travelling in time. */
export function ScopeProvider({ scope, travelling, children }: ScopeProviderProps): JSX.Element {
  return (
    <ScopeContext.Provider value={scope}>
      <ReplayContext.Provider value={travelling}>{children}</ReplayContext.Provider>
    </ScopeContext.Provider>
  );
}

interface ScopeTagProps {
  /** Whether this panel's data takes the scope; a panel that does not shows the whole hub. */
  readonly scoped?: boolean;
  /** Whether the snapshot fills this panel; in a peer scope only such a panel shows the peer. */
  readonly snapshot?: boolean;
  /** Whether this panel shows the rebuilt past state while the cockpit travels in time. */
  readonly replayed?: boolean;
  /** A statement of what in the panel the scope narrows, when not all of it. */
  readonly note?: MessageKey;
}

/**
 * Name, in a panel's header, the part of the fleet the panel shows while a
 * scope is active. A panel whose feed the scope does not narrow, and a panel
 * that shows a past state rebuilt from the log, says that it shows the whole
 * hub, so no panel looks narrowed when it is not. In a peer scope, a panel the
 * snapshot fills says it is advisory and observed from that hub; every other
 * panel says it is not observed.
 */
export function ScopeTag({
  scoped = true,
  snapshot = false,
  replayed = false,
  note,
}: ScopeTagProps): JSX.Element | null {
  const scope = useContext(ScopeContext);
  const travelling = useContext(ReplayContext);
  const { t } = useCockpitI18n();
  if (scope.kind === "all") return null;
  if (scope.kind === "peer") {
    return snapshot ? (
      <span className="panel__sub panel__scope panel__scope--peer" title={t("scope.peerTitle")}>
        {t("scope.peerTag", { hub: scope.hub })}
      </span>
    ) : (
      <span className="panel__sub panel__scope panel__scope--whole" title={t("scope.peerFeedTitle")}>
        {t("scope.peerFeedTag")}
      </span>
    );
  }
  if (!scoped || (replayed && travelling)) {
    return (
      <span className="panel__sub panel__scope panel__scope--whole" title={t("scope.unscopedTitle")}>
        {t("scope.tag", { scope: t("scope.wholeHub") })}
      </span>
    );
  }
  return (
    <span className="panel__sub panel__scope" title={note === undefined ? undefined : t(note)}>
      {t("scope.tag", { scope: scopeText(scope) })}
    </span>
  );
}
