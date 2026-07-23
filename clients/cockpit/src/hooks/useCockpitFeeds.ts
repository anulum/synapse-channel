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
import type { FederationState } from "../lib/federation";
import type { HealthAnomaliesState } from "../lib/healthAnomalies";
import type { MetricsState } from "../lib/metrics";
import {
  createLiveTransport,
  type LiveChannelFrame,
  type LiveConnectionState,
} from "../lib/liveTransport";
import {
  cockpitStamp,
  headlineKpis,
  headlineMetricsOf,
  ZERO_HEADLINE_METRICS,
  type HeadlineMetrics,
} from "../lib/cockpitKpis";
import {
  projectEventsFrame,
  projectOperatorActionsFrame,
  projectReceiptsFrame,
  projectSnapshotFrame,
} from "../lib/cockpitLiveFrames";
import type { ReliabilityState } from "../lib/reliability";
import type { SessionsState } from "../lib/sessions";
import {
  createSnapshotStore,
  withFreshness,
  type SnapshotStore,
  type SnapshotState,
} from "../lib/snapshot";
import { createSnapshotEventSource } from "../lib/spineEvents";
import type { WaitsState } from "../lib/waits";
import type { CockpitEvent, EventSource } from "../types";
import {
  AUXILIARY_FEED_START_FALLBACK_MS,
  HEAVY_FEED_STAGGER_FALLBACK_MS,
  useCockpitAuxiliaryFeeds,
} from "./useCockpitAuxiliaryFeeds";

export { AUXILIARY_FEED_START_FALLBACK_MS, HEAVY_FEED_STAGGER_FALLBACK_MS };

const INITIAL_SNAPSHOT: SnapshotState = {
  snapshot: null,
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

/** Start the legacy high-frequency feeds only after a sustained stream outage. */
export const LIVE_TRANSPORT_FALLBACK_MS = 6_000;

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
  const auxiliary = useCockpitAuxiliaryFeeds(blocked, credentialRevision);
  const [snap, setSnap] = useState<SnapshotState>(INITIAL_SNAPSHOT);
  const [kpis, setKpis] = useState<readonly Kpi[]>([]);
  const [log, setLog] = useState<readonly CockpitEvent[]>([]);
  const [spineSource, setSpineSource] = useState<EventSource | undefined>(undefined);
  const [provenance, setProvenance] = useState<SpineProvenance>("connecting");
  const [nowMs, setNowMs] = useState<number>(() => Date.now());
  const [receipts, setReceipts] = useState<ReceiptsState>(INITIAL_RECEIPTS);
  const [operatorActions, setOperatorActions] =
    useState<OperatorActionsState>(INITIAL_OPERATOR_ACTIONS);
  const [transportState, setTransportState] = useState<LiveConnectionState>({
    status: "connecting",
    attempt: 0,
    detail: null,
  });
  const previous = useRef<HeadlineMetrics>(ZERO_HEADLINE_METRICS);

  useEffect(() => {
    setSnap(INITIAL_SNAPSHOT);
    setKpis([]);
    setLog([]);
    setSpineSource(undefined);
    setProvenance("connecting");
    setReceipts(INITIAL_RECEIPTS);
    setOperatorActions(INITIAL_OPERATOR_ACTIONS);
    setTransportState({ status: "connecting", attempt: 0, detail: null });
    previous.current = ZERO_HEADLINE_METRICS;
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
          auxiliary.start();
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
      auxiliary.start();
    };

    const applySnapshotFrame = (frame: LiveChannelFrame): void => {
      const projection = projectSnapshotFrame(frame);
      if (projection.kind === "heartbeat") {
        setSnap((current) =>
          current.snapshot === null
            ? { ...current, status: "error", error: "snapshot heartbeat arrived before bootstrap" }
            : { ...current, status: "live", fetchedAt: projection.sentAt, error: null },
        );
        return;
      }
      if (projection.kind === "error") {
        setSnap((current) => ({ ...current, error: projection.error }));
        return;
      }
      publishStreamSnapshot(projection.state);
    };

    const applyEventsFrame = (frame: LiveChannelFrame): void => {
      const projection = projectEventsFrame(frame);
      if (projection.mode === null) setProvenance(projection.provenance);
      else activate(projection.mode, projection.provenance);
      for (const event of projection.events) push(event);
    };

    const applyReceiptsFrame = (frame: LiveChannelFrame): void => {
      setReceipts((current) => projectReceiptsFrame(current, frame));
    };

    const applyOperatorActionsFrame = (frame: LiveChannelFrame): void => {
      setOperatorActions((current) => projectOperatorActionsFrame(current, frame));
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
      if (liveFallback !== undefined) clearTimeout(liveFallback);
      unsubscribeFrames();
      unsubscribeTransportState();
      unsubscribeDerived();
      transport.stop();
      stopPollingFallback?.();
      clearInterval(clock);
      derived.stop();
      streamSnapshotStore.stop();
    };
  }, [auxiliary.start, blocked, credentialRevision]);

  useEffect(() => {
    const next = headlineMetricsOf(snap, log, nowMs);
    const prior = previous.current;
    previous.current = next;
    setKpis(headlineKpis(prior, next));
  }, [snap, log, nowMs]);

  return {
    snap,
    stamp: cockpitStamp(snap.fetchedAt),
    kpis,
    log,
    spineSource,
    provenance,
    coverage: eventCoverageOf(
      log,
      provenance === "hub" ? "hub" : provenance === "connecting" ? "connecting" : "derived",
    ),
    nowMs,
    reliability: auxiliary.reliability,
    federation: auxiliary.federation,
    metrics: auxiliary.metrics,
    sessions: auxiliary.sessions,
    waits: auxiliary.waits,
    anomalyReport: auxiliary.anomalyReport,
    receipts,
    operatorActions,
    transport: transportState,
  };
}
