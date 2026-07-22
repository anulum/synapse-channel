// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — explicit live, historical, and comparison mode boundary

import type { JSX } from "react";
import type { ReplayState } from "../lib/workspace";

interface TimeTravelBarProps {
  readonly mode: ReplayState["mode"];
  readonly label: string;
  readonly onModeChange: (mode: ReplayState["mode"]) => void;
}

/** Keep live and reconstructed evidence visually and semantically distinct. */
export function TimeTravelBar({ mode, label, onModeChange }: TimeTravelBarProps): JSX.Element {
  return (
    <div className={`timetravel timetravel--${mode}`}>
      <div className="timetravel__modes" role="group" aria-label="Fleet evidence time mode">
        {(["live", "history", "compare"] as const).map((candidate) => (
          <button
            key={candidate}
            type="button"
            className={`timetravel__mode${mode === candidate ? " timetravel__mode--active" : ""}`}
            aria-pressed={mode === candidate}
            onClick={() => onModeChange(candidate)}
          >
            {candidate}
          </button>
        ))}
      </div>
      <span className="timetravel__label" role="status" aria-live="polite">
        {label}
      </span>
    </div>
  );
}
