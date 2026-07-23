// @vitest-environment jsdom
// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — inspector panel router contracts

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { InspectorPanel, type InspectorPanelProps } from "../../src/components/InspectorPanel";

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
});
