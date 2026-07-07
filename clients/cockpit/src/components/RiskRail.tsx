// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — the risk rail: the operator's triage list

import { memo } from "react";

import type { AnomalyFlag } from "../lib/anomalies";
import type { PendingApprovalView } from "../lib/approvals";
import type { DeadLetterView } from "../lib/deadLetters";
import type { HealthAnomaliesState } from "../lib/healthAnomalies";
import type { WaitsState } from "../lib/waits";
import { orderSignals } from "../lib/risk";
import type { RiskView } from "../types";

function deadLetterTime(ts: number | null): string {
  if (ts === null) return "—";
  return new Date(ts * 1000).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

/** Glyph per level — redundant with colour, never colour alone. */
const LEVEL_GLYPH: Record<RiskView["level"], string> = {
  red: "▲",
  amber: "!",
  green: "·",
};

interface RiskRailProps {
  /** The snapshot's risk view, or null before the first successful fetch. */
  readonly risk: RiskView | null;
  /**
   * Client-side repetition heuristics over the observed window — rendered in
   * their own clearly-labelled section, never mixed into the hub's signals.
   */
  readonly anomalies?: readonly AnomalyFlag[];
  /** Hub-recorded dead letters: targets whose messages nobody reads. */
  readonly deadLetters?: readonly DeadLetterView[];
  /** Read-only pending coordination gates (/waits.json). */
  readonly waits?: WaitsState | undefined;
  /** The hub's causal-graph anomaly report (/health-anomalies.json). */
  readonly anomalyReport?: HealthAnomaliesState | undefined;
  /** Relays awaiting a second operator (state.pending_relay_approvals). */
  readonly approvals?: readonly PendingApprovalView[];
}

/** How many safe-next-work rows show before the tail collapses into a count. */
const SAFE_WORK_SHOWN = 14;

function RiskRailView({
  risk,
  anomalies = [],
  deadLetters = [],
  waits,
  anomalyReport,
  approvals = [],
}: RiskRailProps): JSX.Element {
  const signals = risk === null ? [] : orderSignals(risk.signals);
  const safeWork = risk?.safe_next_work ?? [];
  const safeShown = safeWork.slice(0, SAFE_WORK_SHOWN);
  const safeOverflow = safeWork.length - safeShown.length;

  return (
    <section className="panel" aria-label="Risk rail">
      <div className="panel__head">
        <span>Risk rail</span>
        {risk !== null && (
          <span className={`risk-verdict risk-verdict--${risk.level}`}>
            {LEVEL_GLYPH[risk.level]} {risk.level}
          </span>
        )}
      </div>
      <div className="panel__body" tabIndex={0}>
        {risk === null ? (
          <p className="panel__placeholder">Waiting for the hub.</p>
        ) : signals.length === 0 ? (
          <p className="panel__placeholder">No risk signals recorded.</p>
        ) : (
          <ul className="risk-list">
            {signals.map((signal, index) => (
              <li
                key={`${signal.category}:${signal.subject}:${index}`}
                className={`risk-row risk-row--${signal.level}`}
              >
                <span className="risk-row__glyph" aria-hidden="true">
                  {LEVEL_GLYPH[signal.level]}
                </span>
                <span className="risk-row__body">
                  <span className="risk-row__subject">
                    <span className="risk-row__category">{signal.category}</span>
                    {signal.subject}
                  </span>
                  {signal.detail !== "" && <span className="risk-row__detail">{signal.detail}</span>}
                </span>
              </li>
            ))}
          </ul>
        )}
        {deadLetters.length > 0 && (
          <div className="risk-heuristics">
            <span
              className="risk-safe__head"
              title="The hub recorded messages for these targets, and no reader has picked them up."
            >
              dead letters · nobody listening
            </span>
            <ul className="risk-list">
              {deadLetters.map((letter) => (
                <li key={letter.target} className="risk-row risk-row--red">
                  <span className="risk-row__glyph" aria-hidden="true">
                    ✉
                  </span>
                  <span className="risk-row__body">
                    <span className="risk-row__subject">
                      <span className="risk-row__category">{`${letter.count} unread`}</span>
                      {letter.target}
                    </span>
                    <span className="risk-row__detail">
                      {`last from ${letter.lastSender === "" ? "—" : letter.lastSender} at ${deadLetterTime(letter.lastTs)}`}
                    </span>
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}
        {anomalyReport?.data != null && anomalyReport.data.anomalyCount > 0 && (
          <div className="risk-heuristics">
            <span className="risk-safe__head" title="The hub's own causal-graph anomaly report.">
              hub health anomalies · {anomalyReport.data.anomalyCount}
            </span>
            <ul className="risk-list">
              {[
                ...anomalyReport.data.orphaned.map((item) => ({ ...item, category: "orphaned" })),
                ...anomalyReport.data.dangling.map((item) => ({ ...item, category: "dangling" })),
                ...anomalyReport.data.stale.map((item) => ({ ...item, category: "stale" })),
              ].map((item) => (
                <li key={`${item.category}:${item.taskId}`} className="risk-row risk-row--amber">
                  <span className="risk-row__glyph" aria-hidden="true">
                    !
                  </span>
                  <span className="risk-row__body">
                    <span className="risk-row__subject">
                      <span className="risk-row__category">{item.category}</span>
                      {item.taskId}
                    </span>
                    <span className="risk-row__detail">{item.detail}</span>
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}
        {waits?.data != null && waits.data.waits.length > 0 && (
          <div className="risk-heuristics">
            <span
              className="risk-safe__head"
              title="Tasks standing behind unmet dependencies — the pending decision queue, read-only."
            >
              pending gates · {waits.data.waitCount}
            </span>
            <ul className="risk-list">
              {waits.data.waits.slice(0, 8).map((wait) => (
                <li key={wait.taskId} className="risk-row risk-row--amber">
                  <span className="risk-row__glyph" aria-hidden="true">
                    ⏳
                  </span>
                  <span className="risk-row__body">
                    <span className="risk-row__subject">
                      <span className="risk-row__category">{wait.who === "" ? "unowned" : wait.who}</span>
                      {wait.taskId}
                    </span>
                    <span className="risk-row__detail">{`waits on ${wait.onWhat.join(", ")}`}</span>
                  </span>
                </li>
              ))}
              {waits.data.waits.length > 8 && (
                <li className="risk-row risk-row--amber">
                  <span className="risk-row__body">
                    <span className="risk-row__detail">{`+${waits.data.waits.length - 8} more gates`}</span>
                  </span>
                </li>
              )}
            </ul>
          </div>
        )}
        {approvals.length > 0 && (
          <div className="risk-heuristics">
            <span
              className="risk-safe__head"
              title="The hub's two-person relay quorum: the first operator requested; a second, different operator must approve before anything applies."
            >
              pending approvals · awaiting a second operator
            </span>
            <ul className="risk-list">
              {approvals.map((approval, index) => (
                <li
                  key={`${approval.action}:${approval.namespace}:${approval.taskId}:${index}`}
                  className="risk-row risk-row--amber"
                >
                  <span className="risk-row__glyph" aria-hidden="true">
                    ✍
                  </span>
                  <span className="risk-row__body">
                    <span className="risk-row__subject">
                      <span className="risk-row__category">{approval.action === "" ? "relay" : approval.action}</span>
                      {approval.taskId === "" ? "—" : approval.taskId}
                    </span>
                    <span className="risk-row__detail">
                      {`in ${approval.namespace === "" ? "—" : approval.namespace} · requested by ${approval.requester === "" ? "—" : approval.requester}`}
                    </span>
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}
        {anomalies.length > 0 && (
          <div className="risk-heuristics">
            <span
              className="risk-safe__head"
              title="Computed client-side from the observed event window only; not the hub's verdict."
            >
              repetition heuristics · observed window
            </span>
            <ul className="risk-list">
              {anomalies.map((flag) => (
                <li key={`${flag.kind}:${flag.taskId}`} className="risk-row risk-row--amber">
                  <span className="risk-row__glyph" aria-hidden="true">
                    ↻
                  </span>
                  <span className="risk-row__body">
                    <span className="risk-row__subject">
                      <span className="risk-row__category">
                        {flag.kind === "claim_churn" ? "churn" : "lease"}
                      </span>
                      {flag.taskId}
                    </span>
                    <span className="risk-row__detail">{flag.detail}</span>
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}
        {safeWork.length > 0 && (
          <div className="risk-safe">
            <span className="risk-safe__head">Safe next work</span>
            <ul className="risk-safe__list">
              {safeShown.map((item) => (
                <li key={item} className="risk-safe__item">
                  {item}
                </li>
              ))}
              {safeOverflow > 0 && (
                <li className="risk-safe__item risk-safe__item--more">{`+${safeOverflow} more`}</li>
              )}
            </ul>
          </div>
        )}
      </div>
    </section>
  );
}

/** Memoised: re-renders only when its own data changes, not on the 1 s clock. */
export const RiskRail = memo(RiskRailView);
