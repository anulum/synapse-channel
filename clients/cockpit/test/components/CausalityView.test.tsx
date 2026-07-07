// @vitest-environment jsdom
// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — causality inspector behaviour tests

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CausalityView } from "../../src/components/CausalityView";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function wireNode(seq: number, hubId = ""): Record<string, unknown> {
  return { seq, kind: "claim", owner: "quantum/claude", task_id: "t-1", ts: 1_751_800_000, hub_id: hubId };
}

const LOADED_TRACE = {
  direction: "causes",
  seq: 42,
  present: true,
  node: wireNode(42),
  direct: [{ src: 41, dst: 42, relation: "same_task", detail: "prior claim", node: wireNode(41) }],
  transitive: [wireNode(40), wireNode(39, "hub-b")],
  note: "",
};

function stubFetch(body: unknown, status = 200): ReturnType<typeof vi.fn> {
  const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status }));
  vi.stubGlobal("fetch", fetcher);
  return fetcher;
}

describe("CausalityView", () => {
  it("guides the operator while idle and refuses to trace an empty subject", async () => {
    const fetcher = stubFetch({});
    render(<CausalityView />);
    expect(screen.getByText(/Enter a hub event seq/)).toBeTruthy();
    await userEvent.click(screen.getByText("trace"));
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("traces a seq, shows causes, relations, and hub-clustered transitive closure", async () => {
    const fetcher = stubFetch(LOADED_TRACE);
    render(<CausalityView />);
    await userEvent.type(screen.getByLabelText("Hub event seq or task id"), "42");
    await userEvent.click(screen.getByText("trace"));
    await waitFor(() => expect(screen.getByText("causes of")).toBeTruthy());
    const [url] = (fetcher.mock.calls[0] ?? []) as [string];
    expect(url).toBe("/causality.json?seq=42&direction=causes");
    expect(screen.getByText("same_task")).toBeTruthy();
    expect(screen.getByText("prior claim")).toBeTruthy();
    expect(screen.getByText("transitive (2)")).toBeTruthy();
    // A federated trace names each hub cluster, including the local one.
    expect(screen.getByText("local hub")).toBeTruthy();
    expect(screen.getByText("hub-b")).toBeTruthy();
  });

  it("routes a non-numeric subject as a task query with the chosen direction", async () => {
    const fetcher = stubFetch({ ...LOADED_TRACE, direction: "effects", direct: [], transitive: [] });
    render(<CausalityView />);
    await userEvent.type(screen.getByLabelText("Hub event seq or task id"), "t-1");
    await userEvent.selectOptions(screen.getByLabelText("Trace direction"), "effects");
    await userEvent.click(screen.getByText("trace"));
    await waitFor(() => expect(screen.getByText("No recorded relations.")).toBeTruthy());
    const [url] = (fetcher.mock.calls[0] ?? []) as [string];
    expect(url).toBe("/causality.json?task=t-1&direction=effects");
  });

  it("states absence, failure, and a not-in-graph verdict as distinct facts", async () => {
    stubFetch("nf", 404);
    render(<CausalityView />);
    await userEvent.type(screen.getByLabelText("Hub event seq or task id"), "7");
    await userEvent.click(screen.getByText("trace"));
    await waitFor(() => expect(screen.getByText(/does not serve causality traces yet/)).toBeTruthy());
    cleanup();
    vi.unstubAllGlobals();
    stubFetch("boom", 500);
    render(<CausalityView />);
    await userEvent.type(screen.getByLabelText("Hub event seq or task id"), "7");
    await userEvent.click(screen.getByText("trace"));
    await waitFor(() => expect(screen.getByText("Trace failed: hub returned 500")).toBeTruthy());
    cleanup();
    vi.unstubAllGlobals();
    stubFetch({ direction: "causes", seq: 7, present: false, node: null, direct: [], transitive: [], note: "journal predates the causal index" });
    render(<CausalityView />);
    await userEvent.type(screen.getByLabelText("Hub event seq or task id"), "7");
    await userEvent.click(screen.getByText("trace"));
    await waitFor(() =>
      expect(screen.getByText("Event 7: journal predates the causal index.")).toBeTruthy(),
    );
  });

  it("adopts a prefilled subject from another panel and traces it immediately", async () => {
    stubFetch(LOADED_TRACE);
    render(<CausalityView prefill={{ subject: "t-1", nonce: 1 }} />);
    await waitFor(() => expect(screen.getByText("causes of")).toBeTruthy());
    expect((screen.getByLabelText("Hub event seq or task id") as HTMLInputElement).value).toBe("t-1");
  });
});
