// @vitest-environment jsdom
// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — contextual guide drawer behaviour tests

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { GuideDrawer } from "../../src/components/GuideDrawer";
import { CockpitI18nProvider } from "../../src/context/CockpitI18n";

beforeEach(() => history.replaceState(null, "", "/cockpit/?lang=en"));
afterEach(() => {
  cleanup();
  localStorage.clear();
});

function renderDrawer(onClose = vi.fn()): ReturnType<typeof vi.fn> {
  render(
    <CockpitI18nProvider>
      <GuideDrawer open activePanel="audit" onClose={onClose} />
    </CockpitI18nProvider>,
  );
  return onClose;
}

describe("GuideDrawer", () => {
  it("renders nothing while closed", () => {
    render(<CockpitI18nProvider><GuideDrawer open={false} activePanel="log" onClose={() => {}} /></CockpitI18nProvider>);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("opens on the current context, focuses search and filters locally", async () => {
    renderDrawer();
    expect(screen.getByRole("heading", { name: "Cockpit guide" })).toBeTruthy();
    expect(screen.getByText("Audit")).toBeTruthy();
    const search = screen.getByLabelText("Search the cockpit guide");
    expect(document.activeElement).toBe(search);
    await userEvent.type(search, "keyboard");
    expect(screen.getByText("Keyboard and accessibility")).toBeTruthy();
    expect(screen.queryByText("Audit")).toBeNull();
    await userEvent.clear(search);
    await userEvent.type(search, "never transmitted phrase");
    expect(screen.getByText("No guide topic matches this search.")).toBeTruthy();
  });

  it("switches catalogue while preserving protocol tokens", async () => {
    renderDrawer();
    await userEvent.selectOptions(screen.getByLabelText("Interface language"), "sk");
    expect(screen.getByRole("heading", { name: "Príručka cockpit-u" })).toBeTruthy();
    expect(location.search).toContain("lang=sk");
    expect(screen.getByText(/Transport acknowledgement/u)).toBeTruthy();
  });

  it("routes operators into the read-only setup assistant", async () => {
    const onOpenSetup = vi.fn();
    render(
      <CockpitI18nProvider>
        <GuideDrawer open activePanel="audit" onClose={() => {}} onOpenSetup={onOpenSetup} />
      </CockpitI18nProvider>,
    );
    expect(screen.getByText("Prepare a local setup")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Open the read-only setup assistant" }));
    expect(onOpenSetup).toHaveBeenCalledOnce();
  });

  it("closes from Escape, the close control, and only the veil itself", async () => {
    const onClose = renderDrawer();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
    await userEvent.click(screen.getByLabelText("Close cockpit guide"));
    expect(onClose).toHaveBeenCalledTimes(2);
    const veil = document.querySelector(".guide-veil");
    expect(veil).not.toBeNull();
    fireEvent.mouseDown(veil as Element);
    expect(onClose).toHaveBeenCalledTimes(3);
    fireEvent.mouseDown(screen.getByRole("dialog"));
    expect(onClose).toHaveBeenCalledTimes(3);
  });

  it("traps forward and reverse keyboard focus inside the modal", () => {
    renderDrawer();
    const close = screen.getByLabelText("Close cockpit guide");
    const summaries = document.querySelectorAll("summary");
    const last = summaries.item(summaries.length - 1) as HTMLElement;
    last.focus();
    fireEvent.keyDown(window, { key: "Tab" });
    expect(document.activeElement).toBe(close);
    fireEvent.keyDown(window, { key: "Tab", shiftKey: true });
    expect(document.activeElement).toBe(last);
  });
});
