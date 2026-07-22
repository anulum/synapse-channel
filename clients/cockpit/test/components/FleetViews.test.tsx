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
import { useState, type JSX } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { FleetViews } from "../../src/components/FleetViews";
import type { CockpitEvent } from "../../src/types";
import type { CockpitSelection, FleetView } from "../../src/lib/workspace";

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

const F4_EVENTS: readonly CockpitEvent[] = [
  ...EVENTS,
  {
    seq: 3,
    ts: Date.now() / 1000 + 1,
    kind: "claim",
    lane: "claims",
    severity: 0.3,
    actor: "alpha/one",
    label: "claimed SCH-3",
    taskId: "SCH-3",
  },
  {
    seq: 4,
    ts: Date.now() / 1000 + 2,
    kind: "presence",
    lane: "presence",
    severity: 0.1,
    actor: "beta/two-rx",
    label: "receiver waiting",
    taskId: "",
  },
  {
    seq: 5,
    ts: Date.now() / 1000 + 3,
    kind: "task",
    lane: "task",
    severity: 0.4,
    actor: "beta/two",
    label: "task advanced",
    taskId: "SCH-5",
  },
];

type FleetHarnessProps = Omit<
  Parameters<typeof FleetViews>[0],
  "view" | "onViewChange" | "selection" | "onSelectionChange"
> & {
  readonly initialView?: FleetView;
  readonly initialSelection?: CockpitSelection | null;
};

function FleetHarness({
  initialView = "web",
  initialSelection = null,
  ...props
}: FleetHarnessProps): JSX.Element {
  const [view, setView] = useState<FleetView>(initialView);
  const [selection, setSelection] = useState<CockpitSelection | null>(initialSelection);
  return (
    <FleetViews
      {...props}
      view={view}
      onViewChange={setView}
      selection={selection}
      onSelectionChange={setSelection}
    />
  );
}

describe("FleetViews", () => {
  it("switches among all five fleet instruments", async () => {
    render(<FleetHarness events={F4_EVENTS} claims={[]} agents={[]} window={null} connected canMessage={false} />);
    expect(screen.getByTestId("fleet-web")).toBeTruthy();
    await userEvent.click(screen.getByRole("tab", { name: "matrix" }));
    expect(screen.getByTestId("fleet-matrix")).toBeTruthy();
    await userEvent.click(screen.getByRole("tab", { name: "projects" }));
    expect(screen.getByTestId("fleet-projects")).toBeTruthy();
    await userEvent.click(screen.getByRole("tab", { name: "timeline" }));
    expect(screen.getByTestId("fleet-timeline")).toBeTruthy();
    await userEvent.click(screen.getByRole("tab", { name: "flow" }));
    expect(screen.getByTestId("fleet-flow")).toBeTruthy();
  });

  it("keeps message controls absent for viewers and prefills an exact operator peer", async () => {
    const onMessagePeer = vi.fn();
    const { rerender } = render(
      <FleetHarness
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
      <FleetHarness
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
      <FleetHarness
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
      <FleetHarness
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
    expect(screen.getByText(/transport ACK remains unchanged/iu)).toBeTruthy();
  });

  it("offers a precise priority-route selector when graph hit targets overlap", async () => {
    render(<FleetHarness events={EVENTS} claims={[]} agents={[]} window={null} connected canMessage={false} />);
    await userEvent.click(
      screen.getByRole("button", {
        name: "Select priority route alpha/one to beta/two: 1 message",
      }),
    );
    expect(screen.getByLabelText("Communication detail")).toBeTruthy();
    expect(screen.getByText("alpha/one → beta/two")).toBeTruthy();
    expect(screen.getByText("1 · unknown")).toBeTruthy();
  });

  it("states empty durable-feed data honestly", () => {
    render(<FleetHarness events={[]} claims={[]} agents={["quiet/one"]} window={null} connected canMessage={false} />);
    expect(screen.getByText(/require the durable event feed/u)).toBeTruthy();
  });

  it("restores a controlled route selection and uses roving view-tab focus", async () => {
    const user = userEvent.setup();
    render(
      <FleetHarness
        events={EVENTS}
        claims={[]}
        agents={[]}
        window={null}
        connected
        canMessage={false}
        initialView="matrix"
        initialSelection={{ kind: "route", source: "alpha/one", target: "beta/two" }}
      />,
    );
    expect(screen.getByTestId("fleet-matrix")).toBeTruthy();
    expect(screen.getByLabelText("Communication detail")).toBeTruthy();
    expect(
      screen.getByRole("button", { name: "alpha/one to beta/two: 1 messages" }).getAttribute("aria-pressed"),
    ).toBe("true");
    const matrix = screen.getByRole("tab", { name: "matrix" });
    matrix.focus();
    await user.keyboard("{ArrowRight}");
    expect(document.activeElement).toBe(screen.getByRole("tab", { name: "projects" }));
    expect(screen.getByTestId("fleet-projects")).toBeTruthy();
    await user.keyboard("{Home}");
    expect(document.activeElement).toBe(screen.getByRole("tab", { name: "web" }));
    await user.keyboard("{ArrowLeft}");
    expect(document.activeElement).toBe(screen.getByRole("tab", { name: "flow" }));
    await user.keyboard("{End}");
    expect(document.activeElement).toBe(screen.getByRole("tab", { name: "flow" }));
  });

  it("binds timeline and project-flow marks to exact retained event evidence", async () => {
    render(
      <FleetHarness
        events={F4_EVENTS}
        claims={[]}
        agents={[]}
        window={null}
        connected
        canMessage={false}
        initialView="timeline"
      />,
    );
    expect(screen.getAllByText("message").length).toBeGreaterThan(0);
    expect(screen.getAllByText("claim").length).toBeGreaterThan(0);
    expect(screen.getAllByText("wait").length).toBeGreaterThan(0);
    expect(screen.getAllByText("task").length).toBeGreaterThan(0);
    await userEvent.click(screen.getByRole("button", { name: "#5" }));
    expect(screen.getByRole("button", { name: "#5" }).getAttribute("aria-pressed")).toBe("true");

    await userEvent.click(screen.getByRole("tab", { name: "flow" }));
    expect(screen.getByText("exact route evidence")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "#2" }));
    expect(screen.getByRole("button", { name: "#2" }).getAttribute("aria-pressed")).toBe("true");
  });

  it("marks controlled agent and project selections in their visual peers", () => {
    const { rerender } = render(
      <FleetHarness
        events={EVENTS}
        claims={[]}
        agents={[]}
        window={null}
        connected
        canMessage={false}
        initialSelection={{ kind: "agent", id: "alpha/one" }}
      />,
    );
    expect(screen.getByRole("button", { name: /alpha\/one, 1 message/u }).getAttribute("aria-pressed")).toBe("true");
    rerender(
      <FleetHarness
        key="project-selection"
        events={EVENTS}
        claims={[]}
        agents={[]}
        window={null}
        connected
        canMessage={false}
        initialView="projects"
        initialSelection={{ kind: "project", id: "alpha" }}
      />,
    );
    expect(
      screen.getByRole("button", { name: /alpha.*1 agent.*1 contact/u }).getAttribute("aria-pressed"),
    ).toBe("true");
  });
});
