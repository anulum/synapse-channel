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
import { afterEach, describe, expect, it, vi } from "vitest";

import { InspectorTabs } from "../../src/components/InspectorTabs";
import type { CockpitEvent } from "../../src/types";

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

describe("InspectorTabs", () => {
  it("starts on the log and switches to each tab", async () => {
    render(
      <InspectorTabs
        events={EVENTS}
        connected
        receipts={{ data: [], status: "live", fetchedAt: 1, error: null }}
        operatorActions={{ data: [], status: "live", fetchedAt: 1, error: null }}
      />,
    );
    expect(screen.getByRole("tab", { name: /signal log/ }).getAttribute("aria-selected")).toBe("true");
    expect(screen.getByLabelText("Signal log")).toBeTruthy();
    await userEvent.click(screen.getByRole("tab", { name: "fleet" }));
    expect(screen.getByLabelText("Fleet communication views")).toBeTruthy();
    await userEvent.click(screen.getByRole("tab", { name: "topology" }));
    expect(screen.getByLabelText("Fleet topology")).toBeTruthy();
    await userEvent.click(screen.getByRole("tab", { name: "metrics" }));
    expect(screen.getByLabelText("Log metrics")).toBeTruthy();
    await userEvent.click(screen.getByRole("tab", { name: "audit" }));
    expect(screen.getByLabelText("Receipt and operator audit")).toBeTruthy();
    await userEvent.click(screen.getByRole("tab", { name: "causality" }));
    expect(screen.getByLabelText("Causality inspector")).toBeTruthy();
  });

  it("shows the brushed window beside the tabs and clears it", async () => {
    const onClearWindow = vi.fn();
    render(
      <InspectorTabs
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
    render(<InspectorTabs events={EVENTS} connected provenance="derived" />);
    await userEvent.click(screen.getByText("claimed t-7"));
    expect(screen.getByRole("tab", { name: "causality" }).getAttribute("aria-selected")).toBe("true");
    await waitFor(() =>
      expect(
        (screen.getByLabelText("Hub event seq or task id") as HTMLInputElement).value,
      ).toBe("t-7"),
    );
  });

  it("adopts an external trace request the same way a log row does", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("nf", { status: 404 })));
    const { rerender } = render(<InspectorTabs events={EVENTS} connected />);
    rerender(<InspectorTabs events={EVENTS} connected traceRequest={{ subject: "t-99", nonce: 1 }} />);
    await waitFor(() =>
      expect(
        (screen.getByLabelText("Hub event seq or task id") as HTMLInputElement).value,
      ).toBe("t-99"),
    );
  });
});
