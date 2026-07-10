// @vitest-environment jsdom
// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — mobile nav, install chip, and panel boundary behaviour tests

import type { JSX } from "react";
import { act, cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { InstallChip } from "../../src/components/InstallChip";
import { MobileNav, MOBILE_SEGMENTS } from "../../src/components/MobileNav";
import { PanelBoundary } from "../../src/components/PanelBoundary";

afterEach(cleanup);

describe("MobileNav", () => {
  it("shows all five segments, presses the active one, and selects on tap", async () => {
    const onSelect = vi.fn();
    render(<MobileNav active="claims" onSelect={onSelect} />);
    expect(screen.getAllByRole("button")).toHaveLength(MOBILE_SEGMENTS.length);
    expect(screen.getByText("claims").getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByText("board").getAttribute("aria-pressed")).toBe("false");
    await userEvent.click(screen.getByText("roster"));
    expect(onSelect).toHaveBeenCalledWith("roster");
  });
});

describe("InstallChip", () => {
  it("renders nothing until the browser offers an install prompt", () => {
    const { container } = render(<InstallChip />);
    expect(container.innerHTML).toBe("");
  });

  it("appears on beforeinstallprompt and hands over to the browser dialog once", async () => {
    render(<InstallChip />);
    const prompt = vi.fn().mockResolvedValue(undefined);
    act(() => {
      const event = new Event("beforeinstallprompt", { cancelable: true });
      (event as Event & { prompt: () => Promise<void> }).prompt = prompt;
      window.dispatchEvent(event);
    });
    const chip = screen.getByText("add to home screen");
    await userEvent.click(chip);
    expect(prompt).toHaveBeenCalledTimes(1);
    expect(screen.queryByText("add to home screen")).toBeNull();
  });
});

function Bomb(): JSX.Element {
  throw new Error("gauge exploded");
}

describe("PanelBoundary", () => {
  it("renders its child while healthy", () => {
    render(
      <PanelBoundary name="Claims">
        <p>all claims</p>
      </PanelBoundary>,
    );
    expect(screen.getByText("all claims")).toBeTruthy();
  });

  it("replaces only the failed panel with an honest named fallback", () => {
    const silenced = vi.spyOn(console, "error").mockImplementation(() => {});
    render(
      <>
        <PanelBoundary name="Claims">
          <Bomb />
        </PanelBoundary>
        <PanelBoundary name="Board">
          <p>board still flying</p>
        </PanelBoundary>
      </>,
    );
    silenced.mockRestore();
    expect(screen.getByLabelText("Claims (failed)")).toBeTruthy();
    expect(screen.getByText(/This panel failed to render: gauge exploded/)).toBeTruthy();
    expect(screen.getByText("board still flying")).toBeTruthy();
  });
});
