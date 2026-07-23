// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — signal-log workspace modes and their lifecycle

import { useEffect, useRef, useState } from "react";

import { actorsInWindow, eventsInWindow, type TimeWindow } from "../lib/brush";
import {
  fetchHistoryWindow,
  fetchLatestSeq,
  type HistoryWindow,
} from "../lib/history";
import { applyQuery, type LogQuery } from "../lib/logQuery";
import { readLogExportFile, type PostMortem } from "../lib/postmortem";
import type { CockpitEvent } from "../types";

interface SignalLogWorkspaceOptions {
  readonly events: readonly CockpitEvent[];
  readonly window: TimeWindow | null;
  readonly query: LogQuery;
  readonly provenance: "hub" | "derived";
}

export interface LoadedPostMortem {
  readonly data: PostMortem;
  readonly name: string;
}

/** State and commands for the signal log's live, history, and file modes. */
export interface SignalLogWorkspace {
  readonly paused: boolean;
  readonly newerCount: number;
  readonly historyOn: boolean;
  readonly historyLatest: number;
  readonly historyPos: number;
  readonly historyWindow: HistoryWindow | null;
  readonly historyNote: string | null;
  readonly pinnedWindow: HistoryWindow | null;
  readonly diffOpen: boolean;
  readonly postMortem: LoadedPostMortem | null;
  readonly postMortemNote: string | null;
  readonly shown: readonly CockpitEvent[];
  readonly actors: readonly string[];
  readonly shownProvenance: "hub" | "derived";
  readonly togglePause: () => void;
  readonly enterHistory: () => Promise<void>;
  readonly leaveHistory: () => void;
  readonly scrubTo: (position: number) => void;
  readonly togglePinnedWindow: () => void;
  readonly toggleDiff: () => void;
  readonly openExportFile: (file: File) => Promise<void>;
  readonly closePostMortem: () => void;
}

function historyMessage(
  result: { readonly kind: "absent" } | { readonly kind: "error"; readonly message: string },
): string {
  return result.kind === "absent" ? "event feed not served" : result.message;
}

/**
 * Own the mutually exclusive signal-log workspaces. The transport keeps
 * recording while the live view is frozen; file mode outranks history, and
 * history outranks the live brush when choosing the shown evidence.
 */
export function useSignalLogWorkspace({
  events,
  window,
  query,
  provenance,
}: SignalLogWorkspaceOptions): SignalLogWorkspace {
  const [paused, setPaused] = useState(false);
  const frozen = useRef<readonly CockpitEvent[]>([]);

  const [historyOn, setHistoryOn] = useState(false);
  const [historyLatest, setHistoryLatest] = useState(0);
  const [historyPos, setHistoryPos] = useState(0);
  const [historyWindow, setHistoryWindow] = useState<HistoryWindow | null>(null);
  const [historyNote, setHistoryNote] = useState<string | null>(null);
  const scrubTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const [pinnedWindow, setPinnedWindow] = useState<HistoryWindow | null>(null);
  const [diffOpen, setDiffOpen] = useState(false);

  const [postMortem, setPostMortem] = useState<LoadedPostMortem | null>(null);
  const [postMortemNote, setPostMortemNote] = useState<string | null>(null);

  useEffect(
    () => () => {
      if (scrubTimer.current !== undefined) clearTimeout(scrubTimer.current);
    },
    [],
  );

  const leaveHistory = (): void => {
    if (scrubTimer.current !== undefined) clearTimeout(scrubTimer.current);
    scrubTimer.current = undefined;
    setHistoryOn(false);
    setHistoryWindow(null);
    setHistoryNote(null);
    setPinnedWindow(null);
    setDiffOpen(false);
  };

  const enterHistory = async (): Promise<void> => {
    const latest = await fetchLatestSeq();
    if (latest.kind !== "loaded") {
      setHistoryNote(historyMessage(latest));
      return;
    }
    setHistoryLatest(latest.latest);
    setHistoryPos(latest.latest);
    setHistoryNote(null);
    setHistoryOn(true);
    const result = await fetchHistoryWindow(latest.latest);
    if (result.kind === "loaded") setHistoryWindow(result.window);
    else setHistoryNote(historyMessage(result));
  };

  const scrubTo = (position: number): void => {
    setHistoryPos(position);
    if (scrubTimer.current !== undefined) clearTimeout(scrubTimer.current);
    scrubTimer.current = setTimeout(() => {
      scrubTimer.current = undefined;
      void fetchHistoryWindow(position).then((result) => {
        if (result.kind === "loaded") {
          setHistoryWindow(result.window);
          setHistoryNote(null);
        } else {
          setHistoryNote(historyMessage(result));
        }
      });
    }, 250);
  };

  const togglePause = (): void => {
    if (!paused) frozen.current = events;
    setPaused(!paused);
  };

  const togglePinnedWindow = (): void => {
    if (pinnedWindow !== null) {
      setPinnedWindow(null);
      setDiffOpen(false);
    } else if (historyWindow !== null) {
      setPinnedWindow(historyWindow);
    }
  };

  const toggleDiff = (): void => setDiffOpen(!diffOpen);

  const openExportFile = async (file: File): Promise<void> => {
    const parsed = await readLogExportFile(file);
    if (parsed === null) {
      setPostMortemNote(`${file.name} is not a cockpit export`);
      return;
    }
    setPostMortemNote(null);
    setPostMortem({ data: parsed, name: file.name });
    if (historyOn) leaveHistory();
  };

  const closePostMortem = (): void => {
    setPostMortem(null);
    setPostMortemNote(null);
  };

  const base = paused ? frozen.current : events;
  let newerCount = 0;
  if (paused) {
    const frozenHead = frozen.current[0]?.seq;
    if (frozenHead === undefined) newerCount = events.length;
    else {
      const headAt = events.findIndex((event) => event.seq === frozenHead);
      newerCount = headAt === -1 ? events.length : headAt;
    }
  }

  const shown = postMortem
    ? applyQuery(postMortem.data.events, query)
    : historyOn
      ? applyQuery(historyWindow?.events ?? [], query)
      : applyQuery(eventsInWindow(base, window), query);
  const actors = window === null || historyOn || postMortem !== null ? [] : actorsInWindow(base, window);
  const shownProvenance = postMortem?.data.provenance ?? provenance;

  return {
    paused,
    newerCount,
    historyOn,
    historyLatest,
    historyPos,
    historyWindow,
    historyNote,
    pinnedWindow,
    diffOpen,
    postMortem,
    postMortemNote,
    shown,
    actors,
    shownProvenance,
    togglePause,
    enterHistory,
    leaveHistory,
    scrubTo,
    togglePinnedWindow,
    toggleDiff,
    openExportFile,
    closePostMortem,
  };
}
