// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — an identifier that keeps both ends when its column is narrow

import type { ClipboardEvent, JSX } from "react";

import { splitIdentifier } from "../lib/identifier";

interface IdentProps {
  /** The whole identifier: a seat, a task, a path. */
  readonly value: string;
  /** The place's own class, for its colour, weight and share of the row. */
  readonly className?: string | undefined;
  /**
   * Let the identifier itself take keyboard focus. Set it only where the row
   * has no other focus target; elsewhere the row's own control shows the value.
   */
  readonly focusable?: boolean | undefined;
}

/**
 * One element whose text is the whole identifier, drawn as a head that gives
 * way to an ellipsis and a tail that is never cut. The whole value is the
 * title, is shown in full under keyboard focus, and is selected as one piece.
 * A focusable identifier answers the copy key with the whole value.
 */
export function Ident({ value, className, focusable = false }: IdentProps): JSX.Element {
  const { head, tail } = splitIdentifier(value);
  const copyWhole = (event: ClipboardEvent<HTMLSpanElement>): void => {
    const selection = window.getSelection();
    // A selection the reader made is theirs to copy.
    if (selection !== null && !selection.isCollapsed) return;
    event.clipboardData.setData("text/plain", value);
    event.preventDefault();
  };
  return (
    <span
      className={className === undefined ? "ident" : `ident ${className}`}
      title={value}
      {...(focusable ? { tabIndex: 0, onCopy: copyWhole } : {})}
    >
      <span className="ident__head">{head}</span>
      {tail !== "" && <span className="ident__tail">{tail}</span>}
    </span>
  );
}
