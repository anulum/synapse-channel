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
  parseOperatorActionsPage,
  parseReceiptsPage,
  type OperatorActionRow,
  type OperatorActionsState,
  type ReceiptRow,
  type ReceiptsState,
} from "../lib/auditFeeds";
import {
  createEventsTailSource,
  mapStoredEvent,
  parseTail,
  type SpineProvenance,
} from "../lib/eventsTail";
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
import {
  createLiveTransport,
  type LiveChannelFrame,
  type LiveConnectionState,
} from "../lib/liveTransport";
import { createReliabilityStore, type ReliabilityState } from "../lib/reliability";
import { createSessionsStore, type SessionsState } from "../lib/sessions";
import {
  createSnapshotStore,
  parseSnapshot,
  withFreshness,
  type SnapshotStore,
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

/** Start the legacy high-frequency feeds only after a sustained stream outage. */
export const LIVE_TRANSPORT_FALLBACK_MS = 6_000;

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

function mergeAuditRows<T extends { readonly seq: number }>(
  current: readonly T[] | null,
  incoming: readonly T[],
  retainedLimit = 100,
): readonly T[] {
  const bySequence = new Map<number, T>();
  for (const row of current ?? []) bySequence.set(row.seq, row);
  for (const row of incoming) bySequence.set(row.seq, row);
  return [...bySequence.values()]
    .sort((left, right) => right.seq - left.seq)
    .slice(0, retainedLimit);
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
  readonly transport: LiveConnectionState;
}

/**
 * Own the multiplexed stream and its bounded polling fallback for one credential revision.
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
  const [transportState, setTransportState] = useState<LiveConnectionState>({
    status: "connecting",
    attempt: 0,
    detail: null,
  });
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
    setTransportState({ status: "connecting", attempt: 0, detail: null });
    previous.current = ZERO_METRICS;
    if (blocked) return;

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
      setLog((current) =>
        current.some((candidate) => candidate.seq === event.seq)
          ? current
          : [event, ...current].slice(0, EVENT_RETENTION_LIMIT),
      );
      for (const listener of routed) listener(event);
    };

    const activate = (next: "tail" | "derived", nextProvenance: SpineProvenance): void => {
      if (active !== next) {
        active = next;
        setLog([]);
      }
      setProvenance(nextProvenance);
    };

    let streamSnapshotState = INITIAL_SNAPSHOT;
    const streamSnapshotListeners = new Set<(state: SnapshotState) => void>();
    const streamSnapshotStore: SnapshotStore = {
      subscribe(listener) {
        streamSnapshotListeners.add(listener);
        listener(streamSnapshotState);
        return () => streamSnapshotListeners.delete(listener);
      },
      stop() {
        streamSnapshotListeners.clear();
      },
    };
    const publishStreamSnapshot = (next: SnapshotState): void => {
      streamSnapshotState = next;
      setSnap(next);
      for (const listener of streamSnapshotListeners) listener(next);
    };
    const derived = createSnapshotEventSource(streamSnapshotStore);
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
      stopAuxiliaryFeeds = () => {
        unsubscribeReliability();
        unsubscribeFederation();
        unsubscribeMetrics();
        unsubscribeSessions();
        unsubscribeWaits();
        if (anomalyFallback !== undefined) clearTimeout(anomalyFallback);
        unsubscribeAnomalies?.();
        reliabilityStore.stop();
        federationStore.stop();
        metricsStore.stop();
        sessionsStore.stop();
        waitsStore.stop();
        anomaliesStore?.stop();
      };
    };
    auxiliaryFallback = setTimeout(startAuxiliaryFeeds, AUXILIARY_FEED_START_FALLBACK_MS);

    let stopPollingFallback: (() => void) | undefined;
    const startPollingFallback = (): void => {
      if (!effectActive || stopPollingFallback !== undefined) return;
      const snapshotStore = createSnapshotStore();
      const fallbackDerived = createSnapshotEventSource(snapshotStore);
      const tail = createEventsTailSource();
      const unsubscribeSnapshots = snapshotStore.subscribe(setSnap);
      const unsubscribeFallbackDerived = fallbackDerived.subscribe((event) => {
        if (active === "derived") push(event);
      });
      const unsubscribeTail = tail.subscribe((event) => {
        if (active === "tail") push(event);
      });
      let receiptsStore: ReturnType<typeof createReceiptsStore> | undefined;
      let operatorActionsStore: ReturnType<typeof createOperatorActionsStore> | undefined;
      let unsubscribeReceipts: (() => void) | undefined;
      let unsubscribeOperatorActions: (() => void) | undefined;
      let fallbackAuditTimer: ReturnType<typeof setTimeout> | undefined;
      const startFallbackAudits = (): void => {
        if (receiptsStore !== undefined) return;
        if (fallbackAuditTimer !== undefined) clearTimeout(fallbackAuditTimer);
        receiptsStore = createReceiptsStore();
        operatorActionsStore = createOperatorActionsStore();
        unsubscribeReceipts = receiptsStore.subscribe(setReceipts);
        unsubscribeOperatorActions = operatorActionsStore.subscribe(setOperatorActions);
      };
      const unsubscribeMode = tail.subscribeMode((mode) => {
        const next = mode === "hub" ? "tail" : mode === "connecting" ? active : "derived";
        if (next !== null) activate(next, mode);
        if (mode !== "connecting") {
          startFallbackAudits();
          startAuxiliaryFeeds();
        }
      });
      fallbackAuditTimer = setTimeout(startFallbackAudits, AUXILIARY_FEED_START_FALLBACK_MS);
      stopPollingFallback = () => {
        unsubscribeSnapshots();
        unsubscribeFallbackDerived();
        unsubscribeTail();
        unsubscribeMode();
        if (fallbackAuditTimer !== undefined) clearTimeout(fallbackAuditTimer);
        unsubscribeReceipts?.();
        unsubscribeOperatorActions?.();
        snapshotStore.stop();
        fallbackDerived.stop();
        tail.stop();
        receiptsStore?.stop();
        operatorActionsStore?.stop();
        stopPollingFallback = undefined;
      };
    };

    let liveFallback: ReturnType<typeof setTimeout> | undefined;
    const schedulePollingFallback = (): void => {
      if (liveFallback !== undefined || stopPollingFallback !== undefined) return;
      liveFallback = setTimeout(() => {
        liveFallback = undefined;
        startPollingFallback();
        setTransportState((current) => ({
          status: "fallback",
          attempt: current.attempt,
          detail: current.detail,
        }));
      }, LIVE_TRANSPORT_FALLBACK_MS);
    };
    const confirmLiveTransport = (): void => {
      if (liveFallback !== undefined) {
        clearTimeout(liveFallback);
        liveFallback = undefined;
      }
      stopPollingFallback?.();
      setTransportState({ status: "live", attempt: 0, detail: null });
      startAuxiliaryFeeds();
    };

    const applySnapshotFrame = (frame: LiveChannelFrame): void => {
      if (frame.status === "unchanged") {
        setSnap((current) =>
          current.snapshot === null
            ? { ...current, status: "error", error: "snapshot heartbeat arrived before bootstrap" }
            : { ...current, status: "live", fetchedAt: frame.sentAt, error: null },
        );
        return;
      }
      if (frame.status !== "live") {
        const detail = frame.detail ?? `snapshot stream is ${frame.status}`;
        setSnap((current) => ({ ...current, error: detail }));
        return;
      }
      const snapshot = parseSnapshot(frame.data);
      if (snapshot === null) {
        setSnap((current) => ({ ...current, error: "stream snapshot was not an object" }));
        return;
      }
      publishStreamSnapshot({ snapshot, status: "live", fetchedAt: frame.sentAt, error: null });
    };

    const applyEventsFrame = (frame: LiveChannelFrame): void => {
      if (frame.status === "absent") {
        activate("derived", "absent");
        return;
      }
      if (frame.status === "error") {
        setProvenance("error");
        return;
      }
      const page = parseTail(frame.data);
      if (page === null) {
        setProvenance("error");
        return;
      }
      activate("tail", "hub");
      for (const event of page.events) push(mapStoredEvent(event));
    };

    const applyReceiptsFrame = (frame: LiveChannelFrame): void => {
      if (frame.status === "absent") {
        setReceipts((current) => ({ ...current, status: "absent", error: null }));
        return;
      }
      if (frame.status === "error") {
        setReceipts((current) => ({ ...current, status: "error", error: frame.detail ?? "stream error" }));
        return;
      }
      const page = parseReceiptsPage(frame.data);
      if (page === null) {
        setReceipts((current) => ({ ...current, status: "error", error: "stream payload was not parseable" }));
        return;
      }
      setReceipts((current) => ({
        data: mergeAuditRows<ReceiptRow>(current.data, page.rows),
        status: "live",
        fetchedAt: frame.sentAt,
        error: null,
      }));
    };

    const applyOperatorActionsFrame = (frame: LiveChannelFrame): void => {
      if (frame.status === "absent") {
        setOperatorActions((current) => ({ ...current, status: "absent", error: null }));
        return;
      }
      if (frame.status === "error") {
        setOperatorActions((current) => ({ ...current, status: "error", error: frame.detail ?? "stream error" }));
        return;
      }
      const page = parseOperatorActionsPage(frame.data);
      if (page === null) {
        setOperatorActions((current) => ({
          ...current,
          status: "error",
          error: "stream payload was not parseable",
        }));
        return;
      }
      setOperatorActions((current) => ({
        data: mergeAuditRows<OperatorActionRow>(current.data, page.rows),
        status: "live",
        fetchedAt: frame.sentAt,
        error: null,
      }));
    };

    const transport = createLiveTransport();
    const unsubscribeFrames = transport.subscribeFrames((frame) => {
      confirmLiveTransport();
      if (frame.channel === "snapshot") applySnapshotFrame(frame);
      else if (frame.channel === "events") applyEventsFrame(frame);
      else if (frame.channel === "receipts") applyReceiptsFrame(frame);
      else applyOperatorActionsFrame(frame);
    });
    const unsubscribeTransportState = transport.subscribeState((state: LiveConnectionState) => {
      // Once polling is active, reconnect attempts are an implementation detail: keep
      // the HUD honest about the data path currently serving the operator. A valid
      // channel frame, rather than a merely-open HTTP response, confirms recovery.
      if (stopPollingFallback === undefined) setTransportState(state);
      if (state.status === "unsupported") {
        if (liveFallback !== undefined) clearTimeout(liveFallback);
        liveFallback = undefined;
        startPollingFallback();
        setTransportState({
          status: "fallback",
          attempt: state.attempt,
          detail: state.detail,
        });
      } else if (state.status === "reconnecting" || state.status === "gap") {
        schedulePollingFallback();
      }
    });

    const clock = setInterval(() => {
      const tick = Date.now();
      setNowMs(tick);
      setSnap((current) => withFreshness(current, tick));
    }, 1000);

    return () => {
      effectActive = false;
      if (auxiliaryFallback !== undefined) clearTimeout(auxiliaryFallback);
      if (liveFallback !== undefined) clearTimeout(liveFallback);
      unsubscribeFrames();
      unsubscribeTransportState();
      unsubscribeDerived();
      transport.stop();
      stopPollingFallback?.();
      stopAuxiliaryFeeds?.();
      clearInterval(clock);
      derived.stop();
      streamSnapshotStore.stop();
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
    transport: transportState,
  };
}
