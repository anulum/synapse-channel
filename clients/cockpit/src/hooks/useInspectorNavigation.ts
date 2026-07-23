// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — inspector focus and trace-hop lifecycle

import type { KeyboardEvent, RefObject } from "react";
import { useCallback, useEffect, useRef, useState } from "react";

import type { CausalityPrefill } from "../components/CausalityView";
import {
  INSPECTOR_TABS,
  type CockpitSelection,
  type InspectorTab,
} from "../lib/workspace";

interface InspectorNavigationOptions {
  readonly onTabChange: (tab: InspectorTab) => void;
  readonly onSelectionChange?: ((selection: CockpitSelection | null) => void) | undefined;
  readonly traceRequest?: { readonly subject: string; readonly nonce: number } | undefined;
}

export interface InspectorNavigation {
  readonly prefill: CausalityPrefill | null;
  readonly tabRefs: RefObject<Array<HTMLButtonElement | null>>;
  readonly onSelectTask: (taskId: string) => void;
  readonly onTabKeyDown: (event: KeyboardEvent<HTMLButtonElement>, index: number) => void;
}

/** Own roving tab focus and task-to-causality trace navigation. */
export function useInspectorNavigation({
  onTabChange,
  onSelectionChange,
  traceRequest,
}: InspectorNavigationOptions): InspectorNavigation {
  const [prefill, setPrefill] = useState<CausalityPrefill | null>(null);
  const tabRefs = useRef<Array<HTMLButtonElement | null>>([]);

  const onSelectTask = useCallback((taskId: string): void => {
    onSelectionChange?.({ kind: "task", id: taskId });
    setPrefill((current) => ({ subject: taskId, nonce: (current?.nonce ?? 0) + 1 }));
    onTabChange("causality");
  }, [onSelectionChange, onTabChange]);

  const onTabKeyDown = useCallback((event: KeyboardEvent<HTMLButtonElement>, index: number): void => {
    let nextIndex: number | null = null;
    if (event.key === "ArrowRight") nextIndex = (index + 1) % INSPECTOR_TABS.length;
    else if (event.key === "ArrowLeft") {
      nextIndex = (index - 1 + INSPECTOR_TABS.length) % INSPECTOR_TABS.length;
    } else if (event.key === "Home") nextIndex = 0;
    else if (event.key === "End") nextIndex = INSPECTOR_TABS.length - 1;
    if (nextIndex === null) return;
    event.preventDefault();
    const nextTab = INSPECTOR_TABS[nextIndex] as InspectorTab;
    onTabChange(nextTab);
    tabRefs.current[nextIndex]?.focus();
  }, [onTabChange]);

  useEffect(() => {
    if (traceRequest !== undefined) onSelectTask(traceRequest.subject);
  }, [traceRequest, onSelectTask]);

  return { prefill, tabRefs, onSelectTask, onTabKeyDown };
}
