// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — small persisted preferences, storage failures shrugged off

// The theme module set the pattern: an explicit choice persists in
// localStorage, a throwing storage (private mode, denied access) costs only
// persistence, never the feature. Focus and density reuse it through this
// pair instead of re-growing their own try/catch.

import type { ThemeStorage } from "./theme";

/** Read a stored preference; a throwing storage reads as unset. */
export function readPref(storage: ThemeStorage, key: string): string | null {
  try {
    return storage.getItem(key);
  } catch {
    return null;
  }
}

/** Persist a preference; empty string clears it via the sentinel-free path. */
export function writePref(storage: ThemeStorage, key: string, value: string): void {
  try {
    storage.setItem(key, value);
  } catch {
    // Session-only preference; it simply resets on the next open.
  }
}
