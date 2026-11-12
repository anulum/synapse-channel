// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — split an identifier into the part that may be cut and the end that may not

// A seat, a task or a path is told from its neighbours by its end:
// `…/autonomous-operations-seat-api` and `…/autonomous-operations-seat-web`
// differ in their last four characters. A column that cuts at the end hides
// exactly that. This module names the end, so that a component can keep it.

/** A value this short is shown whole; it has no separate end. */
export const IDENT_WHOLE_MAX = 8;
/** The shortest end that tells two identifiers apart, separator included. */
export const IDENT_TAIL_MIN = 4;
/** The longest end kept whole; a longer last segment is cut inside. */
export const IDENT_TAIL_MAX = 16;
/** The end kept when no separator bounds a usable one. */
export const IDENT_TAIL_FALLBACK = 6;

const SEPARATORS = "/-_:.";

export interface IdentifierParts {
  /** The leading part, which may give way to an ellipsis. */
  readonly head: string;
  /** The distinguishing end, which is never cut; empty for a short value. */
  readonly tail: string;
}

/**
 * Split `value` so that `head + tail === value`. The tail is the shortest end
 * that starts at a separator and holds at least `IDENT_TAIL_MIN` and at most
 * `IDENT_TAIL_MAX` characters; without one it is the last
 * `IDENT_TAIL_FALLBACK` characters.
 */
export function splitIdentifier(value: string): IdentifierParts {
  if (value.length <= IDENT_WHOLE_MAX) return { head: value, tail: "" };
  for (let index = value.length - 1; index > 0; index -= 1) {
    const tailLength = value.length - index;
    if (tailLength > IDENT_TAIL_MAX) break;
    if (tailLength >= IDENT_TAIL_MIN && SEPARATORS.includes(value.charAt(index))) {
      return { head: value.slice(0, index), tail: value.slice(index) };
    }
  }
  const cut = value.length - IDENT_TAIL_FALLBACK;
  return { head: value.slice(0, cut), tail: value.slice(cut) };
}
