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

afterEach(cleanup);

function claim(taskId: string): ClaimRecord {
  return { task_id: taskId, owner: "x", lease_expires_at: null, paths: [], stale: false, git: null };
}

function entry(agent: string, overrides: Partial<RosterEntry> = {}): RosterEntry {
  return {
    agent,
    status: "idle",
    online: true,
    activeClaims: [],
    staleClaims: [],
    paths: [],
    inConflict: false,
    wakerMissing: false,
    roles: [],
    ...overrides,
  };
}

describe("FleetRoster", () => {
  it("waits honestly with an empty roster", () => {
    render(<FleetRoster roster={[]} waiters={0} />);
    expect(screen.getByText("No agents present. Waiting for the hub.")).toBeTruthy();
  });

  it("counts the live fleet, folds waiters, and splits name from project", () => {
    render(
      <FleetRoster
        roster={[
          entry("quantum/claude-7f3a", { activeClaims: [claim("t-1")] }),
          entry("standalone", { online: false }),
        ]}
        waiters={3}
      />,
    );
    expect(screen.getByText("Fleet roster").parentElement?.textContent).toContain("1");
    expect(screen.getByText("3 waiting")).toBeTruthy();
    expect(screen.getByText("claude-7f3a")).toBeTruthy();
    expect(screen.getByText("quantum")).toBeTruthy();
    expect(screen.getByText("standalone")).toBeTruthy();
    expect(screen.getByText("1 claim")).toBeTruthy();
    expect(screen.getByText("no claims")).toBeTruthy();
    expect(screen.getByText("offline")).toBeTruthy();
  });

  it("marks the waker-missing honesty tag and collapses the path tail", () => {
    render(
      <FleetRoster
        roster={[
          entry("quantum/claude", {
            status: "holding",
            wakerMissing: true,
            activeClaims: [claim("t-1"), claim("t-2")],
            paths: ["a.py", "b.py", "c.py", "d.py", "e.py"],
          }),
        ]}
        waiters={0}
      />,
    );
    expect(screen.getByText("waker missing")).toBeTruthy();
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
