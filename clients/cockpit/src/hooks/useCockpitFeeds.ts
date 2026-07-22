// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — lifecycle owner for the cockpit's authenticated live feeds

import { useEffect, useRef, useState } from "react";

import type { Kpi } from "../components/Hud";
import {
  createOperatorActionsStore,
  createReceiptsStore,
  type OperatorActionsState,
  type ReceiptsState,
} from "../lib/auditFeeds";
import { createEventsTailSource, type SpineProvenance } from "../lib/eventsTail";
import {
  EVENT_RETENTION_LIMIT,
  eventCoverageOf,
  type EventCoverage,
} from "../lib/eventCoverage";
import { createFederationStore, type FederationState } from "../lib/federation";
import {
  createHealthAnomaliesStore,
  type HealthAnomaliesState,
} from "../lib/healthAnomalies";
import { createMetricsStore, type MetricsState } from "../lib/metrics";
import { createReliabilityStore, type ReliabilityState } from "../lib/reliability";
import { createSessionsStore, type SessionsState } from "../lib/sessions";
import {
  createSnapshotStore,
  withFreshness,
  type SnapshotState,
} from "../lib/snapshot";
import { createSnapshotEventSource } from "../lib/spineEvents";
import { createWaitsStore, type WaitsState } from "../lib/waits";
import type { CockpitEvent, EventSource } from "../types";

const INITIAL_SNAPSHOT: SnapshotState = {
  snapshot: null,
  status: "connecting",
  fetchedAt: null,
  error: null,
};
const INITIAL_RELIABILITY: ReliabilityState = {
  data: null,
  status: "connecting",
  fetchedAt: null,
  error: null,
};
const INITIAL_FEDERATION: FederationState = {
  data: null,
  status: "connecting",
  fetchedAt: null,
  error: null,
};
const INITIAL_METRICS: MetricsState = {
  data: null,
  status: "connecting",
  fetchedAt: null,
  error: null,
};
const INITIAL_SESSIONS: SessionsState = {
  data: null,
  status: "connecting",
  fetchedAt: null,
  error: null,
};
const INITIAL_WAITS: WaitsState = {
  data: null,
  status: "connecting",
  fetchedAt: null,
  error: null,
};
const INITIAL_ANOMALIES: HealthAnomaliesState = {
  data: null,
  status: "connecting",
  fetchedAt: null,
  error: null,
};
const INITIAL_RECEIPTS: ReceiptsState = {
  data: null,
  status: "connecting",
  fetchedAt: null,
  error: null,
};
const INITIAL_OPERATOR_ACTIONS: OperatorActionsState = {
  data: null,
  status: "connecting",
  fetchedAt: null,
  error: null,
};

/** Maximum wait before secondary reports start when the primary event tail hangs. */
export const AUXILIARY_FEED_START_FALLBACK_MS = 20_000;

/** Maximum stagger before the second whole-log report starts on a slow store. */
export const HEAVY_FEED_STAGGER_FALLBACK_MS = 45_000;

interface HeadlineMetrics {
  readonly agents: number;
  readonly claims: number;
  readonly risk: number;
  readonly ratePerMinute: number;
}

const ZERO_METRICS: HeadlineMetrics = { agents: 0, claims: 0, risk: 0, ratePerMinute: 0 };

function metricsOf(state: SnapshotState, ratePerMinute: number): HeadlineMetrics {
  const snapshot = state.snapshot;
  if (snapshot === null) return { ...ZERO_METRICS, ratePerMinute };
  return {
    agents: snapshot.fleet.agents.live.length,
    claims: snapshot.fleet.claims.active,
    risk: snapshot.risk.signals.filter((signal) => signal.level === "red").length,
    ratePerMinute,
  };
}

function observedPerMinute(log: readonly CockpitEvent[], nowMs: number): number {
  const since = nowMs / 1000 - 60;
  let count = 0;
  for (const event of log) {
    if (event.ts < since) break;
    count += 1;
  }
  return count;
}

