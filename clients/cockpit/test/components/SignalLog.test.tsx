// @vitest-environment jsdom
// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — signal log behaviour tests

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SignalLog } from "../../src/components/SignalLog";
import { OPEN_QUERY, type LogQuery } from "../../src/lib/logQuery";
import type { CockpitEvent, EventKind } from "../../src/types";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function eventOf(
  seq: number,
  kind: EventKind,
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

const QUERY: LogQuery = { ...OPEN_QUERY };

describe("SignalLog", () => {
  it("states its provenance plainly and swaps to the brushed-window strip", async () => {
    const onClearWindow = vi.fn();
    render(<SignalLog events={[eventOf(1, "claim")]} provenance="derived" query={QUERY} />);
    expect(screen.getByText("observed transitions")).toBeTruthy();
    cleanup();
    render(<SignalLog events={[eventOf(1, "claim")]} provenance="hub" query={QUERY} />);
    expect(screen.getByText("hub event log")).toBeTruthy();
    cleanup();
    render(
      <SignalLog
        events={[eventOf(1, "claim", { ts: 1_751_800_100 })]}
        provenance="hub"
        query={QUERY}
        window={{ fromTs: 1_751_800_000, toTs: 1_751_800_200 }}
        onClearWindow={onClearWindow}
      />,
    );
    expect(screen.getByText(/1 actor/)).toBeTruthy();
    await userEvent.click(screen.getByText("clear"));
    expect(onClearWindow).toHaveBeenCalled();
  });

  it("distinguishes the three empty states", () => {
    render(<SignalLog events={[]} query={QUERY} />);
    expect(screen.getByText(/No coordination events observed yet/)).toBeTruthy();
    cleanup();
    render(<SignalLog events={[]} query={{ ...QUERY, text: "ghost" }} />);
    expect(screen.getByText("No events match the query.")).toBeTruthy();
    cleanup();
    render(
      <SignalLog
        events={[eventOf(1, "claim", { ts: 5 })]}
        query={QUERY}
        window={{ fromTs: 1_751_900_000, toTs: 1_751_900_100 }}
      />,
    );
    expect(screen.getByText("No observed events inside the brushed window.")).toBeTruthy();
  });

  it("hops by exact seq on the hub feed and by task id on the derived feed", async () => {
    const onSelectTask = vi.fn();
    render(
      <SignalLog
        events={[eventOf(7, "claim", { taskId: "t-7" })]}
        provenance="hub"
        query={QUERY}
        onSelectTask={onSelectTask}
      />,
    );
    await userEvent.click(screen.getByText("event 7"));
    expect(onSelectTask).toHaveBeenCalledWith("7");
    cleanup();
    onSelectTask.mockClear();
    render(
      <SignalLog
        events={[eventOf(8, "claim", { taskId: "t-8" }), eventOf(9, "chat")]}
        provenance="derived"
        query={QUERY}
        onSelectTask={onSelectTask}
      />,
    );
    await userEvent.click(screen.getByText("event 8"));
    expect(onSelectTask).toHaveBeenCalledWith("t-8");
    // A derived chat row without a task names nothing traceable — no hop.
    expect(screen.getByText("event 9").tagName).not.toBe("BUTTON");
  });

  it("drives the query through search, order, view, and reset", async () => {
    const onQueryChange = vi.fn();
    render(
      <SignalLog
        events={[eventOf(1, "claim")]}
        query={{ ...QUERY, text: "x" }}
        onQueryChange={onQueryChange}
      />,
    );
    await userEvent.type(screen.getByLabelText("Search events by actor, task, or text"), "y");
    expect(onQueryChange).toHaveBeenCalledWith(expect.objectContaining({ text: "xy" }));
    await userEvent.click(screen.getByText("newest ↓"));
    expect(onQueryChange).toHaveBeenCalledWith(expect.objectContaining({ order: "oldest" }));
    await userEvent.click(screen.getByText("flat"));
    expect(onQueryChange).toHaveBeenCalledWith(expect.objectContaining({ view: "compact" }));
    await userEvent.click(screen.getByText("reset"));
    expect(onQueryChange).toHaveBeenCalledWith(OPEN_QUERY);
  });

  it("freezes the view on pause and counts what arrived since", async () => {
    const first = [eventOf(2, "claim"), eventOf(1, "claim")];
    const { rerender } = render(<SignalLog events={first} query={QUERY} />);
    await userEvent.click(screen.getByText("pause"));
    rerender(<SignalLog events={[eventOf(3, "release"), ...first]} query={QUERY} />);
    expect(screen.getByText("paused · 1 new")).toBeTruthy();
    // The frozen list still shows the moment of the pause, not the new head.
    expect(screen.queryByText("event 3")).toBeNull();
    await userEvent.click(screen.getByText("paused · 1 new"));
    expect(screen.getByText("event 3")).toBeTruthy();
  });

  it("expands a row's raw payload and verifies inclusion on the hub feed", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("nf", { status: 404 })),
    );
    render(
      <SignalLog
        events={[eventOf(5, "claim", { payload: { target: "x" } })]}
        provenance="hub"
        query={QUERY}
      />,
    );
    await userEvent.click(screen.getByTitle("Show the hub's raw stored event"));
    expect(screen.getByText(/"target": "x"/)).toBeTruthy();
    await userEvent.click(screen.getByText("verify inclusion"));
    await waitFor(() =>
      expect(screen.getByText("proof surface not served (/merkle-proof.json)")).toBeTruthy(),
    );
    // Collapse again.
    await userEvent.click(screen.getByTitle("Show the hub's raw stored event"));
    expect(screen.queryByText(/"target": "x"/)).toBeNull();
  });

  it("keeps the verify affordance off the derived feed", async () => {
    render(
      <SignalLog
        events={[eventOf(5, "claim", { payload: { target: "x" } })]}
        provenance="derived"
        query={QUERY}
      />,
    );
    await userEvent.click(screen.getByTitle("Show the hub's raw stored event"));
    expect(screen.queryByText("verify inclusion")).toBeNull();
  });

  it(
    "states the render cap as a remainder instead of painting past it",
    // A thousand painted rows are genuinely slow when the whole suite's jsdom
    // environments run in parallel; the default 5 s deadline is load-flaky.
    { timeout: 20_000 },
    () => {
      const many = Array.from({ length: 1005 }, (_, index) => eventOf(index + 1, "claim"));
      render(<SignalLog events={many} query={QUERY} />);
      expect(screen.getByText(/\+5 more match — narrow the query to see them/)).toBeTruthy();
    },
  );

  it("groups the compact view by task with the chatter fold", () => {
    render(
      <SignalLog
        events={[
          eventOf(3, "task", { taskId: "t-1" }),
          eventOf(2, "claim", { taskId: "t-1" }),
          eventOf(1, "chat"),
        ]}
        query={{ ...QUERY, view: "compact" }}
        onSelectTask={() => {}}
      />,
    );
    expect(screen.getByText("t-1")).toBeTruthy();
    expect(screen.getByText("chatter · 1")).toBeTruthy();
  });

  it("disables history off the hub feed and states an unserved feed on entry", async () => {
    render(<SignalLog events={[eventOf(1, "claim")]} provenance="derived" query={QUERY} />);
    expect((screen.getByText("history") as HTMLButtonElement).disabled).toBe(true);
    cleanup();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("nf", { status: 404 })));
    render(<SignalLog events={[eventOf(1, "claim")]} provenance="hub" query={QUERY} />);
    await userEvent.click(screen.getByText("history"));
    await waitFor(() => expect(screen.getByText("event feed not served")).toBeTruthy());
  });

  it("scrubs the attested log in history mode and returns to live untouched", async () => {
    const tail = {
      events: [
        { seq: 41, ts: 1_751_800_041, kind: "chat", payload: { text: "hello" } },
        { seq: 42, ts: 1_751_800_042, kind: "task", payload: { task_id: "t-1", status: "done" } },
      ],
      next_cursor: 42,
    };
    // A fresh Response per call — one body cannot be read twice.
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(() =>
        Promise.resolve(new Response(JSON.stringify(tail), { status: 200 })),
      ),
    );
    render(<SignalLog events={[eventOf(1, "claim")]} provenance="hub" query={QUERY} />);
    await userEvent.click(screen.getByText("history"));
    await waitFor(() => expect(screen.getByText(/seq 41–42 of 42/)).toBeTruthy());
    expect(screen.getByLabelText("Scrub position in the hub's event log, by sequence")).toBeTruthy();
    // Pin A, open the comparison — same window on both sides is a zero diff.
    await userEvent.click(screen.getByText("pin A"));
    await userEvent.click(screen.getByText("compare"));
    expect(screen.getAllByText(/seq 41–42 · 2 events/).length).toBe(2);
    await userEvent.click(screen.getByText("live"));
    expect(screen.getByText("event 1")).toBeTruthy();
  });

  it("opens an export for post-mortem review and refuses a malformed file", async () => {
    render(<SignalLog events={[]} query={QUERY} />);
    const picker = screen.getByLabelText("Open a cockpit export for post-mortem review");
    const valid = new File(
      [
        JSON.stringify({
          provenance: "hub",
          exported_at: "2026-07-07T00:00:00Z",
          events: [
            { seq: 9, ts: 1_751_800_009, kind: "claim", lane: "claims", actor: "a", label: "from the file" },
          ],
        }),
      ],
      "export.json",
      { type: "application/json" },
    );
    await userEvent.upload(picker, valid);
    await waitFor(() => expect(screen.getByText(/post-mortem · export\.json · 1 events/)).toBeTruthy());
    expect(screen.getByText("hub event log · file")).toBeTruthy();
    expect(screen.getByText("from the file")).toBeTruthy();
    await userEvent.click(screen.getByText("close file"));
    expect(screen.queryByText("from the file")).toBeNull();
    const invalid = new File(["not json"], "junk.json", { type: "application/json" });
    await userEvent.upload(screen.getByLabelText("Open a cockpit export for post-mortem review"), invalid);
    await waitFor(() => expect(screen.getByText("junk.json is not a cockpit export")).toBeTruthy());
  });

  it("downloads the shown window as a self-describing export", async () => {
    const objectUrl = vi.fn().mockReturnValue("blob:log");
    const revoke = vi.fn();
    Object.assign(URL, { createObjectURL: objectUrl, revokeObjectURL: revoke });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    render(<SignalLog events={[eventOf(1, "claim")]} query={QUERY} />);
    await userEvent.click(screen.getByText("export"));
    expect(objectUrl).toHaveBeenCalledTimes(1);
    expect(click).toHaveBeenCalledTimes(1);
    expect(revoke).toHaveBeenCalledWith("blob:log");
    click.mockRestore();
    cleanup();
    render(<SignalLog events={[]} query={QUERY} />);
    expect((screen.getByText("export") as HTMLButtonElement).disabled).toBe(true);
  });
});
