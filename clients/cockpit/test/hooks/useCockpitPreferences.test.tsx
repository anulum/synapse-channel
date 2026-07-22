// @vitest-environment jsdom
// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — cockpit preference and shareable-log hook tests

import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useCockpitPreferences } from "../../src/hooks/useCockpitPreferences";
import { OPEN_QUERY } from "../../src/lib/logQuery";

beforeEach(() => {
  localStorage.clear();
  history.replaceState({ retained: true }, "", "/cockpit/?panel=fleet#q=claim&order=oldest");
  vi.stubGlobal(
    "matchMedia",
    vi.fn().mockReturnValue({ matches: false }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
  document.documentElement.removeAttribute("data-density");
  document.documentElement.removeAttribute("data-theme");
  history.replaceState(null, "", "/");
});

describe("useCockpitPreferences", () => {
  it("restores persisted choices and keeps log filters in the current URL", () => {
    localStorage.setItem("cockpit-focus", "alpha");
    localStorage.setItem("cockpit-density", "compact");
    localStorage.setItem("cockpit-theme", "light");
    const { result } = renderHook(() => useCockpitPreferences());

    expect(result.current.focus).toBe("alpha");
    expect(result.current.density).toBe("compact");
    expect(result.current.theme).toBe("light");
    expect(result.current.logQuery.text).toBe("claim");
    expect(result.current.logQuery.order).toBe("oldest");
    expect(document.documentElement.getAttribute("data-density")).toBe("compact");
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");

    act(() => result.current.setFocus("beta"));
    act(() => result.current.toggleDensity());
    act(() => result.current.toggleTheme());
    act(() => result.current.setLogQuery({
      text: "route",
      kinds: ["chat"],
      order: "newest",
      view: "compact",
    }));

    expect(localStorage.getItem("cockpit-focus")).toBe("beta");
    expect(localStorage.getItem("cockpit-density")).toBe("cozy");
    expect(localStorage.getItem("cockpit-theme")).toBe("dark");
    expect(document.documentElement.hasAttribute("data-density")).toBe(false);
    expect(document.documentElement.hasAttribute("data-theme")).toBe(false);
    expect(location.pathname).toBe("/cockpit/");
    expect(location.search).toBe("?panel=fleet");
    expect(location.hash).toBe("#q=route&kinds=chat&view=compact");
    expect(history.state).toEqual({ retained: true });

    act(() => result.current.setLogQuery(OPEN_QUERY));
    expect(location.hash).toBe("");
  });

  it("uses the operating-system theme when no explicit choice exists", () => {
    vi.stubGlobal("matchMedia", vi.fn().mockReturnValue({ matches: true }));
    const { result } = renderHook(() => useCockpitPreferences());
    expect(result.current.theme).toBe("light");
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
    expect(result.current.density).toBe("cozy");
    act(() => result.current.toggleDensity());
    expect(result.current.density).toBe("compact");
  });

  it("starts with an open log query when no browser location exists", () => {
    vi.stubGlobal("location", undefined);
    const { result } = renderHook(() => useCockpitPreferences());
    expect(result.current.logQuery).toEqual(OPEN_QUERY);
  });
});
