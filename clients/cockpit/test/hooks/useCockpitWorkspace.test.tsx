// @vitest-environment jsdom
// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE CHANNEL — cockpit workspace browser-history tests

import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useCockpitWorkspace } from "../../src/hooks/useCockpitWorkspace";

beforeEach(() => {
  history.replaceState({ retained: true }, "", "/cockpit/?panel=fleet&fleet=matrix&from=a&to=b#q=claim");
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  history.replaceState(null, "", "/");
});

describe("useCockpitWorkspace", () => {
  it("initialises from the URL and pushes navigable changes without losing the hash", () => {
    const remove = vi.spyOn(window, "removeEventListener");
    const { result, unmount } = renderHook(() => useCockpitWorkspace());
    expect(result.current.workspace).toEqual({
      panel: "fleet",
      fleetView: "matrix",
      selection: { kind: "route", source: "a", target: "b" },
    });

    act(() => result.current.setFleetView("projects"));
    expect(location.search).toBe("?panel=fleet&fleet=projects&from=a&to=b");
    expect(location.hash).toBe("#q=claim");
    expect(history.state).toEqual({ retained: true });

    act(() => result.current.setFleetSelection({ kind: "agent", id: "alpha/one" }));
    expect(location.search).toBe("?panel=fleet&fleet=projects&agent=alpha%2Fone");

    act(() => result.current.setPanel("audit"));
    expect(location.search).toBe("?panel=audit&agent=alpha%2Fone");
    expect(result.current.workspace.selection).toEqual({ kind: "agent", id: "alpha/one" });

    act(() => result.current.setSelection({ kind: "task", id: "SCH-17" }));
    expect(location.search).toBe("?panel=audit&task=SCH-17");

    act(() => result.current.setPanelSelection("causality", { kind: "task", id: "SCH-18" }));
    expect(location.search).toBe("?panel=causality&task=SCH-18");

    unmount();
    expect(remove).toHaveBeenCalledWith("popstate", expect.any(Function));
  });

  it("restores an external history location on popstate", () => {
    const { result } = renderHook(() => useCockpitWorkspace());
    act(() => {
      history.pushState(history.state, "", "/cockpit/?panel=metrics&event=17#q=claim");
      window.dispatchEvent(new PopStateEvent("popstate", { state: history.state }));
    });
    expect(result.current.workspace).toEqual({
      panel: "metrics",
      fleetView: "web",
      selection: { kind: "event", seq: 17 },
    });
  });

  it("does not push a duplicate location", () => {
    const push = vi.spyOn(history, "pushState");
    const { result } = renderHook(() => useCockpitWorkspace());
    act(() => result.current.setPanel("fleet"));
    expect(push).not.toHaveBeenCalled();
  });

  it("initialises safely when a server render has no location", () => {
    vi.stubGlobal("location", undefined);
    const { result } = renderHook(() => useCockpitWorkspace());
    expect(result.current.workspace).toEqual({
      panel: "log",
      fleetView: "web",
      selection: null,
    });
  });
});
