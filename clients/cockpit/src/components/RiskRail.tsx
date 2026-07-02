// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — the risk rail: the operator's triage list

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
}

export function RiskRail({ risk }: RiskRailProps): JSX.Element {
  const signals = risk === null ? [] : orderSignals(risk.signals);
  const safeWork = risk?.safe_next_work ?? [];

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
        {safeWork.length > 0 && (
          <div className="risk-safe">
            <span className="risk-safe__head">Safe next work</span>
            <ul className="risk-safe__list">
              {safeWork.map((item) => (
                <li key={item} className="risk-safe__item">
                  {item}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </section>
  );
}
