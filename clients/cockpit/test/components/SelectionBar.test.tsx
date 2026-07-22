// @vitest-environment jsdom
// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE CHANNEL — visible cockpit context control tests

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SelectionBar } from "../../src/components/SelectionBar";

afterEach(cleanup);

describe("SelectionBar", () => {
  it("stays absent when no selection or filter is active", () => {
    const { container } = render(
      <SelectionBar
        selection={null}
        focus=""
        window={null}
        onClearSelection={vi.fn()}
        onClearFocus={vi.fn()}
        onClearWindow={vi.fn()}
      />,
    );
    expect(container.childElementCount).toBe(0);
  });

  it("names each active context and clears one chip directly", async () => {
    const onClearSelection = vi.fn();
    render(
      <SelectionBar
        selection={{ kind: "route", source: "alpha", target: "beta" }}
        focus="SYNAPSE-CHANNEL/alpha"
        window={{ fromTs: 100, toTs: 120 }}
        onClearSelection={onClearSelection}
        onClearFocus={vi.fn()}
        onClearWindow={vi.fn()}
      />,
    );
    expect(screen.getByText("alpha → beta")).toBeTruthy();
    expect(screen.getByText("SYNAPSE-CHANNEL/alpha")).toBeTruthy();
    expect(screen.getByRole("region").getAttribute("aria-label")).toContain("selection and filters");
    await userEvent.click(screen.getByRole("button", { name: "Clear selected route alpha → beta" }));
    expect(onClearSelection).toHaveBeenCalledOnce();
  });

  it("clears every active context with one operator action", async () => {
    const onClearSelection = vi.fn();
    const onClearFocus = vi.fn();
    const onClearWindow = vi.fn();
    render(
      <SelectionBar
        selection={{ kind: "task", id: "SCH-17" }}
        focus="alpha"
        window={{ fromTs: 100, toTs: 120 }}
        onClearSelection={onClearSelection}
        onClearFocus={onClearFocus}
        onClearWindow={onClearWindow}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "clear all" }));
    expect(onClearSelection).toHaveBeenCalledOnce();
    expect(onClearFocus).toHaveBeenCalledOnce();
    expect(onClearWindow).toHaveBeenCalledOnce();
  });

  it.each([
    {
      selection: { kind: "task", id: "SCH-17" } as const,
      focus: "alpha",
      window: null,
      called: [true, true, false],
    },
    {
      selection: { kind: "event", seq: 17 } as const,
      focus: "",
      window: { fromTs: 100, toTs: 120 },
      called: [true, false, true],
    },
    {
      selection: null,
      focus: "alpha",
      window: { fromTs: 100, toTs: 120 },
      called: [false, true, true],
    },
  ])("clears only the active pair in a mixed context", async ({ selection, focus, window, called }) => {
    const onClearSelection = vi.fn();
    const onClearFocus = vi.fn();
    const onClearWindow = vi.fn();
    render(
      <SelectionBar
        selection={selection}
        focus={focus}
        window={window}
        onClearSelection={onClearSelection}
        onClearFocus={onClearFocus}
        onClearWindow={onClearWindow}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "clear all" }));
    expect([
      onClearSelection.mock.calls.length > 0,
      onClearFocus.mock.calls.length > 0,
      onClearWindow.mock.calls.length > 0,
    ]).toEqual(called);
  });

  it("shows a single context without a redundant clear-all action", () => {
    render(
      <SelectionBar
        selection={{ kind: "agent", id: "alpha" }}
        focus=""
        window={null}
        onClearSelection={vi.fn()}
        onClearFocus={vi.fn()}
        onClearWindow={vi.fn()}
      />,
    );
    expect(screen.queryByRole("button", { name: "clear all" })).toBeNull();
  });
});
