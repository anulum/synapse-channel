// @vitest-environment jsdom
// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — replay workbench behaviour tests

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ReplayWorkbench, type ReplaySlot } from "../../src/components/ReplayWorkbench";
import type { FleetStateAt } from "../../src/lib/stateAt";
import type { CockpitEvent } from "../../src/types";

afterEach(cleanup);

function state(seq: number, taskStatus = "open"): FleetStateAt {
  return {
    asOfSeq: seq,
    logEndSeq: 80,
    asOfTs: 1_780_000_000 + seq,
    note: "presence omitted",
    claims: [],
    tasks: [{ taskId: "T", title: "Task", status: taskStatus, bucket: taskStatus === "done" ? "done" : "open", dependsOn: [], unblocks: [] }],
  };
}

function slot(seq: number, taskStatus = "open"): ReplaySlot {
  return { seq, state: state(seq, taskStatus), note: null };
}

function event(seq: number): CockpitEvent {
  return { seq, ts: seq, kind: "task", lane: "task", severity: 0.5, actor: "a", label: "T changed", taskId: "T" };
}

const noOp = (): void => {};

describe("ReplayWorkbench", () => {
  it("enters history or compare at the latest retained sequence", async () => {
    const onReplayChange = vi.fn();
    render(
      <ReplayWorkbench
        replay={{ mode: "live" }}
        slotA={null}
        slotB={null}
        events={[event(50)]}
        onReplayChange={onReplayChange}
        onReplayReplace={noOp}
        onSelectEvent={noOp}
        onSelectTask={noOp}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "history" }));
    await userEvent.click(screen.getByRole("button", { name: "compare" }));
    await userEvent.click(screen.getByRole("button", { name: "live" }));
    expect(onReplayChange.mock.calls.map(([value]) => value)).toEqual([
      { mode: "history", at: 50 },
      { mode: "compare", a: 0, b: 50 },
      { mode: "live" },
    ]);
  });

  it("updates one historical position with replace navigation and jumps to the log end", async () => {
    const onReplayReplace = vi.fn();
    render(
      <ReplayWorkbench
        replay={{ mode: "history", at: 42 }}
        slotA={null}
        slotB={slot(42)}
        events={[]}
        onReplayChange={noOp}
        onReplayReplace={onReplayReplace}
        onSelectEvent={noOp}
        onSelectTask={noOp}
      />,
    );
    expect(screen.getByRole("status").textContent).toContain("HISTORY");
    fireEvent.change(screen.getByRole("spinbutton"), { target: { value: "17" } });
    fireEvent.change(screen.getByRole("slider", { name: "Historical sequence" }), { target: { value: "18" } });
    fireEvent.change(screen.getByRole("spinbutton"), { target: { value: "1.5" } });
    await userEvent.click(screen.getByRole("button", { name: "latest" }));
    expect(onReplayReplace.mock.calls.map(([value]) => value)).toEqual([
      { mode: "history", at: 17 },
      { mode: "history", at: 18 },
      { mode: "history", at: 0 },
      { mode: "history", at: 80 },
    ]);
  });

  it("compares A and B, exposes exact transition hops, and keeps missing provenance honest", async () => {
    const onReplayReplace = vi.fn();
    const onSelectEvent = vi.fn();
    const onSelectTask = vi.fn();
    const { rerender } = render(
      <ReplayWorkbench
        replay={{ mode: "compare", a: 10, b: 20 }}
        slotA={slot(10, "open")}
        slotB={slot(20, "done")}
        events={[event(18)]}
        onReplayChange={noOp}
        onReplayReplace={onReplayReplace}
        onSelectEvent={onSelectEvent}
        onSelectTask={onSelectTask}
      />,
    );
    expect(screen.getByText("~1 changed")).toBeTruthy();
    expect(screen.getByText("1/1 transition events retained")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "task · T" }));
    await userEvent.click(screen.getByRole("button", { name: "exact event #18" }));
    expect(onSelectTask).toHaveBeenCalledWith("T");
    expect(onSelectEvent).toHaveBeenCalledWith(18);
    const numbers = screen.getAllByRole("spinbutton");
    fireEvent.change(numbers[0] as HTMLElement, { target: { value: "9" } });
    fireEvent.change(numbers[1] as HTMLElement, { target: { value: "21" } });
    expect(onReplayReplace).toHaveBeenCalledWith({ mode: "compare", a: 9, b: 20 });
    expect(onReplayReplace).toHaveBeenCalledWith({ mode: "compare", a: 10, b: 21 });

    rerender(
      <ReplayWorkbench
        replay={{ mode: "compare", a: 10, b: 20 }}
        slotA={slot(10, "open")}
        slotB={slot(20, "done")}
        events={[]}
        onReplayChange={noOp}
        onReplayReplace={noOp}
        onSelectEvent={noOp}
        onSelectTask={noOp}
      />,
    );
    expect(screen.getByText("transition event outside retained window")).toBeTruthy();
  });

  it("shows reconstruction gaps and a no-delta result without inventing empty data", () => {
    const { rerender } = render(
      <ReplayWorkbench
        replay={{ mode: "compare", a: 1, b: 2 }}
        slotA={{ seq: 1, state: null, note: "A failed" }}
        slotB={{ seq: 2, state: null, note: "B failed" }}
        events={[]}
        onReplayChange={noOp}
        onReplayReplace={noOp}
        onSelectEvent={noOp}
        onSelectTask={noOp}
      />,
    );
    expect(screen.getAllByRole("alert").map((node) => node.textContent)).toEqual(["B failed", "A failed"]);
    rerender(
      <ReplayWorkbench
        replay={{ mode: "compare", a: 1, b: 2 }}
        slotA={slot(1)}
        slotB={slot(2)}
        events={[]}
        onReplayChange={noOp}
        onReplayReplace={noOp}
        onSelectEvent={noOp}
        onSelectTask={noOp}
      />,
    );
    expect(screen.getByText("No claim or task evidence changed between A and B.")).toBeTruthy();
  });
});
