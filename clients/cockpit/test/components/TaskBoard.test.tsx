// @vitest-environment jsdom
// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — task board behaviour tests

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { TaskBoard } from "../../src/components/TaskBoard";
import type { BoardBucket, BoardTask } from "../../src/lib/board";

afterEach(cleanup);

function task(taskId: string, bucket: BoardBucket, overrides: Partial<BoardTask> = {}): BoardTask {
  return {
    taskId,
    title: "",
    status: bucket === "done" ? "done" : bucket === "blocked" ? "blocked" : "declared",
    bucket,
    dependsOn: [],
    unblocks: [],
    ...overrides,
  };
}

describe("TaskBoard", () => {
  it("distinguishes waiting, empty board, and no-match states", async () => {
    render(<TaskBoard tasks={[]} connected={false} />);
    expect(screen.getByText("Waiting for the hub.")).toBeTruthy();
    cleanup();
    render(<TaskBoard tasks={[]} connected />);
    expect(screen.getByText("The board is empty — no tasks declared.")).toBeTruthy();
    cleanup();
    render(<TaskBoard tasks={[task("t-1", "open")]} connected />);
    await userEvent.type(screen.getByLabelText("Find a task by id or title"), "nothing-matches");
    // Typed input lands keystroke by keystroke; wait out the last render.
    await waitFor(() => expect(screen.getByText("No task matches the query.")).toBeTruthy());
    expect(screen.getByText("0 of 1 shown")).toBeTruthy();
    await userEvent.click(screen.getByText("reset"));
    expect(screen.queryByText("No task matches the query.")).toBeNull();
  });

  it("counts blocked loudly, verdicts dependency edges, and shows unblocks on done rows", () => {
    render(
      <TaskBoard
        connected
        tasks={[
          task("t-blocked", "blocked", {
            title: "wire it",
            dependsOn: [
              { taskId: "t-done", satisfied: true, missing: false, status: "done" },
              { taskId: "t-ghost", satisfied: false, missing: true, status: "missing" },
              { taskId: "t-wip", satisfied: false, missing: false, status: "doing" },
            ],
          }),
          task("t-done", "done", { unblocks: ["t-blocked"] }),
        ]}
      />,
    );
    expect(screen.getByText("1 blocked")).toBeTruthy();
    expect(screen.getByText("✓ t-done")).toBeTruthy();
    expect(screen.getByText("✕ t-ghost").className).toContain("dep-chip--missing");
    expect(screen.getByText("… t-wip").className).toContain("dep-chip--waiting");
    expect(screen.getByText("↳ t-blocked")).toBeTruthy();
    expect(screen.getByText("wire it")).toBeTruthy();
  });

  it("filters by bucket chips and finds by text", async () => {
    render(
      <TaskBoard
        connected
        tasks={[task("t-a", "open", { title: "alpha work" }), task("t-b", "ready"), task("t-c", "done")]}
      />,
    );
    expect(screen.getByText("open 1")).toBeTruthy();
    await userEvent.click(screen.getByText("ready 1"));
    expect(screen.queryByText("t-a")).toBeNull();
    expect(screen.getByText("t-b")).toBeTruthy();
    await userEvent.click(screen.getByText("ready 1"));
    await userEvent.type(screen.getByLabelText("Find a task by id or title"), "alpha");
    expect(screen.getByText("t-a")).toBeTruthy();
    expect(screen.queryByText("t-b")).toBeNull();
  });

  it("collapses the done tail unless the board is being searched", async () => {
    const tasks = [
      task("t-open", "open"),
      ...Array.from({ length: 9 }, (_, index) => task(`t-done-${index}`, "done")),
    ];
    render(<TaskBoard connected tasks={tasks} />);
    expect(screen.getByText("+3 more done")).toBeTruthy();
    await userEvent.click(screen.getByText("done 9"));
    expect(screen.queryByText("+3 more done")).toBeNull();
  });

  it("states the hub's cap signal in the head count", () => {
    render(
      <TaskBoard
        connected
        tasks={[task("t-1", "open")]}
        truncation={{ totalTasks: 500, truncated: true, taskCap: null }}
      />,
    );
    expect(screen.getByText("1 of 500")).toBeTruthy();
    expect(screen.getByText("capped reply")).toBeTruthy();
  });

  it("opens the task drawer on row click and downloads the shown board as a report", async () => {
    const onInspect = vi.fn();
    // jsdom ships no createObjectURL at all, so these are installed, not spied.
    const objectUrl = vi.fn().mockReturnValue("blob:report");
    const revoke = vi.fn();
    Object.assign(URL, { createObjectURL: objectUrl, revokeObjectURL: revoke });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    render(<TaskBoard connected tasks={[task("t-1", "open")]} onInspect={onInspect} lens="quantum/claude" />);
    await userEvent.click(screen.getByText("t-1"));
    expect(onInspect).toHaveBeenCalledWith("t-1");
    await userEvent.click(screen.getByText("report"));
    expect(objectUrl).toHaveBeenCalledTimes(1);
    expect(click).toHaveBeenCalledTimes(1);
    expect(revoke).toHaveBeenCalledWith("blob:report");
    click.mockRestore();
  });

  it("highlights only the task carried by the shared selection", () => {
    render(
      <TaskBoard
        connected
        tasks={[task("t-selected", "ready"), task("t-other", "open")]}
        selection={{ kind: "task", id: "t-selected" }}
      />,
    );
    expect(screen.getByText("t-selected").closest("li")?.className).toContain("context-match");
    expect(screen.getByText("t-selected").closest("li")?.getAttribute("aria-current")).toBe("true");
    expect(screen.getByText("t-other").closest("li")?.className).not.toContain("context-match");
  });
});

describe("TaskBoard cap headroom", () => {
  it("states the active cap quietly and turns loud from nine tenths full", () => {
    render(
      <TaskBoard
        connected
        tasks={[task("t-1", "open")]}
        truncation={{ totalTasks: 100, truncated: false, taskCap: 500 }}
      />,
    );
    expect(screen.getByText("cap 500")).toBeTruthy();
    expect(screen.queryByText(/near cap/)).toBeNull();
    cleanup();
    render(
      <TaskBoard
        connected
        tasks={[task("t-1", "open")]}
        truncation={{ totalTasks: 450, truncated: false, taskCap: 500 }}
      />,
    );
    expect(screen.getByText("near cap · 450/500").className).toContain("panel__sub--warn");
    expect(screen.queryByText("cap 500")).toBeNull();
  });

  it("shows no gauge at all while the board is served uncapped", () => {
    render(
      <TaskBoard connected tasks={[task("t-1", "open")]} truncation={{ totalTasks: null, truncated: false, taskCap: null }} />,
    );
    expect(screen.queryByText(/cap /)).toBeNull();
  });
});
