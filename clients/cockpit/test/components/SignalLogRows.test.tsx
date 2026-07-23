// @vitest-environment jsdom
// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — bounded signal-log evidence renderer contracts

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { RENDERED_ROWS_CAP, SignalLogRows } from "../../src/components/SignalLogRows";
import { fetchAndVerify } from "../../src/lib/merkleVerify";
import type { CockpitEvent, EventKind } from "../../src/types";

vi.mock("../../src/lib/merkleVerify", () => ({ fetchAndVerify: vi.fn() }));

function eventOf(
  seq: number,
  kind: EventKind = "claim",
  overrides: Partial<CockpitEvent> = {},
): CockpitEvent {
  return {
    seq,
    ts: 1_751_800_000 + seq,
    kind,
    lane: kind === "chat" ? "presence" : "claims",
    severity: 0.4,
    actor: `agent-${seq}`,
    label: `event ${seq}`,
    taskId: "",
    ...overrides,
  };
}

afterEach(() => {
  cleanup();
  vi.mocked(fetchAndVerify).mockReset();
});

describe("SignalLogRows", () => {
  it("renders flat evidence, selection, exact event selection, and trace navigation", async () => {
    const onSelectTask = vi.fn();
    const onSelectEvent = vi.fn();
    render(
      <SignalLogRows
        events={[
          eventOf(8, "claim", { actor: "alpha", taskId: "t-8" }),
          eventOf(7, "chat", { actor: "beta" }),
        ]}
        view="flat"
        navigationProvenance="hub"
        evidenceProvenance="hub"
        onSelectTask={onSelectTask}
        onSelectEvent={onSelectEvent}
        selection={{ kind: "agent", id: "alpha" }}
      />,
    );

    expect(screen.getByText("event 8").closest("tr")?.className).toContain("context-match");
    expect(screen.getByText("event 7").closest("tr")?.className).not.toContain("context-match");
    await userEvent.click(screen.getByText("event 8"));
    expect(onSelectTask).toHaveBeenCalledWith("8");
    expect(screen.getByText("event 7").tagName).not.toBe("BUTTON");
    await userEvent.click(screen.getByRole("button", { name: "Select event sequence 8" }));
    expect(onSelectEvent).toHaveBeenCalledWith(8);
  });

  it("expands raw evidence and states every proof-verification outcome", async () => {
    vi.mocked(fetchAndVerify)
      .mockResolvedValueOnce({ kind: "verified", root: "1234567890abcdef" })
      .mockResolvedValueOnce({ kind: "mismatch" })
      .mockResolvedValueOnce({ kind: "absent", note: "pruned" })
      .mockResolvedValueOnce({ kind: "unserved" })
      .mockResolvedValueOnce({ kind: "error", message: "offline" });
    render(
      <SignalLogRows
        events={[eventOf(5, "claim", { payload: { target: "x" } })]}
        view="flat"
        navigationProvenance="derived"
        evidenceProvenance="hub"
      />,
    );

    await userEvent.click(screen.getByTitle("Show the hub's raw stored event"));
    expect(screen.getByText(/"target": "x"/)).toBeTruthy();
    const verify = screen.getByText("verify inclusion");
    const expected = [
      /committed to root 1234567890ab/,
      "✗ proof did not reconstruct the claimed root",
      "not in the committed tree: pruned",
      "proof surface not served (/merkle-proof.json)",
      "verify failed: offline",
    ];
    for (const label of expected) {
      await userEvent.click(verify);
      await waitFor(() => expect(screen.getByText(label)).toBeTruthy());
    }
    await userEvent.click(screen.getByTitle("Show the hub's raw stored event"));
    expect(screen.queryByText(/"target": "x"/)).toBeNull();
  });

  it("keeps proof verification off derived evidence", async () => {
    render(
      <SignalLogRows
        events={[eventOf(5, "claim", { payload: { target: "x" } })]}
        view="flat"
        navigationProvenance="derived"
        evidenceProvenance="derived"
      />,
    );
    await userEvent.click(screen.getByTitle("Show the hub's raw stored event"));
    expect(screen.queryByText("verify inclusion")).toBeNull();
  });

  it("groups task lifecycles, folds chatter, and states the render remainder", () => {
    const onSelectTask = vi.fn();
    const events = Array.from({ length: RENDERED_ROWS_CAP + 1 }, (_, index) =>
      eventOf(index + 1, "claim", { taskId: "shared-task" }),
    );
    render(
      <SignalLogRows
        events={events}
        view="compact"
        navigationProvenance="hub"
        evidenceProvenance="hub"
        selection={{ kind: "task", id: "shared-task" }}
        onSelectTask={onSelectTask}
      />,
    );
    const task = screen.getByText("shared-task");
    expect(task.closest(".log-group")?.className).toContain("context-match");
    task.click();
    expect(onSelectTask).toHaveBeenCalledWith("shared-task");
    expect(screen.getByText(/\+1 more match beyond the render cap/)).toBeTruthy();
    cleanup();

    render(
      <SignalLogRows
        events={Array.from({ length: 41 }, (_, index) =>
          eventOf(index + 1, "chat", { actor: index === 0 ? "" : `agent-${index + 1}` }),
        )}
        view="compact"
        navigationProvenance="derived"
        evidenceProvenance="derived"
        selection={{ kind: "agent", id: "agent-2" }}
      />,
    );
    expect(screen.getByText("chatter · 41")).toBeTruthy();
    expect(screen.getByText("+1")).toBeTruthy();
    cleanup();

    render(
      <SignalLogRows
        events={[eventOf(1, "claim", { taskId: "read-only-task" })]}
        view="compact"
        navigationProvenance="derived"
        evidenceProvenance="derived"
      />,
    );
    expect(screen.getByText("read-only-task").tagName).toBe("SPAN");
  });

  it("navigates derived rows by task id", async () => {
    const onSelectTask = vi.fn();
    render(
      <SignalLogRows
        events={[eventOf(3, "task", { taskId: "task-3" })]}
        view="flat"
        navigationProvenance="derived"
        evidenceProvenance="derived"
        onSelectTask={onSelectTask}
      />,
    );
    await userEvent.click(screen.getByText("event 3"));
    expect(onSelectTask).toHaveBeenCalledWith("task-3");
  });

  it("marks an exact selected event button as pressed", () => {
    render(
      <SignalLogRows
        events={[eventOf(4)]}
        view="flat"
        navigationProvenance="hub"
        evidenceProvenance="hub"
        selection={{ kind: "event", seq: 4 }}
        onSelectEvent={() => {}}
      />,
    );
    expect(screen.getByRole("button", { name: "Select event sequence 4" }).getAttribute("aria-pressed")).toBe("true");
  });
});
