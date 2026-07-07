// @vitest-environment jsdom
// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — toast stack behaviour tests

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ToastStack } from "../../src/components/ToastStack";
import type { Toast } from "../../src/lib/toasts";

afterEach(cleanup);

function toast(id: string, severity: Toast["severity"], text: string): Toast {
  return { id, severity, text };
}

describe("ToastStack", () => {
  it("renders nothing at all when there are no toasts", () => {
    const { container } = render(<ToastStack toasts={[]} onDismiss={() => {}} />);
    expect(container.innerHTML).toBe("");
  });

  it("announces politely, glyphs each severity, and dismisses on click", async () => {
    const onDismiss = vi.fn();
    render(
      <ToastStack
        toasts={[
          toast("a", "crit", "epoch drifted"),
          toast("b", "warn", "task t-1 blocked"),
          toast("c", "ok", "task t-2 done"),
        ]}
        onDismiss={onDismiss}
      />,
    );
    const region = screen.getByRole("status");
    expect(region.getAttribute("aria-live")).toBe("polite");
    expect(screen.getByText("epoch drifted").textContent).toContain("●");
    expect(screen.getByText("task t-1 blocked").textContent).toContain("!");
    expect(screen.getByText("task t-2 done").textContent).toContain("✓");
    await userEvent.click(screen.getByText("task t-1 blocked"));
    expect(onDismiss).toHaveBeenCalledWith("b");
  });

  it("caps at four shown and states the queue honestly", () => {
    render(
      <ToastStack
        toasts={["a", "b", "c", "d", "e", "f"].map((id) => toast(id, "ok", `toast ${id}`))}
        onDismiss={() => {}}
      />,
    );
    expect(screen.getAllByRole("button")).toHaveLength(4);
    expect(screen.getByText("+2 more")).toBeTruthy();
  });
});
