// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — order risk signals worst-first for the triage rail

import type { RiskSignal } from "../types";

/** Worst-first rank per level; doubles as the rail's sort priority. */
const LEVEL_RANK: Record<RiskSignal["level"], number> = {
  red: 0,
  amber: 1,
  green: 2,
};

/**
 * Order risk signals for triage: red before amber before green, then by
 * category and subject so equal-severity rows keep a stable, scannable order.
 * The hub's own ordering is preserved semantically — only presentation order
 * changes, never content.
 */
export function orderSignals(signals: readonly RiskSignal[]): RiskSignal[] {
  return [...signals].sort(
    (a, b) =>
      LEVEL_RANK[a.level] - LEVEL_RANK[b.level] ||
      a.category.localeCompare(b.category) ||
      a.subject.localeCompare(b.subject),
  );
}
