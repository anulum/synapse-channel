// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — read the capability summary only while someone looks at it

import { useEffect, useState } from "react";

import { createServedSurfacesStore, type ServedSurfacesState } from "../lib/servedSurfaces";

const CONNECTING: ServedSurfacesState = Object.freeze({
  data: null,
  status: "connecting",
  fetchedAt: null,
  error: null,
});

/**
 * Read `/capabilities.json` while `active` (the setup assistant is open and
 * access is settled), and stop when it closes. A new credential revision
 * reads it again with the new bearer.
 */
export function useServedSurfaces(active: boolean, credentialRevision: number): ServedSurfacesState {
  const [state, setState] = useState<ServedSurfacesState>(CONNECTING);

  useEffect(() => {
    setState(CONNECTING);
    if (!active) return;
    const store = createServedSurfacesStore();
    const unsubscribe = store.subscribe(setState);
    return () => {
      unsubscribe();
      store.stop();
    };
  }, [active, credentialRevision]);

  return state;
}
