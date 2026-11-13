// @vitest-environment jsdom
// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — fleet roster behaviour tests

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { FleetRoster } from "../../src/components/FleetRoster";
import type { RosterEntry } from "../../src/lib/roster";
import type { ClaimRecord } from "../../src/types";
import { identText } from "../identText";

afterEach(cleanup);

function claim(taskId: string): ClaimRecord {
  return { task_id: taskId, owner: "x", lease_expires_at: null, paths: [], stale: false, git: null };
}

function entry(agent: string, overrides: Partial<RosterEntry> = {}): RosterEntry {
  return {
    agent,
    status: "idle",
    seat: "listening",
    activeClaims: [],
    staleClaims: [],
    paths: [],
    inConflict: false,
    roles: [],
    ...overrides,
  };
}

describe("FleetRoster", () => {
  it("inspects through a native button with Enter and Space", async () => {
    const inspect = vi.fn();
    render(<FleetRoster roster={[entry("project/worker")]} waiters={0} onInspect={inspect} />);
    const button = screen.getByRole("button", { name: "Inspect agent project/worker" });
    expect(button.tagName).toBe("BUTTON");
    expect(button.getAttribute("aria-haspopup")).toBe("dialog");
    button.focus();
    await userEvent.keyboard("{Enter} ");
    expect(inspect.mock.calls).toEqual([["project/worker"], ["project/worker"]]);
  });

  it("does not advertise a control without an inspection callback", () => {
    render(<FleetRoster roster={[entry("project/worker")]} waiters={0} />);
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("waits honestly with an empty roster", () => {
    render(<FleetRoster roster={[]} waiters={0} />);
    expect(screen.getByText("No agents present. Waiting for the hub.")).toBeTruthy();
  });

  it("counts the live fleet, folds waiters, and splits name from project", () => {
    render(
      <FleetRoster
        roster={[
          entry("quantum/claude-7f3a", { activeClaims: [claim("t-1")] }),
          entry("standalone", { seat: "silent" }),
        ]}
        waiters={3}
      />,
    );
    expect(screen.getByText("Fleet roster").parentElement?.textContent).toContain("1");
    expect(screen.getByText("3 waiting")).toBeTruthy();
    expect(screen.getByText(identText("claude-7f3a")).className).toContain("roster-row__name");
    expect(screen.getByText("quantum")).toBeTruthy();
    expect(screen.getByText(identText("standalone")).className).toContain("roster-row__name");
    expect(screen.getByText("1 claim")).toBeTruthy();
    expect(screen.getByText("no claims")).toBeTruthy();
    const silent = screen.getByText("silent");
    expect(silent.getAttribute("title")).toBe(
      "Nothing recent in the record and no socket receiving its messages.",
    );
    expect(silent.className).toBe("roster-row__tag");
    // A listening seat carries no state tag: only the seats that are not listening say so.
    expect(screen.queryByText("listening")).toBeNull();
    expect(document.querySelectorAll(".roster-row__tag")).toHaveLength(1);
    expect(screen.queryByText("offline")).toBeNull();
  });

  it("marks a seat at work with nobody listening, never offline, and collapses the path tail", () => {
    render(
      <FleetRoster
        roster={[
          entry("quantum/claude", {
            status: "holding",
            seat: "active, not listening",
            activeClaims: [claim("t-1"), claim("t-2")],
            paths: ["a.py", "b.py", "c.py", "d.py", "e.py"],
          }),
        ]}
        waiters={0}
      />,
    );
    const tag = screen.getByText("active, not listening");
    expect(tag.className).toBe("roster-row__tag roster-row__tag--warn");
    expect(document.querySelector(".roster-row--not-listening")).not.toBeNull();
    expect(screen.queryByText("waker missing")).toBeNull();
    expect(screen.getByText("2 claims")).toBeTruthy();
    expect(screen.getByText("+2 more")).toBeTruthy();
    expect(document.querySelector(".roster-row--holding")).not.toBeNull();
  });

  it("opens the agent drawer on click only when inspection is wired", async () => {
    const onInspect = vi.fn();
    render(<FleetRoster roster={[entry("quantum/claude")]} waiters={0} onInspect={onInspect} />);
    await userEvent.click(screen.getByText("claude"));
    expect(onInspect).toHaveBeenCalledWith("quantum/claude");
    cleanup();
    render(<FleetRoster roster={[entry("quantum/claude")]} waiters={0} />);
    expect(document.querySelector(".roster-row--link")).toBeNull();
  });

  it("marks the shared agent selection without hiding other roster rows", () => {
    render(
      <FleetRoster
        roster={[entry("quantum/claude"), entry("fleet/codex")]}
        waiters={0}
        selection={{ kind: "agent", id: "quantum/claude" }}
      />,
    );
    const selected = screen.getByText("claude").closest("li");
    expect(selected?.className).toContain("context-match");
    expect(selected?.getAttribute("aria-current")).toBe("true");
    expect(screen.getByText("codex")).toBeTruthy();
  });
});

describe("FleetRoster roles", () => {
  it("chips each bound role apart from the warning tags", () => {
    render(
      <FleetRoster
        roster={[entry("quantum/claude", { roles: ["reviewer", "release-captain"] })]}
        waiters={0}
      />,
    );
    expect(screen.getByText("reviewer").className).toContain("roster-row__tag--role");
    expect(screen.getByText("release-captain")).toBeTruthy();
  });
});
