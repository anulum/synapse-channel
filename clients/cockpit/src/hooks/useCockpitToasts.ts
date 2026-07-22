// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — live fleet transition-toast lifecycle

import { useCallback, useEffect, useRef, useState } from "react";

import type { BoardTask } from "../lib/board";
import type { BranchConflictView } from "../lib/claims";
import type { DeadLetterView } from "../lib/deadLetters";
import { factsOf, toastsBetween, type FleetFacts, type Toast } from "../lib/toasts";
import type { FleetSnapshot } from "../types";

export interface CockpitToastController {
  readonly toasts: readonly Toast[];
  readonly dismiss: (id: string) => void;
}

interface ToastControllerOptions {
  readonly blocked: boolean;
  readonly snapshot: FleetSnapshot | null;
  readonly board: readonly BoardTask[];
  readonly conflicts: readonly BranchConflictView[];
  readonly deadLetters: readonly DeadLetterView[];
}

/** Announce live state transitions once and clear every timer when the shell locks. */
export function useCockpitToasts({
  blocked,
  snapshot,
  board,
  conflicts,
  deadLetters,
}: ToastControllerOptions): CockpitToastController {
  const [toasts, setToasts] = useState<readonly Toast[]>([]);
  const previousFacts = useRef<FleetFacts | null>(null);
  const timers = useRef<Set<ReturnType<typeof setTimeout>>>(new Set());

  const dismiss = useCallback((id: string) => {
    setToasts((current) => current.filter((toast) => toast.id !== id));
  }, []);

  useEffect(() => {
    const activeTimers = timers.current;
    return () => {
      for (const timer of activeTimers) clearTimeout(timer);
    };
  }, []);

  useEffect(() => {
    if (blocked) {
      for (const timer of timers.current) clearTimeout(timer);
      timers.current.clear();
      previousFacts.current = null;
      setToasts([]);
      return;
    }
    if (snapshot === null) return;

    const facts = factsOf(board, conflicts, deadLetters, snapshot.risk, snapshot.config_epoch);
    const fresh = toastsBetween(previousFacts.current, facts);
    previousFacts.current = facts;
    if (fresh.length === 0) return;
    setToasts((current) => {
      const seen = new Set(current.map((toast) => toast.id));
      return [...current, ...fresh.filter((toast) => !seen.has(toast.id))];
    });
    const ids = fresh.map((toast) => toast.id);
    const timer = setTimeout(() => {
      timers.current.delete(timer);
      setToasts((current) => current.filter((toast) => !ids.includes(toast.id)));
    }, 8_000);
    timers.current.add(timer);
  }, [blocked, board, conflicts, deadLetters, snapshot]);

  return { toasts, dismiss };
}
