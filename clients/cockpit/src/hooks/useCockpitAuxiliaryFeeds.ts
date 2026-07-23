// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — lifecycle owner for slow cockpit report feeds

import { useCallback, useEffect, useRef, useState } from "react";

import { createFederationStore, type FederationState } from "../lib/federation";
import {
  createHealthAnomaliesStore,
  type HealthAnomaliesState,
} from "../lib/healthAnomalies";
import { createMetricsStore, type MetricsState } from "../lib/metrics";
import { createReliabilityStore, type ReliabilityState } from "../lib/reliability";
import { createSessionsStore, type SessionsState } from "../lib/sessions";
import { createWaitsStore, type WaitsState } from "../lib/waits";

const CONNECTING = { data: null, status: "connecting", fetchedAt: null, error: null } as const;

/** Maximum wait before reports start when the primary event tail hangs. */
export const AUXILIARY_FEED_START_FALLBACK_MS = 20_000;

/** Maximum stagger before the second whole-log report starts on a slow store. */
export const HEAVY_FEED_STAGGER_FALLBACK_MS = 45_000;

/** Slow report states plus an idempotent primary-history-ready signal. */
export interface CockpitAuxiliaryFeeds {
  readonly reliability: ReliabilityState;
  readonly federation: FederationState;
  readonly metrics: MetricsState;
  readonly sessions: SessionsState;
  readonly waits: WaitsState;
  readonly anomalyReport: HealthAnomaliesState;
  readonly start: () => void;
}

/**
 * Own slow report stores independently from the high-frequency live transport.
 *
 * The primary feed calls `start` after exact history settles. A bounded timer
 * prevents a hanging event endpoint from starving reports forever. Reliability
 * starts before the second whole-log anomaly report so both scans do not compete.
 */
export function useCockpitAuxiliaryFeeds(
  blocked: boolean,
  credentialRevision: number,
): CockpitAuxiliaryFeeds {
  const [reliability, setReliability] = useState<ReliabilityState>(CONNECTING);
  const [federation, setFederation] = useState<FederationState>(CONNECTING);
  const [metrics, setMetrics] = useState<MetricsState>(CONNECTING);
  const [sessions, setSessions] = useState<SessionsState>(CONNECTING);
  const [waits, setWaits] = useState<WaitsState>(CONNECTING);
  const [anomalyReport, setAnomalyReport] = useState<HealthAnomaliesState>(CONNECTING);
  const startRef = useRef<(() => void) | null>(null);
  const start = useCallback(() => startRef.current?.(), []);

  useEffect(() => {
    setReliability(CONNECTING);
    setFederation(CONNECTING);
    setMetrics(CONNECTING);
    setSessions(CONNECTING);
    setWaits(CONNECTING);
    setAnomalyReport(CONNECTING);
    startRef.current = null;
    if (blocked) return;

    let stopFeeds: (() => void) | undefined;
    let startFallback: ReturnType<typeof setTimeout>;
    const startFeeds = (): void => {
      if (stopFeeds !== undefined) return;
      clearTimeout(startFallback);
      const reliabilityStore = createReliabilityStore();
      const federationStore = createFederationStore();
      const metricsStore = createMetricsStore();
      const sessionsStore = createSessionsStore();
      const waitsStore = createWaitsStore();
      const unsubscribeFederation = federationStore.subscribe(setFederation);
      const unsubscribeMetrics = metricsStore.subscribe(setMetrics);
      const unsubscribeSessions = sessionsStore.subscribe(setSessions);
      const unsubscribeWaits = waitsStore.subscribe(setWaits);
      let anomaliesStore: ReturnType<typeof createHealthAnomaliesStore> | undefined;
      let unsubscribeAnomalies: (() => void) | undefined;
      let anomalyFallback: ReturnType<typeof setTimeout>;
      const startAnomalies = (): void => {
        if (anomaliesStore !== undefined) return;
        clearTimeout(anomalyFallback);
        anomaliesStore = createHealthAnomaliesStore();
        unsubscribeAnomalies = anomaliesStore.subscribe(setAnomalyReport);
      };
      const unsubscribeReliability = reliabilityStore.subscribe((state) => {
        setReliability(state);
        if (state.status !== "connecting") startAnomalies();
      });
      anomalyFallback = setTimeout(startAnomalies, HEAVY_FEED_STAGGER_FALLBACK_MS);
      stopFeeds = () => {
        unsubscribeReliability();
        unsubscribeFederation();
        unsubscribeMetrics();
        unsubscribeSessions();
        unsubscribeWaits();
        clearTimeout(anomalyFallback);
        unsubscribeAnomalies?.();
        reliabilityStore.stop();
        federationStore.stop();
        metricsStore.stop();
        sessionsStore.stop();
        waitsStore.stop();
        anomaliesStore?.stop();
      };
    };
    startRef.current = startFeeds;
    startFallback = setTimeout(startFeeds, AUXILIARY_FEED_START_FALLBACK_MS);

    return () => {
      startRef.current = null;
      clearTimeout(startFallback);
      stopFeeds?.();
    };
  }, [blocked, credentialRevision]);

  return { reliability, federation, metrics, sessions, waits, anomalyReport, start };
}
