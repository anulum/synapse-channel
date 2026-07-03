// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — the risk rail: the operator's triage list

import { memo } from "react";

import type { AnomalyFlag } from "../lib/anomalies";
import { orderSignals } from "../lib/risk";
import type { RiskView } from "../types";

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
}

/** How many safe-next-work rows show before the tail collapses into a count. */
const SAFE_WORK_SHOWN = 14;

function RiskRailView({ risk, anomalies = [] }: RiskRailProps): JSX.Element {
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
      <div className="panel__body">
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
