// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — lifecycle owner for the dashboard access descriptor

import { useEffect, useState } from "react";

import {
  fetchDashboardAccess,
  LOADING_DASHBOARD_ACCESS,
  type DashboardAccessState,
} from "../lib/access";

export const DASHBOARD_ACCESS_POLL_MS = 5_000;

interface ResolvedAccess {
  readonly revision: number;
  readonly state: DashboardAccessState;
}

/** Probe before exposing the shell, then replace presentation capabilities on every poll. */
export function useDashboardAccess(
  blocked: boolean,
  revision: number,
  pollMs = DASHBOARD_ACCESS_POLL_MS,
): DashboardAccessState {
  const [resolved, setResolved] = useState<ResolvedAccess>({
    revision: -1,
    state: LOADING_DASHBOARD_ACCESS,
  });

  useEffect(() => {
    if (blocked) return undefined;
    let active = true;
    let polling = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    setResolved({ revision, state: LOADING_DASHBOARD_ACCESS });

    const poll = async (): Promise<void> => {
      if (polling) return;
      polling = true;
      const state = await fetchDashboardAccess();
      polling = false;
      if (!active) return;
      setResolved({ revision, state });
      timer = setTimeout(() => void poll(), Math.max(10, pollMs));
    };
    const refresh = (): void => {
      if (timer !== undefined) clearTimeout(timer);
      timer = undefined;
      void poll();
    };
    window.addEventListener("focus", refresh);
    void poll();
    return () => {
      active = false;
      window.removeEventListener("focus", refresh);
      if (timer !== undefined) clearTimeout(timer);
    };
  }, [blocked, pollMs, revision]);

  if (blocked || resolved.revision !== revision) return LOADING_DASHBOARD_ACCESS;
  return resolved.state;
}
