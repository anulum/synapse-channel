// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — cockpit app shell

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ActivitySpine } from "./components/ActivitySpine";
import { ClaimsBoard } from "./components/ClaimsBoard";
import { FederationRow } from "./components/FederationRow";
import { FindingsStream } from "./components/FindingsStream";
import { FleetRoster } from "./components/FleetRoster";
import { Hud, type Kpi } from "./components/Hud";
import { InspectorTabs } from "./components/InspectorTabs";
import { InstallChip } from "./components/InstallChip";
import { MobileNav, type MobileSegment } from "./components/MobileNav";
import { Palette } from "./components/Palette";
import { PanelBoundary } from "./components/PanelBoundary";
import { ReliabilityPanel } from "./components/ReliabilityPanel";
import { RiskRail } from "./components/RiskRail";
import { TaskBoard } from "./components/TaskBoard";
import { TimeTravelBar } from "./components/TimeTravelBar";
import { ToastStack } from "./components/ToastStack";
import { DetailDrawer } from "./components/DetailDrawer";
import { deriveAnomalies } from "./lib/anomalies";
import { parsePendingApprovals } from "./lib/approvals";
import { agentDetail, taskDetail } from "./lib/detail";
import { boardTruncation, deriveBoard, deriveFindings } from "./lib/board";
import { parseDeadLetters } from "./lib/deadLetters";
import type { TimeWindow } from "./lib/brush";
import { deriveClaims, parseConflicts } from "./lib/claims";
import { createEventsTailSource, type SpineProvenance } from "./lib/eventsTail";
import { queryFromHash, queryToHash, type LogQuery } from "./lib/logQuery";
import { createMetricsStore, type MetricsState } from "./lib/metrics";
import {
  applyTheme,
  persistTheme,
  resolveInitialTheme,
  toggledTheme,
  type Theme,
} from "./lib/theme";
import { createFederationStore, type FederationState } from "./lib/federation";
import { createHealthAnomaliesStore, type HealthAnomaliesState } from "./lib/healthAnomalies";
import { createSessionsStore, type SessionsState } from "./lib/sessions";
import { createWaitsStore, type WaitsState } from "./lib/waits";
import { createReliabilityStore, type ReliabilityState } from "./lib/reliability";
import { deriveRoster } from "./lib/roster";
import {
  createSnapshotStore,
  withFreshness,
  type SnapshotState,
} from "./lib/snapshot";
import { createSnapshotEventSource } from "./lib/spineEvents";
import { fetchStateAt, type FleetStateAt } from "./lib/stateAt";
import { focusClaims, focusTasks } from "./lib/focus";
import { buildCommands, type Command } from "./lib/palette";
import { readPref, writePref } from "./lib/prefs";
import { factsOf, toastsBetween, type FleetFacts, type Toast } from "./lib/toasts";
import type { CockpitEvent, EventSource } from "./types";

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

const INITIAL_SESSIONS: SessionsState = { data: null, status: "connecting", fetchedAt: null, error: null };
const INITIAL_WAITS: WaitsState = { data: null, status: "connecting", fetchedAt: null, error: null };
const INITIAL_ANOMALIES: HealthAnomaliesState = { data: null, status: "connecting", fetchedAt: null, error: null };

/** Most recent derived events held for the signal log. */
const LOG_LIMIT = 250;

interface Metrics {
  readonly agents: number;
  readonly claims: number;
  readonly risk: number;
  readonly ratePerMinute: number;
}

const ZERO_METRICS: Metrics = { agents: 0, claims: 0, risk: 0, ratePerMinute: 0 };

function metricsOf(state: SnapshotState, ratePerMinute: number): Metrics {
  const snapshot = state.snapshot;
  if (snapshot === null) return { ...ZERO_METRICS, ratePerMinute };
  return {
    agents: snapshot.fleet.agents.live.length,
    claims: snapshot.fleet.claims.active,
    risk: snapshot.risk.signals.filter((signal) => signal.level === "red").length,
    ratePerMinute,
  };
}

/**
 * Observed transitions in the trailing minute. Counts the derived event log
 * (capped upstream), so a fleet outpacing the cap reads as at-least-the-cap —
 * an undercount, never an invention.
 */
function observedPerMinute(log: readonly CockpitEvent[], nowMs: number): number {
  const since = nowMs / 1000 - 60;
  let count = 0;
  for (const event of log) {
    if (event.ts >= since) count += 1;
    else break; // newest-first: everything after this is older still
  }
  return count;
}