function stampFor(ms: number | null): string {
  if (ms === null) return "—";
  return new Date(ms).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

/** Auth-bound live state consumed by the app shell. */
export interface CockpitFeeds {
  readonly snap: SnapshotState;
  readonly stamp: string;
  readonly kpis: readonly Kpi[];
  readonly log: readonly CockpitEvent[];
  readonly spineSource: EventSource | undefined;
  readonly provenance: SpineProvenance;
  readonly coverage: EventCoverage;
  readonly nowMs: number;
  readonly reliability: ReliabilityState;
  readonly federation: FederationState;
  readonly metrics: MetricsState;
  readonly sessions: SessionsState;
  readonly waits: WaitsState;
  readonly anomalyReport: HealthAnomaliesState;
  readonly receipts: ReceiptsState;
  readonly operatorActions: OperatorActionsState;
}

/**
 * Own every polling surface for one credential revision.
 *
 * Locking stops all requests and clears every last-good value. Unlocking creates
 * a fresh generation, preventing a late response from the rejected credential
 * from repopulating the presentation.
 */
export function useCockpitFeeds(blocked: boolean, credentialRevision: number): CockpitFeeds {
  const [snap, setSnap] = useState<SnapshotState>(INITIAL_SNAPSHOT);
  const [kpis, setKpis] = useState<readonly Kpi[]>([]);
  const [log, setLog] = useState<readonly CockpitEvent[]>([]);
  const [spineSource, setSpineSource] = useState<EventSource | undefined>(undefined);
  const [provenance, setProvenance] = useState<SpineProvenance>("connecting");
  const [nowMs, setNowMs] = useState<number>(() => Date.now());
  const [reliability, setReliability] = useState<ReliabilityState>(INITIAL_RELIABILITY);
  const [federation, setFederation] = useState<FederationState>(INITIAL_FEDERATION);
  const [metrics, setMetrics] = useState<MetricsState>(INITIAL_METRICS);
  const [sessions, setSessions] = useState<SessionsState>(INITIAL_SESSIONS);
  const [waits, setWaits] = useState<WaitsState>(INITIAL_WAITS);
  const [anomalyReport, setAnomalyReport] = useState<HealthAnomaliesState>(INITIAL_ANOMALIES);
  const [receipts, setReceipts] = useState<ReceiptsState>(INITIAL_RECEIPTS);
  const [operatorActions, setOperatorActions] =
    useState<OperatorActionsState>(INITIAL_OPERATOR_ACTIONS);
  const previous = useRef<HeadlineMetrics>(ZERO_METRICS);

  useEffect(() => {
    setSnap(INITIAL_SNAPSHOT);
    setKpis([]);
    setLog([]);
    setSpineSource(undefined);
    setProvenance("connecting");
    setReliability(INITIAL_RELIABILITY);
    setFederation(INITIAL_FEDERATION);
    setMetrics(INITIAL_METRICS);
    setSessions(INITIAL_SESSIONS);
    setWaits(INITIAL_WAITS);
    setAnomalyReport(INITIAL_ANOMALIES);
    setReceipts(INITIAL_RECEIPTS);
    setOperatorActions(INITIAL_OPERATOR_ACTIONS);
    previous.current = ZERO_METRICS;
    if (blocked) return;

    const store = createSnapshotStore();
    const derived = createSnapshotEventSource(store);
    const tail = createEventsTailSource();
    const routed = new Set<(event: CockpitEvent) => void>();
    setSpineSource({
      subscribe(listener) {
        routed.add(listener);
        return () => routed.delete(listener);
      },
      stop() {
        // The effect cleanup owns the two concrete sources.
      },
    });
    let active: "tail" | "derived" | null = null;
    const push = (event: CockpitEvent): void => {
      setLog((current) => [event, ...current].slice(0, EVENT_RETENTION_LIMIT));
      for (const listener of routed) listener(event);
    };
    const unsubscribeTail = tail.subscribe((event) => {
      if (active === "tail") push(event);
    });
    const unsubscribeDerived = derived.subscribe((event) => {
      if (active === "derived") push(event);
    });
    let effectActive = true;
    let stopAuxiliaryFeeds: (() => void) | undefined;
    let auxiliaryFallback: ReturnType<typeof setTimeout> | undefined;
    const startAuxiliaryFeeds = (): void => {
      if (!effectActive || stopAuxiliaryFeeds !== undefined) return;
      if (auxiliaryFallback !== undefined) {
        clearTimeout(auxiliaryFallback);
        auxiliaryFallback = undefined;
      }
      const reliabilityStore = createReliabilityStore();
      const federationStore = createFederationStore();
      const unsubscribeFederation = federationStore.subscribe(setFederation);
      const metricsStore = createMetricsStore();
      const unsubscribeMetrics = metricsStore.subscribe(setMetrics);
      const sessionsStore = createSessionsStore();
      const unsubscribeSessions = sessionsStore.subscribe(setSessions);
      const waitsStore = createWaitsStore();
      const unsubscribeWaits = waitsStore.subscribe(setWaits);
      let anomaliesStore: ReturnType<typeof createHealthAnomaliesStore> | undefined;
      let unsubscribeAnomalies: (() => void) | undefined;
      let anomalyFallback: ReturnType<typeof setTimeout> | undefined;
      const startAnomalies = (): void => {
        if (!effectActive || anomaliesStore !== undefined) return;
        if (anomalyFallback !== undefined) clearTimeout(anomalyFallback);
        anomaliesStore = createHealthAnomaliesStore();
        unsubscribeAnomalies = anomaliesStore.subscribe(setAnomalyReport);
      };
      const unsubscribeReliability = reliabilityStore.subscribe((state) => {
        setReliability(state);
        if (state.status !== "connecting") startAnomalies();
      });
      anomalyFallback = setTimeout(startAnomalies, HEAVY_FEED_STAGGER_FALLBACK_MS);
      const receiptsStore = createReceiptsStore();
      const unsubscribeReceipts = receiptsStore.subscribe(setReceipts);
      const operatorActionsStore = createOperatorActionsStore();
      const unsubscribeOperatorActions = operatorActionsStore.subscribe(setOperatorActions);
      stopAuxiliaryFeeds = () => {
        unsubscribeReliability();
        unsubscribeFederation();
        unsubscribeMetrics();
        unsubscribeSessions();
        unsubscribeWaits();
        if (anomalyFallback !== undefined) clearTimeout(anomalyFallback);
        unsubscribeAnomalies?.();
        unsubscribeReceipts();
        unsubscribeOperatorActions();
        reliabilityStore.stop();
        federationStore.stop();
        metricsStore.stop();
        sessionsStore.stop();
        waitsStore.stop();
        anomaliesStore?.stop();
        receiptsStore.stop();
        operatorActionsStore.stop();
      };
    };
    auxiliaryFallback = setTimeout(startAuxiliaryFeeds, AUXILIARY_FEED_START_FALLBACK_MS);
    const unsubscribeMode = tail.subscribeMode((mode) => {
      setProvenance(mode);
      const next = mode === "hub" ? "tail" : mode === "connecting" ? active : "derived";
      if (next !== active) {
        active = next;
        setLog([]);
      }
      if (mode !== "connecting") startAuxiliaryFeeds();
    });
    const unsubscribeSnapshots = store.subscribe(setSnap);
    const clock = setInterval(() => {
      const tick = Date.now();
      setNowMs(tick);
      setSnap((current) => withFreshness(current, tick));
    }, 1000);

    return () => {
      effectActive = false;
      if (auxiliaryFallback !== undefined) clearTimeout(auxiliaryFallback);
      unsubscribeTail();
      unsubscribeDerived();
      unsubscribeMode();
      unsubscribeSnapshots();
      stopAuxiliaryFeeds?.();
      clearInterval(clock);
      tail.stop();
      derived.stop();
      store.stop();
    };
  }, [blocked, credentialRevision]);

  useEffect(() => {
    const next = metricsOf(snap, observedPerMinute(log, nowMs));
    const prior = previous.current;
    previous.current = next;
    setKpis([
      { label: "agents online", value: next.agents, delta: next.agents - prior.agents },
      { label: "claims held", value: next.claims, delta: next.claims - prior.claims },
      {
        label: "obs / min",
        value: next.ratePerMinute,
        delta: next.ratePerMinute - prior.ratePerMinute,
      },
      { label: "risk signals", value: next.risk, delta: next.risk - prior.risk },
    ]);
  }, [snap, log, nowMs]);

  return {
    snap,
    stamp: stampFor(snap.fetchedAt),
    kpis,
    log,
    spineSource,
    provenance,
    coverage: eventCoverageOf(
      log,
      provenance === "hub" ? "hub" : provenance === "connecting" ? "connecting" : "derived",
    ),
    nowMs,
    reliability,
    federation,
    metrics,
    sessions,
    waits,
    anomalyReport,
    receipts,
    operatorActions,
  };
}
