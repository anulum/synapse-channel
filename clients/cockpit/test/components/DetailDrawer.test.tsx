// @vitest-environment jsdom
// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — detail drawer behaviour tests

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DetailDrawer } from "../../src/components/DetailDrawer";
import type { AgentDetail, TaskDetail } from "../../src/lib/detail";
import type { ClaimView } from "../../src/lib/claims";
import type { CockpitEvent } from "../../src/types";

afterEach(cleanup);

function claimView(taskId: string, options: { stale?: boolean; paths?: string[] } = {}): ClaimView {
  return {
    claim: {
      task_id: taskId,
      owner: "quantum/claude",
      lease_expires_at: null,
      paths: options.paths ?? [],
      stale: options.stale ?? false,
      git: null,
    },
    urgency: options.stale === true ? "stale" : "held",
    inConflict: false,
    secondsToExpiry: null,
  };
}

function eventOf(seq: number, label: string): CockpitEvent {
  return {
    seq,
    ts: 1_751_800_000 + seq,
    kind: "claim",
    lane: "claims",
    severity: 0.5,
    actor: "quantum/claude",
    label,
    taskId: "t-1",
  };
}

const AGENT: AgentDetail = {
  name: "quantum/claude",
  entry: {
    agent: "quantum/claude",
    status: "holding",
    online: true,
    activeClaims: [],
    staleClaims: [],
    paths: [],
    inConflict: true,
    wakerMissing: true,
  },
  claims: [claimView("t-1", { paths: ["src/a.py"] }), claimView("t-2", { stale: true })],
  deadLetters: [
    { target: "quantum/claude", count: 3, lastSender: "CEO", lastTs: 1 },
    { target: "quantum/claude-tx", count: 2, lastSender: "", lastTs: null },
  ],
  recentEvents: [eventOf(9, "claimed t-1")],
  moreEvents: 4,
};

const TASK: TaskDetail = {
  taskId: "t-1",
  task: {
    taskId: "t-1",
    title: "wire the approvals surface",
    status: "doing",
    bucket: "blocked",
    dependsOn: [
      { taskId: "t-0", satisfied: true, missing: false, status: "done" },
      { taskId: "t-ghost", satisfied: false, missing: true, status: "missing" },
    ],
    unblocks: ["t-2", "t-3"],
  },
  claim: claimView("t-1"),
  recentEvents: [],
  moreEvents: 0,
};

describe("DetailDrawer", () => {
  it("renders nothing when no subject is inspected", () => {
    const { container } = render(
      <DetailDrawer onClose={() => {}} onFilterLog={() => {}} />,
    );
    expect(container.innerHTML).toBe("");
  });

  it("states the agent's condition, claims, unread mailbox, and window overflow", () => {
    render(<DetailDrawer agent={AGENT} onClose={() => {}} onFilterLog={() => {}} />);
    expect(screen.getByLabelText("Agent quantum/claude")).toBeTruthy();
    expect(screen.getByText("online")).toBeTruthy();
    expect(screen.getByText("waker missing")).toBeTruthy();
    expect(screen.getByText("in conflict")).toBeTruthy();
    expect(screen.getByText("claims held · 2")).toBeTruthy();
    expect(screen.getByText("held · src/a.py")).toBeTruthy();
    expect(screen.getByText("stale")).toBeTruthy();
    expect(screen.getByText("unread mailbox · 5")).toBeTruthy();
    expect(screen.getByText("3 unread · last from CEO")).toBeTruthy();
    expect(screen.getByText("2 unread · last from —")).toBeTruthy();
    expect(screen.getByText("claimed t-1")).toBeTruthy();
    expect(screen.getByText("+4 more in the window")).toBeTruthy();
  });

  it("states an unknown agent as not in roster with an empty window", () => {
    render(
      <DetailDrawer
        agent={{ name: "ghost", entry: null, claims: [], deadLetters: [], recentEvents: [], moreEvents: 0 }}
        onClose={() => {}}
        onFilterLog={() => {}}
      />,
    );
    expect(screen.getByText("not in roster")).toBeTruthy();
    expect(screen.getByText("Holds nothing right now.")).toBeTruthy();
    expect(screen.getByText("Nothing from it in the observed window.")).toBeTruthy();
  });

  it("shows the task's bucket, holder, dependency verdicts, and unblocks", () => {
    render(<DetailDrawer task={TASK} onClose={() => {}} onFilterLog={() => {}} onTrace={() => {}} />);
    expect(screen.getByText("blocked · doing")).toBeTruthy();
    expect(screen.getByText("held by quantum/claude")).toBeTruthy();
    expect(screen.getByText("wire the approvals surface")).toBeTruthy();
    expect(screen.getByText("satisfied")).toBeTruthy();
    expect(screen.getByText("missing")).toBeTruthy();
    expect(screen.getByText("t-2, t-3")).toBeTruthy();
  });

  it("states a task absent from the board", () => {
    render(
      <DetailDrawer
        task={{ taskId: "t-x", task: null, claim: null, recentEvents: [], moreEvents: 0 }}
        onClose={() => {}}
        onFilterLog={() => {}}
      />,
    );
    expect(screen.getByText("not on the board")).toBeTruthy();
  });

  it("steers the log and the causality inspector, and closes on Escape and veil", async () => {
    const onClose = vi.fn();
    const onFilterLog = vi.fn();
    const onTrace = vi.fn();
    render(<DetailDrawer task={TASK} onClose={onClose} onFilterLog={onFilterLog} onTrace={onTrace} />);
    await userEvent.click(screen.getByText("filter log"));
    expect(onFilterLog).toHaveBeenCalledWith("t-1");
    await userEvent.click(screen.getByText("trace causality"));
    expect(onTrace).toHaveBeenCalledWith("t-1");
    await userEvent.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(1);
    await userEvent.click(document.querySelector(".drawer-veil") as Element);
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it("hides the trace action for agents and without a trace handler", () => {
    render(<DetailDrawer agent={AGENT} onClose={() => {}} onFilterLog={() => {}} onTrace={() => {}} />);
    expect(screen.queryByText("trace causality")).toBeNull();
    cleanup();
    render(<DetailDrawer task={TASK} onClose={() => {}} onFilterLog={() => {}} />);
    expect(screen.queryByText("trace causality")).toBeNull();
  });
});
