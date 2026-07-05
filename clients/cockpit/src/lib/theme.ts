// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — theme selection: stored choice first, OS preference second

// The instrument is graphite by design, but not everyone reads dark. The
// selection ladder is deliberate: an explicit choice (localStorage) always
// wins; with no choice recorded, the OS's prefers-color-scheme decides; the
// fallback is dark. Applying a theme is one attribute on the root element —
// every colour in the cockpit, canvas and SVG included, resolves through the
// canonical tokens that attribute switches.

/** The two instrument palettes. */
export type Theme = "dark" | "light";

/** Where the explicit choice persists. */
export const THEME_STORAGE_KEY = "cockpit-theme";

/** A storage shim — localStorage's shape, injectable for tests. */
export interface ThemeStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

/**
 * Resolve the theme to start with: the stored explicit choice when present
 * and valid, otherwise the OS preference, otherwise dark. A storage that
 * throws (private mode, denied access) counts as no stored choice.
 */
export function resolveInitialTheme(storage: ThemeStorage, prefersLight: boolean): Theme {
  let stored: string | null = null;
  try {
    stored = storage.getItem(THEME_STORAGE_KEY);
  } catch {
    stored = null;
  }
  if (stored === "dark" || stored === "light") return stored;
  return prefersLight ? "light" : "dark";
}

/** The other theme — what the toggle switches to. */
export function toggledTheme(theme: Theme): Theme {
  return theme === "dark" ? "light" : "dark";
}

/**
 * Apply a theme to the root element. Dark is the bare root (the tokens'
 * home values); light is the `data-theme="light"` override block.
 */
export function applyTheme(theme: Theme, root: HTMLElement): void {
  if (theme === "light") root.setAttribute("data-theme", "light");
  else root.removeAttribute("data-theme");
}

/** Persist an explicit choice; a throwing storage loses only persistence. */
export function persistTheme(theme: Theme, storage: ThemeStorage): void {
  try {
    storage.setItem(THEME_STORAGE_KEY, theme);
  } catch {
    // Session-only theming is acceptable; the choice simply resets next open.
  }
}
