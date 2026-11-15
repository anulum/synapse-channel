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

/** The brushed window as the chip shows it, from its first edge to its last. */
function windowRange(window: TimeWindow): string {
  return `${windowEdgeLabel(window.fromTs)}–${windowEdgeLabel(window.toTs)}`;
}

interface ContextChipProps {
  /** What the chip holds: a selection's kind, "focus" or "window". */
  readonly kind: string;
  /** The held value as the chip shows it. */
  readonly value: string;
  /** What pressing the chip does, in words, after its visible text. */
  readonly action: string;
  readonly selected?: boolean;
  readonly onClear: () => void;
}

/**
 * One chip of the active context. Its name starts with the words it shows (its
 * kind, then its value) and ends with what pressing it does, so a speech user
 * can say what they see (WCAG 2.5.3). The space keeps the two words apart for
 * readers of the text; the chip's flex layout does not draw it.
 */
function ContextChip({ kind, value, action, selected = false, onClear }: ContextChipProps): JSX.Element {
  return (
    <button
      type="button"
      className={selected ? "selection-chip selection-chip--selected" : "selection-chip"}
      onClick={onClear}
      aria-label={`${kind} ${value}: ${action}`}
    >
      <span>{kind}</span>{" "}
      <strong>{value}</strong>
      <i aria-hidden="true">×</i>
    </button>
  );
}

/** The one control that clears every active chip at once. */
function ClearAll({ onClear }: { readonly onClear: () => void }): JSX.Element {
  return (
    <button type="button" className="selection-bar__clear" onClick={onClear}>
      clear all
    </button>
  );
}

/** The active context's chips, and one control that clears them all. */
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
          <ContextChip
            kind={selection.kind}
            value={selectionLabel(selection)}
            action="clear the selection"
            selected
            onClear={onClearSelection}
          />
        )}
        {focus !== "" && (
          <ContextChip kind="focus" value={focus} action="clear the focus filter" onClear={onClearFocus} />
        )}
        {window !== null && (
          <ContextChip
            kind="window"
            value={windowRange(window)}
            action="clear the brushed time window"
            onClear={onClearWindow}
          />
        )}
      </div>
      {count > 1 && (
        <ClearAll
          onClear={() => {
            if (selection !== null) onClearSelection();
            if (focus !== "") onClearFocus();
            if (window !== null) onClearWindow();
          }}
        />
      )}
    </section>
  );
}
