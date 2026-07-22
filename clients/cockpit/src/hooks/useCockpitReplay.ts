// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — replay reconstruction request lifecycle

import { useCallback, useEffect, useRef, useState } from "react";

import { fetchStateAt } from "../lib/stateAt";
import type { ReplayState } from "../lib/workspace";
import type { ReplaySlot } from "../components/ReplayWorkbench";

export interface CockpitReplayController {
  readonly slotA: ReplaySlot | null;
  readonly slotB: ReplaySlot | null;
  readonly travelling: boolean;
  readonly toggleTravel: () => void;
}

interface ReplayControllerOptions {
  readonly blocked: boolean;
  readonly replay: ReplayState;
  readonly maximumSequence: number | null;
  readonly setReplay: (replay: ReplayState) => void;
}

async function loadReplaySlot(sequence: number): Promise<ReplaySlot> {
  const result = await fetchStateAt(sequence);
  if (result.kind === "loaded") return { seq: sequence, state: result.state, note: null };
  return {
    seq: sequence,
    state: null,
    note: result.kind === "absent" ? "state-at surface not served (--feeds-db)" : result.message,
  };
}

/** Load the URL-selected reconstruction and discard responses from stale generations. */
export function useCockpitReplay({
  blocked,
  replay,
  maximumSequence,
  setReplay,
}: ReplayControllerOptions): CockpitReplayController {
  const [slotA, setSlotA] = useState<ReplaySlot | null>(null);
  const [slotB, setSlotB] = useState<ReplaySlot | null>(null);
  const generation = useRef(0);

  useEffect(() => {
    const currentGeneration = generation.current + 1;
    generation.current = currentGeneration;
    if (blocked || replay.mode === "live") {
      setSlotA(null);
      setSlotB(null);
      return undefined;
    }

    const sequenceA = replay.mode === "compare" ? replay.a : null;
    const sequenceB = replay.mode === "compare" ? replay.b : replay.at;
    setSlotA(sequenceA === null ? null : { seq: sequenceA, state: null, note: null });
    setSlotB({ seq: sequenceB, state: null, note: null });
    const timer = setTimeout(() => {
      const requestA = sequenceA === null ? Promise.resolve(null) : loadReplaySlot(sequenceA);
      void Promise.all([requestA, loadReplaySlot(sequenceB)]).then(([loadedA, loadedB]) => {
        if (generation.current !== currentGeneration) return;
        setSlotA(loadedA);
        setSlotB(loadedB);
      });
    }, 250);
    return () => clearTimeout(timer);
  }, [blocked, replay]);

  const toggleTravel = useCallback(() => {
    setReplay(
      replay.mode === "live"
        ? { mode: "history", at: maximumSequence ?? 0 }
        : { mode: "live" },
    );
  }, [maximumSequence, replay.mode, setReplay]);

  return {
    slotA,
    slotB,
    travelling: replay.mode !== "live" && slotB?.state !== null && slotB?.state !== undefined,
    toggleTravel,
  };
}
