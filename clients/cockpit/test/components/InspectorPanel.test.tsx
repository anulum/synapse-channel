// @vitest-environment jsdom
// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — inspector panel router contracts

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { InspectorPanel, type InspectorPanelProps } from "../../src/components/InspectorPanel";

// The operations tab stands in as what the inspector hands it, so the hop of the capability is seen alone.
vi.mock("../../src/components/OperationsView", () => ({
  OperationsView: ({ canDecide }: { readonly canDecide?: boolean }) => <p>{`decide here offered: ${String(canDecide)}`}</p>,
}));

afterEach(cleanup);

function props(tab: InspectorPanelProps["tab"]): InspectorPanelProps {
  return {
    tab, events: [], fleetView: "web", onFleetViewChange: () => {}, fleetSelection: null,
    onFleetSelectionChange: () => {}, onTabChange: () => {}, onSelectTask: () => {}, prefill: null,
  };
}

describe("InspectorPanel", () => {
  it("owns coverage and the immediate attention/log routes", () => {
    const { rerender } = render(<InspectorPanel {...props("log")} />);
    expect(screen.getByLabelText("Signal log")).toBeTruthy();
    expect(screen.getAllByText("observed transitions").length).toBeGreaterThan(0);
    rerender(<InspectorPanel {...props("attention")} connected />);
    expect(screen.getByLabelText("Fleet attention queue")).toBeTruthy();
  });

  it("offers the operations tab a decision only when the login may record one", async () => {
    const { rerender } = render(<InspectorPanel {...props("operations")} />);
    expect(await screen.findByText("decide here offered: false")).toBeTruthy();
    rerender(<InspectorPanel {...props("operations")} canDecideApproval />);
    expect(await screen.findByText("decide here offered: true")).toBeTruthy();
  });
});
