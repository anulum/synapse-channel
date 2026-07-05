// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — theme selection ladder tests

import { describe, expect, it } from "vitest";
import {
  applyTheme,
  persistTheme,
  resolveInitialTheme,
  THEME_STORAGE_KEY,
  toggledTheme,
  type ThemeStorage,
} from "../src/lib/theme";

function memoryStorage(initial: Record<string, string> = {}): ThemeStorage & {
  readonly store: Record<string, string>;
} {
  const store = { ...initial };
  return {
    store,
    getItem: (key) => store[key] ?? null,
    setItem: (key, value) => {
      store[key] = value;
    },
  };
}

const THROWING: ThemeStorage = {
  getItem() {
    throw new Error("denied");
  },
  setItem() {
    throw new Error("denied");
  },
};

describe("resolveInitialTheme", () => {
  it("prefers the stored explicit choice over the OS preference", () => {
    expect(resolveInitialTheme(memoryStorage({ [THEME_STORAGE_KEY]: "light" }), false)).toBe("light");
    expect(resolveInitialTheme(memoryStorage({ [THEME_STORAGE_KEY]: "dark" }), true)).toBe("dark");
  });

  it("follows the media query when nothing valid is stored, defaulting dark", () => {
    expect(resolveInitialTheme(memoryStorage(), true)).toBe("light");
    expect(resolveInitialTheme(memoryStorage(), false)).toBe("dark");
    expect(resolveInitialTheme(memoryStorage({ [THEME_STORAGE_KEY]: "sepia" }), false)).toBe("dark");
  });

  it("treats a throwing storage as no stored choice", () => {
    expect(resolveInitialTheme(THROWING, true)).toBe("light");
  });
});

describe("toggledTheme", () => {
  it("flips between the two palettes", () => {
    expect(toggledTheme("dark")).toBe("light");
    expect(toggledTheme("light")).toBe("dark");
  });
});

describe("applyTheme", () => {
  it("sets the light attribute and clears it for dark", () => {
    const attrs = new Map<string, string>();
    const root = {
      setAttribute: (name: string, value: string) => attrs.set(name, value),
      removeAttribute: (name: string) => attrs.delete(name),
    } as unknown as HTMLElement;
    applyTheme("light", root);
    expect(attrs.get("data-theme")).toBe("light");
    applyTheme("dark", root);
    expect(attrs.has("data-theme")).toBe(false);
  });
});

describe("persistTheme", () => {
  it("stores the explicit choice and survives a throwing storage", () => {
    const storage = memoryStorage();
    persistTheme("light", storage);
    expect(storage.store[THEME_STORAGE_KEY]).toBe("light");
    expect(() => persistTheme("dark", THROWING)).not.toThrow();
  });
});
