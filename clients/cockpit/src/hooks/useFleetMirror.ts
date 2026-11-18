// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE CHANNEL — whether this principal may open the Fleet mirror's scope, and the mirror in it

import { useEffect, useState } from "react";

import { createEndpointFeed } from "../lib/feed";
import {
  MIRROR_ACCESS_POLL_MS,
  createMirrorFeed,
  parseMirrorAccess,
  type MirrorState,
} from "../lib/fleetMirror";

const CONNECTING: MirrorState = { mirror: null, status: "connecting", fetchedAt: null };

/**
 * Read /fleet-observed-access.json on each access revision and slowly after:
 * true only on a 200 that says observe and advisory. Any other answer, a 404
 * included, offers nothing and says nothing.
 */
export function useFleetMirrorAccess(blocked: boolean, revision: number): boolean {
  const [offered, setOffered] = useState(false);
  useEffect(() => {
    setOffered(false);
    if (blocked) return;
    const feed = createEndpointFeed({
      url: "/fleet-observed-access.json",
      pollMs: MIRROR_ACCESS_POLL_MS,
      parse: parseMirrorAccess,
    });
    const unsubscribe = feed.subscribe((state) => setOffered(state.status === "live" && state.data === true));
    return () => {
      unsubscribe();
      feed.stop();
    };
  }, [blocked, revision]);
  return offered;
}

/** Poll /fleet-observed.json alone while the Fleet scope is open; nothing otherwise. */
export function useFleetMirror(active: boolean, revision: number): MirrorState {
  const [state, setState] = useState<MirrorState>(CONNECTING);
  useEffect(() => {
    setState(CONNECTING);
    if (!active) return;
    const feed = createMirrorFeed();
    const unsubscribe = feed.subscribe(setState);
    return () => {
      unsubscribe();
      feed.stop();
    };
  }, [active, revision]);
  return state;
}
