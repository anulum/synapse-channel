// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — deterministic live and replay cockpit projections

import { useMemo } from "react";

import type { ReplaySlot } from "../components/ReplayWorkbench";
import type { CockpitFeeds } from "./useCockpitFeeds";
import type { AttentionItem } from "../lib/attention";
import { deriveAttentionQueue } from "../lib/attention";
import type { PendingApprovalView } from "../lib/approvals";
import { parsePendingApprovals } from "../lib/approvals";
import type { BoardTask, FindingNote } from "../lib/board";
import { deriveBoard, deriveFindings } from "../lib/board";
import type { TimeWindow } from "../lib/brush";
import type { BranchConflictView, ClaimView } from "../lib/claims";
import { deriveClaims, parseConflicts } from "../lib/claims";
import type { CommunicationModel } from "../lib/communications";
import { deriveCommunicationModel } from "../lib/communications";
import type { DeadLetterView } from "../lib/deadLetters";
import { parseDeadLetters } from "../lib/deadLetters";
import { focusClaims, focusTasks } from "../lib/focus";
import type { AnomalyFlag } from "../lib/anomalies";
import { deriveAnomalies } from "../lib/anomalies";
import type { RosterEntry } from "../lib/roster";
import { deriveRoster } from "../lib/roster";

export interface CockpitViewModel {
  readonly roster: readonly RosterEntry[];
  readonly waiters: number;
  readonly liveClaims: readonly ClaimView[];
  readonly liveConflicts: readonly BranchConflictView[];
  readonly liveBoard: readonly BoardTask[];
  readonly claims: readonly ClaimView[];
  readonly conflicts: readonly BranchConflictView[];
  readonly board: readonly BoardTask[];
  readonly lensedClaims: readonly ClaimView[];
  readonly lensedBoard: readonly BoardTask[];
  readonly findings: readonly FindingNote[];
  readonly anomalies: readonly AnomalyFlag[];
  readonly deadLetters: readonly DeadLetterView[];
  readonly approvals: readonly PendingApprovalView[];
  readonly communication: CommunicationModel;
  readonly attention: readonly AttentionItem[];
  readonly connected: boolean;
}

interface ViewModelOptions {
  readonly feeds: CockpitFeeds;
  readonly replaySlot: ReplaySlot | null;
  readonly travelling: boolean;
  readonly focus: string;
  readonly brush: TimeWindow | null;
}

/** Project one immutable panel model from the live feeds and optional replay cut. */
export function useCockpitViewModel({
  feeds,
  replaySlot,
  travelling,
  focus,
  brush,
}: ViewModelOptions): CockpitViewModel {
  const snapshot = feeds.snap.snapshot;
  const roster = useMemo(() => deriveRoster(snapshot), [snapshot]);
  const liveClaims = useMemo(() => deriveClaims(snapshot, feeds.nowMs), [snapshot, feeds.nowMs]);
  const liveConflicts = useMemo(
    () => (snapshot === null ? [] : parseConflicts(snapshot)),
    [snapshot],
  );
  const liveBoard = useMemo(() => deriveBoard(snapshot), [snapshot]);
  const claims = travelling && replaySlot?.state !== null && replaySlot?.state !== undefined
    ? replaySlot.state.claims
    : liveClaims;
  const conflicts = travelling ? [] : liveConflicts;
  const board = travelling && replaySlot?.state !== null && replaySlot?.state !== undefined
    ? replaySlot.state.tasks
    : liveBoard;
  const lensedClaims = useMemo(() => focusClaims(claims, focus), [claims, focus]);
  const lensedBoard = useMemo(() => focusTasks(board, claims, focus), [board, claims, focus]);
  const findings = useMemo(() => deriveFindings(snapshot), [snapshot]);
  const anomalies = useMemo(() => deriveAnomalies(feeds.log), [feeds.log]);
  const deadLetters = useMemo(() => parseDeadLetters(snapshot), [snapshot]);
  const approvals = useMemo(() => parsePendingApprovals(snapshot), [snapshot]);
  const communication = useMemo(
    () => deriveCommunicationModel(feeds.log, liveClaims, roster.map((entry) => entry.agent), brush),
    [feeds.log, liveClaims, roster, brush],
  );
  const attention = useMemo(
    () =>
      deriveAttentionQueue({
        conflicts: liveConflicts,
        deadLetters,
        communication,
        claims: liveClaims,
        missingWaiters: snapshot?.fleet.agents.missing_waiters ?? [],
        board: liveBoard,
        approvals,
        waits: feeds.waits.data?.waits ?? [],
      }),
    [liveConflicts, deadLetters, communication, liveClaims, snapshot, liveBoard, approvals, feeds.waits.data],
  );

  return {
    roster,
    waiters: snapshot?.fleet.agents.waiters.length ?? 0,
    liveClaims,
    liveConflicts,
    liveBoard,
    claims,
    conflicts,
    board,
    lensedClaims,
    lensedBoard,
    findings,
    anomalies,
    deadLetters,
    approvals,
    communication,
    attention,
    connected: snapshot !== null,
  };
}
