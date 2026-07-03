// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — cockpit app shell

import { useEffect, useMemo, useRef, useState } from "react";
import { ActivitySpine } from "./components/ActivitySpine";
import { ClaimsBoard } from "./components/ClaimsBoard";
import { FindingsStream } from "./components/FindingsStream";
import { FleetRoster } from "./components/FleetRoster";
import { Hud, type Kpi } from "./components/Hud";
import { RiskRail } from "./components/RiskRail";
import { SignalLog } from "./components/SignalLog";
import { TaskBoard } from "./components/TaskBoard";
import { deriveBoard, deriveFindings } from "./lib/board";
import { deriveClaims, parseConflicts } from "./lib/claims";
import { deriveRoster } from "./lib/roster";
import {
  createSnapshotStore,
  withFreshness,
  type SnapshotState,
} from "./lib/snapshot";
import { createSnapshotEventSource, type TransitionEventSource } from "./lib/spineEvents";
import type { CockpitEvent } from "./types";

/** Wall-clock time-of-day stamp for the freshness contract. */
function stampFor(ms: number | null): string {
  if (ms === null) return "—";
  return new Date(ms).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

const INITIAL_SNAPSHOT: SnapshotState = {
  snapshot: null,
  status: "connecting",
  fetchedAt: null,
  error: null,
};

/** Most recent derived events held for the signal log. */
const LOG_LIMIT = 250;

interface Metrics {
  readonly agents: number;
  readonly claims: number;
  readonly risk: number;
}

const ZERO_METRICS: Metrics = { agents: 0, claims: 0, risk: 0 };

function metricsOf(state: SnapshotState): Metrics {
  const snapshot = state.snapshot;
  if (snapshot === null) return ZERO_METRICS;
  return {
    agents: snapshot.fleet.agents.live.length,
    claims: snapshot.fleet.claims.active,
    risk: snapshot.risk.signals.filter((signal) => signal.level === "red").length,
  };
}

export function App(): JSX.Element {
  const [snap, setSnap] = useState<SnapshotState>(INITIAL_SNAPSHOT);
  const [kpis, setKpis] = useState<readonly Kpi[]>([]);
  const [log, setLog] = useState<readonly CockpitEvent[]>([]);
  const [spineSource, setSpineSource] = useState<TransitionEventSource | undefined>(undefined);
  const [nowMs, setNowMs] = useState<number>(() => Date.now());
  const previous = useRef<Metrics>(ZERO_METRICS);

  useEffect(() => {
    // The store owns its polling and is created per-mount so its lifecycle is
    // tied to the effect: a clean start on mount, a full stop on unmount. The
    // event source diffs consecutive fetches into the real transitions that
    // feed both the spine and the signal log.
    const store = createSnapshotStore();
    const source = createSnapshotEventSource(store);
    setSpineSource(source);
    const unsubscribeEvents = source.subscribe((event) => {
      setLog((current) => [event, ...current].slice(0, LOG_LIMIT));
    });
    const unsubscribeSnapshots = store.subscribe(setSnap);
    // Re-evaluate freshness between polls so the beacon flips to `stale` even
    // while the hub is silent, without waiting for the next fetch to return.
    // The same tick drives the lease countdowns on the claims board.
    const clock = setInterval(() => {
      const tick = Date.now();
      setNowMs(tick);
      setSnap((current) => withFreshness(current, tick));
    }, 1000);
    return () => {
      unsubscribeEvents();
      unsubscribeSnapshots();
      clearInterval(clock);
      source.stop();
      store.stop();
    };
  }, []);

  useEffect(() => {
    const metrics = metricsOf(snap);
    const prior = previous.current;
    previous.current = metrics;
    setKpis([
      { label: "agents online", value: metrics.agents, delta: metrics.agents - prior.agents },
      { label: "claims held", value: metrics.claims, delta: metrics.claims - prior.claims },
      { label: "risk signals", value: metrics.risk, delta: metrics.risk - prior.risk },
    ]);
  }, [snap]);

  const roster = useMemo(() => deriveRoster(snap.snapshot), [snap.snapshot]);
  const waiters = snap.snapshot?.fleet.agents.waiters.length ?? 0;
  const claims = useMemo(() => deriveClaims(snap.snapshot, nowMs), [snap.snapshot, nowMs]);
  const conflicts = useMemo(
    () => (snap.snapshot === null ? [] : parseConflicts(snap.snapshot)),
    [snap.snapshot],
  );
  const board = useMemo(() => deriveBoard(snap.snapshot), [snap.snapshot]);
  const findings = useMemo(() => deriveFindings(snap.snapshot), [snap.snapshot]);
  const connected = snap.snapshot !== null;

  return (
    <div className="shell">
      <Hud kpis={kpis} live={snap.status === "live"} stamp={stampFor(snap.fetchedAt)} />
      <ActivitySpine source={spineSource} />
      <div className="deck">
        <FleetRoster roster={roster} waiters={waiters} />
        <div className="deck__stack">
          <ClaimsBoard claims={claims} conflicts={conflicts} connected={connected} />
          <SignalLog events={log} />
        </div>
        <TaskBoard tasks={board} connected={connected} />
        <div className="deck__stack deck__stack--rail">
          <RiskRail risk={snap.snapshot?.risk ?? null} />
          <FindingsStream findings={findings} connected={connected} />
        </div>
      </div>
    </div>
  );
}
