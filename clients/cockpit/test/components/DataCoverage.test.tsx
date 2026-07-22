// @vitest-environment jsdom
// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE CHANNEL — retained event-window coverage strip tests

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { DataCoverage } from "../../src/components/DataCoverage";
import type { EventCoverage } from "../../src/lib/eventCoverage";

afterEach(cleanup);

const EMPTY: EventCoverage = {
  source: "connecting",
  retained: 0,
  capacity: 250,
  minSeq: null,
  maxSeq: null,
  minTs: null,
  maxTs: null,
  atCapacity: false,
};

describe("DataCoverage", () => {
  it("renders connecting and derived bounded-window states without a fabricated range", () => {
    const { rerender } = render(<DataCoverage coverage={EMPTY} />);
    expect(screen.getByText("event source connecting")).toBeTruthy();
    expect(screen.getByText("0 retained / 250 client cap")).toBeTruthy();
    expect(screen.getByText("bounded client window")).toBeTruthy();
    expect(screen.queryByText(/^seq /u)).toBeNull();

    rerender(<DataCoverage coverage={{ ...EMPTY, source: "derived" }} />);
    expect(screen.getByText("observed transitions")).toBeTruthy();
  });

  it("renders a hub sequence/time range and distinguishes a window at cap", () => {
    render(
      <DataCoverage
        coverage={{
          source: "hub",
          retained: 250,
          capacity: 250,
          minSeq: 12,
          maxSeq: 300,
          minTs: 1_751_800_000,
          maxTs: 1_751_800_100,
          atCapacity: true,
        }}
      />,
    );
    expect(screen.getByText("hub event log")).toBeTruthy();
    expect(screen.getByText("seq 12–300")).toBeTruthy();
    expect(screen.getByText("retained window at cap")).toBeTruthy();
    expect(screen.getByLabelText("Event data coverage").textContent).toMatch(/\d{2}:\d{2}:\d{2}/u);
  });
});
