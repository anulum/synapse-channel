// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE CHANNEL — the cockpit's scope, held in the URL beside the workspace

import { useCallback, useEffect, useState } from "react";

import { scopeFromSearch, scopeToSearch, type CockpitScope } from "../lib/scope";

export interface CockpitScopeController {
  readonly scope: CockpitScope;
  readonly setScope: (scope: CockpitScope) => void;
}

function currentScope(): CockpitScope {
  return scopeFromSearch(typeof location === "undefined" ? "" : location.search);
}

/**
 * Hold the scope in `?scope=`, so a reload or a shared link opens the same
 * part of the fleet. A change is one history entry; Back restores the scope
 * before it. Every other key of the URL is kept.
 */
export function useCockpitScope(): CockpitScopeController {
  const [scope, setState] = useState<CockpitScope>(currentScope);

  useEffect(() => {
    const restore = (): void => setState(currentScope());
    window.addEventListener("popstate", restore);
    return () => window.removeEventListener("popstate", restore);
  }, []);

  const setScope = useCallback((next: CockpitScope) => {
    const search = scopeToSearch(next, location.search);
    if (search === location.search) return;
    history.pushState(history.state, "", `${location.pathname}${search}${location.hash}`);
    setState(next);
  }, []);

  return { scope, setScope };
}