export function App(): JSX.Element {
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
  const [brush, setBrush] = useState<TimeWindow | null>(null);
  // Phone-width segment: one deck section at a time; CSS ignores this above
  // 640px, where the whole deck renders as always.
  const [mobileSegment, setMobileSegment] = useState<MobileSegment>("signals");
  // Fleet time-travel: when armed, the claims board, task board, and
  // topology render the moment reconstructed from the durable log; the
  // spine, log, and roster stay live. The bar is the loud boundary.
  const [travelOn, setTravelOn] = useState(false);
  const [travelSeq, setTravelSeq] = useState(0);
  const [travelState, setTravelState] = useState<FleetStateAt | null>(null);
  const [travelNote, setTravelNote] = useState<string | null>(null);
  const travelTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const travelFetch = useCallback((seq: number) => {
    void fetchStateAt(seq).then((result) => {
      if (result.kind === "loaded") {
        setTravelState(result.state);
        setTravelNote(null);
      } else {
        setTravelState(null);
        setTravelNote(result.kind === "absent" ? "state-at surface not served (--feeds-db)" : result.message);
      }
    });
  }, []);

  const onToggleTravel = useCallback(() => {
    setTravelOn((current) => {
      const next = !current;
      if (next) {
        // Arm at the log's end; the first fetch names the real bound.
        const seed = Number.MAX_SAFE_INTEGER;
        setTravelSeq(seed);
        travelFetch(seed);
      } else {
        setTravelState(null);
        setTravelNote(null);
      }
      return next;
    });
  }, [travelFetch]);

  const onScrubTravel = useCallback(
    (seq: number) => {
      setTravelSeq(seq);
      if (travelTimer.current !== undefined) clearTimeout(travelTimer.current);
      travelTimer.current = setTimeout(() => travelFetch(seq), 250);
    },
    [travelFetch],
  );

  // The command palette: Ctrl/Cmd+K anywhere.
  const [paletteOpen, setPaletteOpen] = useState(false);
  useEffect(() => {
    const onKey = (event: KeyboardEvent): void => {
      if (event.key.toLowerCase() === "k" && (event.ctrlKey || event.metaKey)) {
        event.preventDefault();
        setPaletteOpen((current) => !current);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // The detail drawer's subject: one agent or one task, or nothing.
  const [inspected, setInspected] = useState<
    { readonly kind: "agent" | "task"; readonly id: string } | null
  >(null);
  // A drawer's trace hop steers the inspector; the nonce re-fires same subjects.
  const [traceRequest, setTraceRequest] = useState<
    { readonly subject: string; readonly nonce: number } | undefined
  >(undefined);

  const onInspectAgent = useCallback((name: string) => setInspected({ kind: "agent", id: name }), []);
  const onInspectTask = useCallback((taskId: string) => setInspected({ kind: "task", id: taskId }), []);
  const onCloseDrawer = useCallback(() => setInspected(null), []);

  // The focus lens and density are small persisted preferences, same
  // storage discipline as the theme (a throwing storage costs persistence).
  const [focus, setFocus] = useState<string>(() => readPref(localStorage, "cockpit-focus") ?? "");
  const onFocusChange = useCallback((next: string) => {
    setFocus(next);
    writePref(localStorage, "cockpit-focus", next);
  }, []);
  const [density, setDensity] = useState<"cozy" | "compact">(() =>
    readPref(localStorage, "cockpit-density") === "compact" ? "compact" : "cozy",
  );
  const onToggleDensity = useCallback(() => {
    setDensity((current) => {
      const next = current === "cozy" ? "compact" : "cozy";
      writePref(localStorage, "cockpit-density", next);
      return next;
    });
  }, []);
  useEffect(() => {
    if (density === "compact") document.documentElement.setAttribute("data-density", "compact");
    else document.documentElement.removeAttribute("data-density");
  }, [density]);

  // Theme ladder: stored explicit choice, else the OS preference, else dark.
  const [theme, setTheme] = useState<Theme>(() =>
    resolveInitialTheme(localStorage, matchMedia("(prefers-color-scheme: light)").matches),
  );

  useEffect(() => {
    applyTheme(theme, document.documentElement);
  }, [theme]);

  const onToggleTheme = useCallback(() => {
    setTheme((current) => {
      const next = toggledTheme(current);
      persistTheme(next, localStorage);
      return next;
    });
  }, []);
  // The log query lives in the URL hash, so a filtered view is a shareable
  // address and survives a reload.
  const [logQuery, setLogQuery] = useState<LogQuery>(() =>
    queryFromHash(typeof location === "undefined" ? "" : location.hash),
  );
  const previous = useRef<Metrics>(ZERO_METRICS);

  const onQueryChange = useCallback((query: LogQuery) => {
    setLogQuery(query);
    const hash = queryToHash(query);
    history.replaceState(null, "", hash === "" ? location.pathname + location.search : `#${hash}`);
  }, []);

  // KPI drill-down: a headline number filters the log to the kinds behind it.
  const onSelectKpi = useCallback(
    (label: string) => {
      const kinds =
        label === "claims held"
          ? (["claim", "lease", "release"] as const)
          : label === "risk signals"
            ? (["conflict"] as const)
            : label === "agents online"
              ? (["presence"] as const)
              : null;
      onQueryChange({ text: "", kinds: kinds === null ? null : [...kinds], order: "newest", view: "flat" });
    },
    [onQueryChange],
  );

  // Stable identities so the spine's canvas effect never re-arms mid-flight.
  const onBrush = useCallback((window: TimeWindow | null) => setBrush(window), []);
  const onClearWindow = useCallback(() => setBrush(null), []);

  useEffect(() => {
    // The stores own their polling and are created per-mount so their
    // lifecycle is tied to the effect. Two event sources exist: the
    // hub-attested tail (/events.json, real seq + ts) and the snapshot-diff
    // derivation. The tail wins whenever the dashboard serves it; the
    // derivation is the honest fallback while the endpoint is absent. A
    // router forwards exactly one of them to the spine and the log, and a
    // provenance flip clears the log so the two never mix.
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
        // The router owns nothing; the effect cleanup stops the real sources.
      },
    });
    let active: "tail" | "derived" | null = null;
    const push = (event: CockpitEvent): void => {
      setLog((current) => [event, ...current].slice(0, LOG_LIMIT));
      for (const listener of routed) listener(event);
    };
    const unsubscribeTail = tail.subscribe((event) => {
      if (active === "tail") push(event);
    });
    const unsubscribeDerived = derived.subscribe((event) => {
      if (active === "derived") push(event);
    });
    const unsubscribeMode = tail.subscribeMode((mode) => {
      setProvenance(mode);
      // The tail feeds the deck only while it is genuinely live; on absence
      // AND on error the derivation takes over — an erroring endpoint must
      // never leave the cockpit with no event source at all. `connecting` is
      // the only (sub-second) window with no active source.
      const next = mode === "hub" ? "tail" : mode === "connecting" ? active : "derived";
      if (next !== active) {
        active = next;
        setLog([]);
      }
    });
    const unsubscribeSnapshots = store.subscribe(setSnap);
    // Reliability evidence is log-derived and heavier server-side, so it polls
    // on its own slow cadence, independent of the 2 s fleet snapshot.
    const reliabilityStore = createReliabilityStore();
    const unsubscribeReliability = reliabilityStore.subscribe(setReliability);
    const federationStore = createFederationStore();
    const unsubscribeFederation = federationStore.subscribe(setFederation);
    const metricsStore = createMetricsStore();
    const unsubscribeMetrics = metricsStore.subscribe(setMetrics);
    const sessionsStore = createSessionsStore();
    const unsubscribeSessions = sessionsStore.subscribe(setSessions);
    const waitsStore = createWaitsStore();
    const unsubscribeWaits = waitsStore.subscribe(setWaits);
    const anomaliesStore = createHealthAnomaliesStore();
    const unsubscribeAnomalies = anomaliesStore.subscribe(setAnomalyReport);
    // Re-evaluate freshness between polls so the beacon flips to `stale` even
    // while the hub is silent, without waiting for the next fetch to return.
    // The same tick drives the lease countdowns on the claims board.
    const clock = setInterval(() => {
      const tick = Date.now();
      setNowMs(tick);
      setSnap((current) => withFreshness(current, tick));
    }, 1000);
    return () => {
      unsubscribeTail();
      unsubscribeDerived();
      unsubscribeMode();
      unsubscribeSnapshots();
      unsubscribeReliability();
      unsubscribeFederation();
      unsubscribeMetrics();
      unsubscribeSessions();
      unsubscribeWaits();
      unsubscribeAnomalies();
      clearInterval(clock);
      tail.stop();
      derived.stop();
      store.stop();
      reliabilityStore.stop();
      federationStore.stop();
      metricsStore.stop();
      sessionsStore.stop();
      waitsStore.stop();
      anomaliesStore.stop();
    };
  }, []);

  useEffect(() => {
    const metrics = metricsOf(snap, observedPerMinute(log, nowMs));
    const prior = previous.current;
    previous.current = metrics;
    setKpis([
      { label: "agents online", value: metrics.agents, delta: metrics.agents - prior.agents },
      { label: "claims held", value: metrics.claims, delta: metrics.claims - prior.claims },
      {
        label: "obs / min",
        value: metrics.ratePerMinute,
        delta: metrics.ratePerMinute - prior.ratePerMinute,
      },
      { label: "risk signals", value: metrics.risk, delta: metrics.risk - prior.risk },
    ]);
  }, [snap, log, nowMs]);

  const roster = useMemo(() => deriveRoster(snap.snapshot), [snap.snapshot]);
  const waiters = snap.snapshot?.fleet.agents.waiters.length ?? 0;
  const liveClaims = useMemo(() => deriveClaims(snap.snapshot, nowMs), [snap.snapshot, nowMs]);
  const liveConflicts = useMemo(
    () => (snap.snapshot === null ? [] : parseConflicts(snap.snapshot)),
    [snap.snapshot],
  );
  const liveBoard = useMemo(() => deriveBoard(snap.snapshot), [snap.snapshot]);
  const travelling = travelOn && travelState !== null;
  const claims = travelling && travelState !== null ? travelState.claims : liveClaims;
  // Advisory conflicts are a live computation, not journalled — none in the past.
  const conflicts = travelling ? [] : liveConflicts;
  const board = travelling && travelState !== null ? travelState.tasks : liveBoard;
  // The focus lens narrows whatever is shown — live or reconstructed alike.
  const lensedClaims = useMemo(() => focusClaims(claims, focus), [claims, focus]);
  const lensedBoard = useMemo(() => focusTasks(board, claims, focus), [board, claims, focus]);
  const findings = useMemo(() => deriveFindings(snap.snapshot), [snap.snapshot]);
  const anomalies = useMemo(() => deriveAnomalies(log), [log]);
  const deadLetters = useMemo(() => parseDeadLetters(snap.snapshot), [snap.snapshot]);
  const approvals = useMemo(() => parsePendingApprovals(snap.snapshot), [snap.snapshot]);
  const connected = snap.snapshot !== null;

  // Toasts mark transitions between polls — computed from LIVE facts only
  // (the reconstruction must never fire alarms about the past). The first
  // capture emits nothing; each toast auto-dismisses after eight seconds.
  const [toasts, setToasts] = useState<readonly Toast[]>([]);
  const previousFacts = useRef<FleetFacts | null>(null);
  const onDismissToast = useCallback(
    (id: string) => setToasts((current) => current.filter((toast) => toast.id !== id)),
    [],
  );
  // Dismissal timers live across effect re-runs (each poll re-runs the
  // delta effect; cancelling there would keep every toast forever). They
  // are cleared only on unmount.
  const toastTimers = useRef<Set<ReturnType<typeof setTimeout>>>(new Set());
  useEffect(() => {
    const timers = toastTimers.current;
    return () => {
      for (const timer of timers) clearTimeout(timer);
    };
  }, []);
  useEffect(() => {
    if (snap.snapshot === null) return;
    const facts = factsOf(liveBoard, liveConflicts, deadLetters, snap.snapshot.risk, snap.snapshot.config_epoch);
    const fresh = toastsBetween(previousFacts.current, facts);
    previousFacts.current = facts;
    if (fresh.length === 0) return;
    setToasts((current) => {
      const seen = new Set(current.map((toast) => toast.id));
      return [...current, ...fresh.filter((toast) => !seen.has(toast.id))];
    });
    const ids = fresh.map((toast) => toast.id);
    const timer = setTimeout(() => {
      toastTimers.current.delete(timer);
      setToasts((current) => current.filter((toast) => !ids.includes(toast.id)));
    }, 8000);
    toastTimers.current.add(timer);
  }, [snap.snapshot, liveBoard, liveConflicts, deadLetters]);

  const paletteCommands = useMemo(
    () =>
      buildCommands(
        roster.map((entry) => entry.agent),
        liveBoard.map((task) => task.taskId),
      ),
    [roster, liveBoard],
  );

  const runPaletteCommand = useCallback(
    (command: Command) => {
      if (command.kind === "focus-agent") onFocusChange(command.subject);
      else if (command.kind === "inspect-agent") setInspected({ kind: "agent", id: command.subject });
      else if (command.kind === "inspect-task") setInspected({ kind: "task", id: command.subject });
      else if (command.kind === "trace-task")
        setTraceRequest((current) => ({ subject: command.subject, nonce: (current?.nonce ?? 0) + 1 }));
      else if (command.kind === "toggle-theme") onToggleTheme();
      else if (command.kind === "toggle-density") onToggleDensity();
      else if (command.kind === "toggle-travel") onToggleTravel();
      else if (command.kind === "clear-focus") onFocusChange("");
    },
    [onFocusChange, onToggleTheme, onToggleDensity, onToggleTravel],
  );

  return (
    <div className="shell">
      <Hud
        kpis={kpis}
        live={snap.status === "live"}
        stamp={stampFor(snap.fetchedAt)}
        onSelect={onSelectKpi}
        theme={theme}
        onToggleTheme={onToggleTheme}
        focus={focus}
        onFocusChange={onFocusChange}
        rosterNames={roster.map((entry) => entry.agent)}
        density={density}
        onToggleDensity={onToggleDensity}
      />
      <PanelBoundary name="Activity spine">
        <ActivitySpine
          key={provenance === "hub" ? "hub" : "derived"}
          source={spineSource}
          onBrush={onBrush}
          brush={brush}
        />
      </PanelBoundary>
      <PanelBoundary name="Federation">
        <FederationRow
          state={federation}
          hubVersion={snap.snapshot?.hub_version ?? ""}
          configEpoch={snap.snapshot?.config_epoch ?? ""}
        />
      </PanelBoundary>
      <TimeTravelBar
        on={travelOn}
        seq={travelSeq}
        state={travelState}
        note={travelNote}
        onToggle={onToggleTravel}
        onScrub={onScrubTravel}
      />
      <MobileNav active={mobileSegment} onSelect={setMobileSegment} />
      <div className={`deck deck--seg-${mobileSegment}`}>
        <div className="deck__stack deck__stack--roster">
          <div className="seg seg--roster">
            <PanelBoundary name="Fleet roster">
              <FleetRoster roster={roster} waiters={waiters} onInspect={onInspectAgent} />
            </PanelBoundary>
          </div>
          <div className="seg seg--reliability">
            <PanelBoundary name="Reliability">
              <ReliabilityPanel state={reliability} />
            </PanelBoundary>
          </div>
        </div>
        <div className="deck__stack">
          <div className="seg seg--claims">
            <PanelBoundary name="Claims">
              <ClaimsBoard claims={lensedClaims} conflicts={conflicts} connected={connected} lens={focus} />
            </PanelBoundary>
          </div>
          <div className="seg seg--signals">
            <PanelBoundary name="Inspector">
              <InspectorTabs
                events={log}
                window={brush}
                onClearWindow={onClearWindow}
                provenance={provenance === "hub" ? "hub" : "derived"}
                query={logQuery}
                onQueryChange={onQueryChange}
                claims={claims}
                conflicts={conflicts}
                liveAgentCount={snap.snapshot?.fleet.agents.live.length ?? 0}
                connected={connected}
                federation={federation}
                metrics={metrics}
                sessions={sessions}
                traceRequest={traceRequest}
              />
            </PanelBoundary>
          </div>
        </div>
        <div className="seg seg--board">
          <PanelBoundary name="Board">
            <TaskBoard
              tasks={lensedBoard}
              connected={connected}
              truncation={travelling ? undefined : boardTruncation(snap.snapshot)}
              onInspect={onInspectTask}
              lens={focus}
            />
          </PanelBoundary>
        </div>
        <div className="deck__stack deck__stack--rail">
          <div className="seg seg--signals">
            <PanelBoundary name="Risk rail">
              <RiskRail
                risk={snap.snapshot?.risk ?? null}
                anomalies={anomalies}
                deadLetters={deadLetters}
                waits={waits}
                anomalyReport={anomalyReport}
                approvals={approvals}
              />
            </PanelBoundary>
          </div>
          <div className="seg seg--signals">
            <PanelBoundary name="Findings">
              <FindingsStream findings={findings} connected={connected} />
            </PanelBoundary>
          </div>
        </div>
      </div>
      <InstallChip />
      <Palette
        open={paletteOpen}
        commands={paletteCommands}
        onClose={() => setPaletteOpen(false)}
        onRun={runPaletteCommand}
      />
      <ToastStack toasts={toasts} onDismiss={onDismissToast} />
      <DetailDrawer
        agent={
          inspected?.kind === "agent"
            ? agentDetail(inspected.id, roster, claims, deadLetters, log)
            : undefined
        }
        task={
          inspected?.kind === "task" ? taskDetail(inspected.id, board, claims, log) : undefined
        }
        onClose={onCloseDrawer}
        onFilterLog={(text) => {
          onQueryChange({ text, kinds: null, order: "newest", view: "flat" });
          setInspected(null);
        }}
        onTrace={(taskId) => {
          setTraceRequest((current) => ({ subject: taskId, nonce: (current?.nonce ?? 0) + 1 }));
          setInspected(null);
        }}
      />
    </div>
  );
}
