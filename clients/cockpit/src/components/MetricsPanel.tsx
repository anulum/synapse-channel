// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — the log-pulse panel: store-attested counts, drawn as plain bars

import { memo } from "react";

import { orderKindCounts, type MetricsState, type MetricsWindow } from "../lib/metrics";
import type { SessionsState } from "../lib/sessions";

/** Kind → semantic colour class, matching the spine's palette discipline. */
function kindClass(kind: string): string {
  if (kind === "claim") return "metrics-bar__fill--info";
  if (kind === "release" || kind === "ledger_task") return "metrics-bar__fill--healthy";
  if (kind === "ledger_progress") return "metrics-bar__fill--warn";
  return "metrics-bar__fill--dim";
}

function stampOf(ts: number | null): string {
  if (ts === null) return "—";
  return new Date(ts * 1000).toLocaleString([], {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

interface KindBarsProps {
  readonly counts: Readonly<Record<string, number>>;
}

/** Horizontal count bars scaled to the largest kind — no chart library. */
function KindBars({ counts }: KindBarsProps): JSX.Element {
  const ordered = orderKindCounts(counts);
  const top = ordered[0]?.[1] ?? 0;
  return (
    <div className="metrics-bars">
      {ordered.map(([kind, count]) => (
        <div key={kind} className="metrics-bar" title={`${kind}: ${count}`}>
          <span className="metrics-bar__kind">{kind}</span>
          <span className="metrics-bar__track">
            <span
              className={`metrics-bar__fill ${kindClass(kind)}`}
              style={{ width: top === 0 ? 0 : `${Math.max(2, (count / top) * 100)}%` }}
            />
          </span>
          <span className="metrics-bar__count">{count}</span>
        </div>
      ))}
    </div>
  );
}

interface WindowBlockProps {
  readonly name: string;
  readonly window: MetricsWindow;
}

function WindowBlock({ name, window }: WindowBlockProps): JSX.Element {
  return (
    <div className="metrics-window">
      <span className="metrics-window__head">
        {name.replace(/_/g, " ")} · {window.events}
      </span>
      <KindBars counts={window.byKind} />
    </div>
  );
}

interface MetricsPanelProps {
  /** The log-pulse feed's current state, including how it was obtained. */
  readonly state: MetricsState;
  /** The sessions/cost feed, rendered under the log pulse when served. */
  readonly sessions?: SessionsState | undefined;
}

function MetricsPanelView({ state, sessions }: MetricsPanelProps): JSX.Element {
  const metrics = state.data;

  return (
    <section className="panel" aria-label="Log metrics">
      <div className="panel__head">
        <span>Metrics</span>
        {metrics !== null && <span className="panel__count">{metrics.log.totalEvents}</span>}
        <span className="panel__sub">store-attested log pulse</span>
      </div>
      <div className="panel__body">
        {state.status === "absent" ? (
          <p className="panel__placeholder">
            This hub's dashboard does not serve log metrics yet
            (no /metrics.json). The panel activates as soon as it does.
          </p>
        ) : metrics === null ? (
          <p className="panel__placeholder">
            {state.status === "error"
              ? `Metrics feed failed: ${state.error ?? "unknown"}`
              : "Waiting for the hub."}
          </p>
        ) : (
          <>
            <div className="metrics-coverage">
              <span className="metrics-coverage__item">
                {`${metrics.log.totalEvents} events · seq ${metrics.log.maxSeq}`}
              </span>
              <span className="metrics-coverage__item">
                {`${stampOf(metrics.log.firstTs)} → ${stampOf(metrics.log.lastTs)}`}
              </span>
            </div>
            {Object.entries(metrics.windows).map(([name, window]) => (
              <WindowBlock key={name} name={name} window={window} />
            ))}
            <div className="metrics-window">
              <span className="metrics-window__head">whole log</span>
              <KindBars counts={metrics.eventsByKind} />
            </div>
            {metrics.note !== "" && <p className="metrics-note">{metrics.note}</p>}
          </>
        )}
        <div className="metrics-window">
          <span className="metrics-window__head">
            sessions · cost
            {sessions?.data !== null && sessions?.data !== undefined
              ? ` · ${sessions.data.sessions.length}`
              : ""}
          </span>
          {sessions === undefined || sessions.status === "absent" ? (
            <p className="panel__placeholder">
              Session telemetry not served (/sessions.json) — activates when the
              store feed ships it.
            </p>
          ) : sessions.data === null ? (
            <p className="panel__placeholder">
              {sessions.status === "error"
                ? `Sessions feed failed: ${sessions.error ?? "unknown"}`
                : "Waiting for the hub."}
            </p>
          ) : sessions.data.sessions.length === 0 ? (
            <p className="panel__placeholder">No session metrics recorded yet.</p>
          ) : (
            <>
              <table className="sessions-table">
                <thead>
                  <tr>
                    <th>agent</th>
                    <th>task</th>
                    <th>turns</th>
                    <th>tokens</th>
                    <th>cost</th>
                  </tr>
                </thead>
                <tbody>
                  {sessions.data.sessions.slice(0, 20).map((row) => (
                    <tr key={`${row.agent}:${row.sessionId}:${row.seq}`}>
                      <td title={row.agent}>{row.agent}</td>
                      <td title={row.taskId}>{row.taskId === "" ? "—" : row.taskId}</td>
                      <td>{row.turns}</td>
                      <td>{row.totalTokens}</td>
                      <td>{row.costUsd === null ? "—" : `$${row.costUsd.toFixed(4)}`}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {sessions.data.sessions.length > 20 && (
                <p className="metrics-note">{`+${sessions.data.sessions.length - 20} more sessions in the feed`}</p>
              )}
              {sessions.data.note !== "" && <p className="metrics-note">{sessions.data.note}</p>}
            </>
          )}
        </div>
      </div>
    </section>
  );
}

/** Memoised: re-renders only when its own data changes, not on the 1 s clock. */
export const MetricsPanel = memo(MetricsPanelView);
