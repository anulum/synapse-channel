// @vitest-environment jsdom
// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE CHANNEL — operator attention queue interaction tests

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AttentionQueue } from "../../src/components/AttentionQueue";
import type { AttentionItem } from "../../src/lib/attention";

afterEach(cleanup);

const ITEMS: readonly AttentionItem[] = [
  {
    id: "critical-agent",
    level: "critical",
    kind: "dead_letter",
    subject: "alpha/one",
    evidence: "2 unread",
    observedAt: 1_751_800_000,
    action: { kind: "agent", id: "alpha/one" },
  },
  {
    id: "warning-task",
    level: "warning",
    kind: "blocked_task",
    subject: "task-a",
    evidence: "unmet: gate-a",
    observedAt: null,
    action: { kind: "task", id: "task-a" },
  },
  {
    id: "warning-route",
    level: "warning",
    kind: "deferred_route",
    subject: "alpha/one → beta/two",
    evidence: "1 deferred receipt",
    observedAt: 1_751_800_100,
    action: { kind: "route", source: "alpha/one", target: "beta/two" },
  },
];

describe("AttentionQueue", () => {
  it("distinguishes connection wait from a connected empty queue", () => {
    const { rerender } = render(<AttentionQueue items={[]} connected={false} />);
    expect(screen.getByText("Waiting for the hub.")).toBeTruthy();
    rerender(<AttentionQueue items={[]} connected />);
    expect(screen.getByText("No current signals in this evidence filter.")).toBeTruthy();
  });

  it("filters by explicit level and runs each navigation action", async () => {
    const onInspectAgent = vi.fn();
    const onInspectTask = vi.fn();
    const onInspectRoute = vi.fn();
    render(
      <AttentionQueue
        items={ITEMS}
        connected
        onInspectAgent={onInspectAgent}
        onInspectTask={onInspectTask}
        onInspectRoute={onInspectRoute}
      />,
    );

    await userEvent.click(screen.getByRole("button", { name: "inspect agent" }));
    await userEvent.click(screen.getByRole("button", { name: "open task" }));
    await userEvent.click(screen.getByRole("button", { name: "inspect route" }));
    expect(onInspectAgent).toHaveBeenCalledWith("alpha/one");
    expect(onInspectTask).toHaveBeenCalledWith("task-a");
    expect(onInspectRoute).toHaveBeenCalledWith("alpha/one", "beta/two");

    await userEvent.click(screen.getByRole("button", { name: "critical 1" }));
    expect(screen.getByText("alpha/one")).toBeTruthy();
    expect(screen.queryByText("task-a")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "warning 2" }));
    expect(screen.queryByText("alpha/one")).toBeNull();
    expect(screen.getByText("task-a")).toBeTruthy();
  });

  it("states when the visible list is capped", () => {
    const many = Array.from({ length: 52 }, (_, index): AttentionItem => ({
      id: `item-${index}`,
      level: "warning",
      kind: "missing_waiter",
      subject: `agent-${index}`,
      evidence: "no waiter",
      observedAt: null,
      action: null,
    }));
    render(<AttentionQueue items={many} connected />);
    expect(screen.getByText("+2 more signals")).toBeTruthy();
    expect(screen.queryByText("agent-51")).toBeNull();
  });
});
