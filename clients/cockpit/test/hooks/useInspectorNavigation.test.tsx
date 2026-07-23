// @vitest-environment jsdom
// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — inspector focus and trace navigation contracts

import { act, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { useInspectorNavigation } from "../../src/hooks/useInspectorNavigation";

function keyEvent(key: string): React.KeyboardEvent<HTMLButtonElement> {
  return { key, preventDefault: vi.fn() } as unknown as React.KeyboardEvent<HTMLButtonElement>;
}

describe("useInspectorNavigation", () => {
  it("traces repeated subjects and adopts external requests", () => {
    const onTabChange = vi.fn();
    const onSelectionChange = vi.fn();
    const { result, rerender } = renderHook(
      ({ traceRequest }) => useInspectorNavigation({ onTabChange, onSelectionChange, traceRequest }),
      { initialProps: { traceRequest: undefined as { subject: string; nonce: number } | undefined } },
    );
    act(() => result.current.onSelectTask("task-1"));
    expect(result.current.prefill).toEqual({ subject: "task-1", nonce: 1 });
    act(() => result.current.onSelectTask("task-1"));
    expect(result.current.prefill).toEqual({ subject: "task-1", nonce: 2 });
    expect(onSelectionChange).toHaveBeenLastCalledWith({ kind: "task", id: "task-1" });
    expect(onTabChange).toHaveBeenLastCalledWith("causality");
    rerender({ traceRequest: { subject: "task-2", nonce: 1 } });
    expect(result.current.prefill).toEqual({ subject: "task-2", nonce: 3 });
  });

  it("moves roving focus for arrows and boundary keys and ignores other keys", () => {
    const onTabChange = vi.fn();
    const { result } = renderHook(() => useInspectorNavigation({ onTabChange }));
    const focus = vi.fn();
    result.current.tabRefs.current[2] = { focus } as unknown as HTMLButtonElement;
    const right = keyEvent("ArrowRight");
    act(() => result.current.onTabKeyDown(right, 1));
    expect(onTabChange).toHaveBeenLastCalledWith("fleet");
    expect(focus).toHaveBeenCalled();
    expect(right.preventDefault).toHaveBeenCalled();

    for (const [key, index, expected] of [
      ["ArrowLeft", 0, "causality"], ["Home", 4, "attention"], ["End", 0, "causality"],
    ] as const) {
      act(() => result.current.onTabKeyDown(keyEvent(key), index));
      expect(onTabChange).toHaveBeenLastCalledWith(expected);
    }
    const ignored = keyEvent("Enter");
    act(() => result.current.onTabKeyDown(ignored, 0));
    expect(ignored.preventDefault).not.toHaveBeenCalled();
  });
});
