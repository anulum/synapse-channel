// @vitest-environment jsdom
// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — transient cockpit overlay controller tests

import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useCockpitOverlays } from "../../src/hooks/useCockpitOverlays";
import type { DashboardCapabilities } from "../../src/lib/access";

const OPERATOR: DashboardCapabilities = {
  read: true,
  message_send: true,
  task_declare: true,
  task_update: true,
};
const VIEWER: DashboardCapabilities = {
  read: true,
  message_send: false,
  task_declare: false,
  task_update: false,
};

interface OverlayProps {
  readonly blocked: boolean;
  readonly authBlocked: boolean;
  readonly capabilities: DashboardCapabilities;
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("useCockpitOverlays", () => {
  it("coordinates keyboard entry, inspection and palette commands", () => {
    const setSelection = vi.fn();
    const setFocus = vi.fn();
    const toggleTheme = vi.fn();
    const toggleDensity = vi.fn();
    const toggleTravel = vi.fn();
    const { result } = renderHook(() => useCockpitOverlays({
      blocked: false,
      authBlocked: false,
      capabilities: OPERATOR,
      accessChangedMessage: "access changed",
      agents: ["alpha/one"],
      tasks: ["TASK-1"],
      setSelection,
      setFocus,
      toggleTheme,
      toggleDensity,
      toggleTravel,
    }));

    act(() => window.dispatchEvent(new KeyboardEvent("keydown", { key: "k", ctrlKey: true })));
    expect(result.current.paletteOpen).toBe(true);
    act(() => window.dispatchEvent(new KeyboardEvent("keydown", { key: "?" })));
    expect(result.current.paletteOpen).toBe(false);
    expect(result.current.guideOpen).toBe(true);
    act(() => window.dispatchEvent(new KeyboardEvent("keydown", { key: "k", metaKey: true })));
    expect(result.current.paletteOpen).toBe(true);
    expect(result.current.guideOpen).toBe(false);

    act(() => result.current.inspectAgent("alpha/one"));
    expect(setSelection).toHaveBeenCalledWith({ kind: "agent", id: "alpha/one" });
    expect(result.current.inspected).toEqual({ kind: "agent", id: "alpha/one" });
    act(() => result.current.messagePeer("alpha/one"));
    expect(result.current.paletteCompose).toEqual({ to: "alpha/one", nonce: 1 });
    act(() => result.current.messagePeer("alpha/one"));
    expect(result.current.paletteCompose).toEqual({ to: "alpha/one", nonce: 2 });

    for (const id of [
      "focus:alpha/one",
      "agent:alpha/one",
      "task:TASK-1",
      "trace:TASK-1",
      "toggle-theme",
      "toggle-density",
      "toggle-travel",
      "clear-focus",
    ]) {
      const command = result.current.commands.find((candidate) => candidate.id === id);
      expect(command).toBeDefined();
      if (command !== undefined) act(() => result.current.runCommand(command));
    }
    expect(setFocus).toHaveBeenCalledWith("alpha/one");
    expect(setFocus).toHaveBeenCalledWith("");
    expect(setSelection).toHaveBeenCalledWith({ kind: "task", id: "TASK-1" });
    expect(result.current.traceRequest).toEqual({ subject: "TASK-1", nonce: 1 });
    expect(toggleTheme).toHaveBeenCalledOnce();
    expect(toggleDensity).toHaveBeenCalledOnce();
    expect(toggleTravel).toHaveBeenCalledOnce();
    const operatorCommand = result.current.commands.find(
      (command) => command.id === "operator-message",
    );
    expect(operatorCommand).toBeDefined();
    if (operatorCommand !== undefined) act(() => result.current.runCommand(operatorCommand));

    const input = document.createElement("input");
    document.body.append(input);
    act(() => input.dispatchEvent(new KeyboardEvent("keydown", { key: "?", bubbles: true })));
    expect(result.current.guideOpen).toBe(false);
    input.remove();

    const guideButton = document.createElement("button");
    const setupButton = document.createElement("button");
    document.body.append(guideButton, setupButton);
    Object.defineProperty(result.current.guideTrigger, "current", {
      configurable: true,
      value: guideButton,
    });
    Object.defineProperty(result.current.setupTrigger, "current", {
      configurable: true,
      value: setupButton,
    });
    act(() => result.current.openGuide());
    act(() => result.current.closeGuide());
    expect(document.activeElement).toBe(guideButton);
    act(() => result.current.openSetup());
    act(() => result.current.closeSetup());
    expect(document.activeElement).toBe(setupButton);
    act(() => result.current.closePalette());
    act(() => result.current.closeInspection());
    expect(result.current.inspected).toBeNull();
    guideButton.remove();
    setupButton.remove();
  });

  it("closes write surfaces and restores focus when capabilities are removed", () => {
    const options = {
      accessChangedMessage: "access changed",
      agents: [] as readonly string[],
      tasks: [] as readonly string[],
      setSelection: vi.fn(),
      setFocus: vi.fn(),
      toggleTheme: vi.fn(),
      toggleDensity: vi.fn(),
      toggleTravel: vi.fn(),
    };
    const { result, rerender } = renderHook(
      ({ blocked, authBlocked, capabilities }: OverlayProps) => useCockpitOverlays({
        blocked,
        authBlocked,
        capabilities,
        ...options,
      }),
      { initialProps: { blocked: false, authBlocked: false, capabilities: OPERATOR } },
    );
    const commandButton = document.createElement("button");
    document.body.append(commandButton);
    Object.defineProperty(result.current.commandTrigger, "current", {
      configurable: true,
      value: commandButton,
    });
    act(() => result.current.openPalette());
    rerender({ blocked: false, authBlocked: false, capabilities: VIEWER });
    expect(result.current.paletteOpen).toBe(false);
    expect(result.current.accessNotice).toBe("access changed");
    expect(document.activeElement).toBe(commandButton);

    act(() => result.current.openSetup());
    act(() => result.current.inspectTask("TASK-2"));
    rerender({ blocked: true, authBlocked: true, capabilities: VIEWER });
    expect(result.current.setupOpen).toBe(false);
    expect(result.current.inspected).toBeNull();
    expect(result.current.traceRequest).toBeUndefined();
    act(() => window.dispatchEvent(new KeyboardEvent("keydown", { key: "k", ctrlKey: true })));
    expect(result.current.paletteOpen).toBe(false);
    commandButton.remove();
  });
});
