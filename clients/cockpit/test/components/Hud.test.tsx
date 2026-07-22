// @vitest-environment jsdom
// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — HUD behaviour tests

import { cleanup, render, screen } from "@testing-library/react";
import { createRef } from "react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Hud, type Kpi } from "../../src/components/Hud";

afterEach(cleanup);

const KPIS: readonly Kpi[] = [
  { label: "agents online", value: 6, delta: 1 },
  { label: "claims held", value: 4, delta: -2 },
  { label: "risk signals", value: 0, delta: 0 },
];

describe("Hud", () => {
  it("codes each delta redundantly — arrow, sign, and class, never colour alone", () => {
    render(<Hud kpis={KPIS} live stamp="12:00:00" />);
    expect(screen.getByText("▲ +1").className).toContain("kpi__delta--up");
    expect(screen.getByText("▼ -2").className).toContain("kpi__delta--down");
    expect(screen.getByText("• 0").className).toContain("kpi__delta--flat");
    expect(screen.getByText("live")).toBeTruthy();
    expect(screen.getByText("12:00:00")).toBeTruthy();
  });

  it("says stale when the feed is stale", () => {
    render(<Hud kpis={[]} live={false} stamp="—" />);
    expect(screen.getByText("stale")).toBeTruthy();
  });

  it.each([
    ["live", "stream"],
    ["fallback", "poll fallback"],
    ["unsupported", "poll fallback"],
    ["gap", "gap detected"],
    ["reconnecting", "reconnecting"],
  ] as const)("shows the %s transport posture as evidence", (transport, label) => {
    render(<Hud kpis={[]} live={transport === "live"} stamp="—" transport={transport} />);
    expect(screen.getByLabelText(`Live transport: ${label}`).textContent).toBe(label);
  });

  it("drills a KPI down into the log filter only when a handler exists", async () => {
    const onSelect = vi.fn();
    render(<Hud kpis={KPIS} live stamp="12:00:00" onSelect={onSelect} />);
    await userEvent.click(screen.getByText("claims held"));
    expect(onSelect).toHaveBeenCalledWith("claims held");
    cleanup();
    render(<Hud kpis={KPIS} live stamp="12:00:00" />);
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("drives the focus lens through the picker and its clear affordance", async () => {
    const onFocusChange = vi.fn();
    render(
      <Hud
        kpis={[]}
        live
        stamp="—"
        focus="quantum/claude"
        onFocusChange={onFocusChange}
        rosterNames={["quantum/claude", "fusion/codex"]}
      />,
    );
    const input = screen.getByLabelText("Focus the claims and board on one identity");
    expect((input as HTMLInputElement).value).toBe("quantum/claude");
    expect(document.querySelectorAll("#hud-focus-roster option")).toHaveLength(2);
    await userEvent.click(screen.getByLabelText("Clear the focus lens"));
    expect(onFocusChange).toHaveBeenCalledWith("");
  });

  it("labels the theme and density toggles by what they switch TO", async () => {
    const onToggleTheme = vi.fn();
    const onToggleDensity = vi.fn();
    render(
      <Hud
        kpis={[]}
        live
        stamp="—"
        theme="dark"
        onToggleTheme={onToggleTheme}
        density="cozy"
        onToggleDensity={onToggleDensity}
      />,
    );
    await userEvent.click(screen.getByLabelText("Switch to light theme"));
    expect(onToggleTheme).toHaveBeenCalled();
    const density = screen.getByLabelText("Toggle display density");
    expect(density.textContent).toBe("compact");
    await userEvent.click(density);
    expect(onToggleDensity).toHaveBeenCalled();
    cleanup();
    render(
      <Hud kpis={[]} live stamp="—" theme="light" onToggleTheme={() => {}} density="compact" onToggleDensity={() => {}} />,
    );
    expect(screen.getByLabelText("Switch to dark theme").textContent).toBe("dark");
    expect(screen.getByLabelText("Toggle display density").textContent).toBe("cozy");
  });

  it("hosts the role control and exposes a focusable command trigger", async () => {
    const onOpen = vi.fn();
    const trigger = createRef<HTMLButtonElement>();
    render(
      <Hud
        kpis={[]}
        live
        stamp="—"
        accessControl={<span>viewer · review</span>}
        commandTriggerRef={trigger}
        onOpenPalette={onOpen}
      />,
    );
    expect(screen.getByText("viewer · review")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Open command palette" }));
    expect(onOpen).toHaveBeenCalledOnce();
    expect(trigger.current).toBeInstanceOf(HTMLButtonElement);
  });

  it("exposes the contextual guide and language chooser in the HUD", async () => {
    const onOpenGuide = vi.fn();
    const onOpenSetup = vi.fn();
    render(<Hud kpis={[]} live stamp="—" onOpenGuide={onOpenGuide} onOpenSetup={onOpenSetup} />);
    await userEvent.click(screen.getByRole("button", { name: "Open cockpit guide" }));
    expect(onOpenGuide).toHaveBeenCalledOnce();
    await userEvent.click(screen.getByRole("button", { name: "Open local setup assistant" }));
    expect(onOpenSetup).toHaveBeenCalledOnce();
    expect((screen.getByLabelText("Interface language") as HTMLSelectElement).value).toBe("en");
    expect(screen.getByRole("option", { name: "DE" })).toBeTruthy();
  });
});
