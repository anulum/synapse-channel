// @vitest-environment jsdom
// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — inspector tab-switch behaviour tests

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState, type JSX } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { InspectorTabs } from "../../src/components/InspectorTabs";
import type { CockpitEvent } from "../../src/types";
import type { FleetSelection, FleetView, InspectorTab } from "../../src/lib/workspace";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const EVENTS: readonly CockpitEvent[] = [
  {
    seq: 7,
    ts: 1_751_800_007,
    kind: "claim",
    lane: "claims",
    severity: 0.4,
    actor: "quantum/worker",
    label: "claimed t-7",
    taskId: "t-7",
  },
];

type HarnessProps = Omit<
  Parameters<typeof InspectorTabs>[0],
  | "tab"
  | "onTabChange"
  | "fleetView"
  | "onFleetViewChange"
  | "fleetSelection"
  | "onFleetSelectionChange"
>;

function InspectorHarness(props: HarnessProps): JSX.Element {
  const [tab, setTab] = useState<InspectorTab>("log");
  const [fleetView, setFleetView] = useState<FleetView>("web");
  const [fleetSelection, setFleetSelection] = useState<FleetSelection | null>(null);
  return (
    <InspectorTabs
      {...props}
      tab={tab}
      onTabChange={setTab}
      fleetView={fleetView}
      onFleetViewChange={setFleetView}
      fleetSelection={fleetSelection}
      onFleetSelectionChange={setFleetSelection}
    />
  );
}

describe("InspectorTabs", () => {
  it("starts on the log and switches to each tab", async () => {
    render(
      <InspectorHarness
        events={EVENTS}
        connected
        receipts={{ data: [], status: "live", fetchedAt: 1, error: null }}
        operatorActions={{ data: [], status: "live", fetchedAt: 1, error: null }}
      />,
    );
    expect(screen.getByRole("tab", { name: /signal log/ }).getAttribute("aria-selected")).toBe("true");
    expect(screen.getByLabelText("Signal log")).toBeTruthy();
    await userEvent.click(screen.getByRole("tab", { name: "attention" }));
    expect(screen.getByLabelText("Fleet attention queue")).toBeTruthy();
    await userEvent.click(screen.getByRole("tab", { name: "fleet" }));
    expect(await screen.findByLabelText("Fleet communication views")).toBeTruthy();
    await userEvent.click(screen.getByRole("tab", { name: "topology" }));
    expect(await screen.findByLabelText("Fleet topology")).toBeTruthy();
    await userEvent.click(screen.getByRole("tab", { name: "metrics" }));
    expect(await screen.findByLabelText("Log metrics")).toBeTruthy();
    await userEvent.click(screen.getByRole("tab", { name: "audit" }));
    expect(await screen.findByLabelText("Receipt and operator audit")).toBeTruthy();
    await userEvent.click(screen.getByRole("tab", { name: "incident" }));
    expect(await screen.findByLabelText("Guided incident workspace")).toBeTruthy();
    await userEvent.click(screen.getByRole("tab", { name: "causality" }));
    expect(await screen.findByLabelText("Causality inspector")).toBeTruthy();
  });

  it("shows the brushed window beside the tabs and clears it", async () => {
    const onClearWindow = vi.fn();
    render(
      <InspectorHarness
        events={EVENTS}
        connected
        window={{ fromTs: 1_751_800_000, toTs: 1_751_800_100 }}
        onClearWindow={onClearWindow}
      />,
    );
    await userEvent.click(screen.getByLabelText("Clear the brushed window"));
    expect(onClearWindow).toHaveBeenCalled();
  });

  it("hops from a log row straight into a traced causality subject", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("nf", { status: 404 })));
    const onSelectionChange = vi.fn();
    render(
      <InspectorHarness
        events={EVENTS}
        connected
        provenance="derived"
        onSelectionChange={onSelectionChange}
      />,
    );
    await userEvent.click(screen.getByText("claimed t-7"));
    expect(onSelectionChange).toHaveBeenCalledWith({ kind: "task", id: "t-7" });
    expect(screen.getByRole("tab", { name: "causality" }).getAttribute("aria-selected")).toBe("true");
    await waitFor(() =>
      expect(
        (screen.getByLabelText("Hub event seq or task id") as HTMLInputElement).value,
      ).toBe("t-7"),
    );
  });

  it("adopts an external trace request the same way a log row does", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("nf", { status: 404 })));
    const { rerender } = render(<InspectorHarness events={EVENTS} connected />);
    rerender(<InspectorHarness events={EVENTS} connected traceRequest={{ subject: "t-99", nonce: 1 }} />);
    await waitFor(() =>
      expect(
        (screen.getByLabelText("Hub event seq or task id") as HTMLInputElement).value,
      ).toBe("t-99"),
    );
  });

  it("promotes an exact fleet-timeline event into shared cockpit selection", async () => {
    const onSelectionChange = vi.fn();
    render(<InspectorHarness events={EVENTS} connected onSelectionChange={onSelectionChange} />);
    await userEvent.click(screen.getByRole("tab", { name: "fleet" }));
    await screen.findByLabelText("Fleet communication views");
    await userEvent.click(screen.getByRole("tab", { name: "timeline" }));
    await userEvent.click(screen.getByRole("button", { name: "#7" }));
    expect(onSelectionChange).toHaveBeenCalledWith({ kind: "event", seq: 7 });
  });

  it("uses roving focus and automatic activation for arrow, Home, and End keys", async () => {
    const user = userEvent.setup();
    render(<InspectorHarness events={EVENTS} connected />);
    const log = screen.getByRole("tab", { name: /signal log/ });
    log.focus();
    await user.keyboard("{ArrowRight}");
    expect(document.activeElement).toBe(screen.getByRole("tab", { name: "fleet" }));
    expect(await screen.findByLabelText("Fleet communication views")).toBeTruthy();
    await user.keyboard("{End}");
    expect(document.activeElement).toBe(screen.getByRole("tab", { name: "causality" }));
    await user.keyboard("{Home}");
    expect(document.activeElement).toBe(screen.getByRole("tab", { name: "attention" }));
    await user.keyboard("{ArrowLeft}");
    expect(document.activeElement).toBe(screen.getByRole("tab", { name: "causality" }));
  });
});
