// @vitest-environment jsdom
// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — multi-view fleet instrument interaction tests

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { FleetViews } from "../../src/components/FleetViews";
import type { CockpitEvent } from "../../src/types";

afterEach(cleanup);

const EVENTS: readonly CockpitEvent[] = [
  {
    seq: 2,
    ts: Date.now() / 1000,
    kind: "chat",
    lane: "task",
    severity: 0.2,
    actor: "alpha/one",
    label: "hello",
    taskId: "",
    payload: {
      sender: "alpha/one",
      target: "beta/two",
      type: "chat",
      payload: "hello",
    },
  },
];

describe("FleetViews", () => {
  it("switches among web, matrix, and project instruments", async () => {
    render(<FleetViews events={EVENTS} claims={[]} agents={[]} window={null} connected canMessage={false} />);
    expect(screen.getByTestId("fleet-web")).toBeTruthy();
    await userEvent.click(screen.getByRole("tab", { name: "matrix" }));
    expect(screen.getByTestId("fleet-matrix")).toBeTruthy();
    await userEvent.click(screen.getByRole("tab", { name: "projects" }));
    expect(screen.getByTestId("fleet-projects")).toBeTruthy();
  });

  it("keeps message controls absent for viewers and prefills an exact operator peer", async () => {
    const onMessagePeer = vi.fn();
    const { rerender } = render(
      <FleetViews
        events={EVENTS}
        claims={[]}
        agents={[]}
        window={null}
        connected
        canMessage={false}
        onMessagePeer={onMessagePeer}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: /beta\/two, 1 message/u }));
    expect(screen.queryByRole("button", { name: "message peer" })).toBeNull();

    rerender(
      <FleetViews
        events={EVENTS}
        claims={[]}
        agents={[]}
        window={null}
        connected
        canMessage
        onMessagePeer={onMessagePeer}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "message peer" }));
    expect(onMessagePeer).toHaveBeenCalledWith("beta/two");
    expect(screen.getByText(/do not alter transport ACK state/u)).toBeTruthy();
  });

  it("opens a pair timeline from an edge and sends an exact semantic response", async () => {
    const respondToMessage = vi.fn().mockResolvedValue({
      kind: "accepted",
      status: "delivered",
      detail: "semantic response delivered",
    });
    const { rerender } = render(
      <FleetViews
        events={EVENTS}
        claims={[]}
        agents={[]}
        window={null}
        connected
        canMessage={false}
        respondToMessage={respondToMessage}
      />,
    );
    await userEvent.click(
      screen.getByRole("button", {
        name: /alpha\/one to beta\/two.*open communication detail/u,
      }),
    );
    expect(screen.getByLabelText("Communication detail")).toBeTruthy();
    expect(screen.getByText("hello")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "send response" })).toBeNull();

    rerender(
      <FleetViews
        events={EVENTS}
        claims={[]}
        agents={[]}
        window={null}
        connected
        canMessage
        respondToMessage={respondToMessage}
      />,
    );
    await userEvent.selectOptions(screen.getByLabelText(/respond to #2/u), "needs_input");
    await userEvent.type(screen.getByLabelText("optional note"), "Which revision?");
    await userEvent.click(screen.getByRole("button", { name: "send response" }));
    expect(respondToMessage).toHaveBeenCalledWith({
      messageSeq: 2,
      to: "alpha/one",
      status: "needs_input",
      note: "Which revision?",
    });
    expect(await screen.findByText("semantic response delivered")).toBeTruthy();
    expect(screen.getByText(/transport ACK remains unchanged/u)).toBeTruthy();
  });

  it("states empty durable-feed data honestly", () => {
    render(<FleetViews events={[]} claims={[]} agents={["quiet/one"]} window={null} connected canMessage={false} />);
    expect(screen.getByText(/require the durable event feed/u)).toBeTruthy();
  });
});
