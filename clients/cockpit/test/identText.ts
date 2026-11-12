// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — find an identifier element by its whole value in component tests

/**
 * A text matcher for Testing Library: the identifier element whose whole text
 * is `value`. An identifier is drawn as a head and a tail inside one element,
 * so a plain string query sees only one of the two parts.
 */
export function identText(value: string): (content: string, element: Element | null) => boolean {
  return (_content, element) =>
    element !== null && element.classList.contains("ident") && element.textContent === value;
}
