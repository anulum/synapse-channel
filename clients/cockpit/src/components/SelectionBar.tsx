// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE CHANNEL — visible cockpit selection and filter chips

import type { JSX } from "react";

import { windowEdgeLabel, type TimeWindow } from "../lib/brush";
import { selectionLabel } from "../lib/selection";
import type { CockpitSelection } from "../lib/workspace";

interface SelectionBarProps {
  readonly selection: CockpitSelection | null;
  readonly focus: string;
  readonly window: TimeWindow | null;
  readonly onClearSelection: () => void;
  readonly onClearFocus: () => void;
  readonly onClearWindow: () => void;
}

export function SelectionBar({
  selection,
  focus,
  window,
  onClearSelection,
  onClearFocus,
  onClearWindow,
}: SelectionBarProps): JSX.Element | null {
  if (selection === null && focus === "" && window === null) return null;
  const count = Number(selection !== null) + Number(focus !== "") + Number(window !== null);

  return (
    <section className="selection-bar" aria-label="Active cockpit selection and filters">
      <span className="selection-bar__label">active context</span>
      <div className="selection-bar__chips">
        {selection !== null && (
          <button
            type="button"
            className="selection-chip selection-chip--selected"
            onClick={onClearSelection}
            aria-label={`Clear selected ${selection.kind} ${selectionLabel(selection)}`}
          >
            <span>{selection.kind}</span>
            <strong>{selectionLabel(selection)}</strong>
            <i aria-hidden="true">×</i>
          </button>
        )}
        {focus !== "" && (
          <button
            type="button"
            className="selection-chip"
            onClick={onClearFocus}
            aria-label={`Clear focus filter ${focus}`}
          >
            <span>focus</span>
            <strong>{focus}</strong>
            <i aria-hidden="true">×</i>
          </button>
        )}
        {window !== null && (
          <button
            type="button"
            className="selection-chip"
            onClick={onClearWindow}
            aria-label="Clear brushed time window"
          >
            <span>window</span>
            <strong>{`${windowEdgeLabel(window.fromTs)}–${windowEdgeLabel(window.toTs)}`}</strong>
            <i aria-hidden="true">×</i>
          </button>
        )}
      </div>
      {count > 1 && (
        <button
          type="button"
          className="selection-bar__clear"
          onClick={() => {
            if (selection !== null) onClearSelection();
            if (focus !== "") onClearFocus();
            if (window !== null) onClearWindow();
          }}
        >
          clear all
        </button>
      )}
    </section>
  );
}
