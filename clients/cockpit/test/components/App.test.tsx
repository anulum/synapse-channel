// @vitest-environment jsdom
// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — app shell smoke tests: the wired deck against a stubbed hub

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "../../src/App";
import {
  authenticatedFetch,
  COCKPIT_BEARER_KEY,
  resetCockpitAuth,
} from "../../src/lib/auth";

const SNAPSHOT = {
  online_agents: ["quantum/worker"],
  hub_version: "0.98.5",
  config_epoch: "deadbeefcafe0123",
  state: {
    dead_letters: [{ target: "ghost/agent", count: 2, last_sender: "CEO", last_ts: 1 }],
    pending_relay_approvals: [
      { action: "task_update", namespace: "quantum", task_id: "t-1", requester: "op-a" },
    ],
  },
  board: {},
  fleet: {
    agents: { live: ["quantum/worker"], waiters: [], missing_waiters: [] },
    claims: { active: 0, stale: 0, active_claims: [], stale_claims: [] },
    branch_conflicts: [],
    task_graph: { nodes: [], edges: [] },
    receipts: [],
  },
  risk: { level: "green", signals: [], safe_next_work: [] },
};

beforeEach(() => {
  window.sessionStorage.clear();
  resetCockpitAuth();
  vi.stubGlobal(
    "matchMedia",
    vi.fn().mockReturnValue({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() }),
  );
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith("/snapshot.json")) {
        return Promise.resolve(new Response(JSON.stringify(SNAPSHOT), { status: 200 }));
      }
      // Every optional feed is honest-absent in this smoke.
      return Promise.resolve(new Response("nf", { status: 404 }));
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  localStorage.clear();
  sessionStorage.clear();
  document.documentElement.removeAttribute("data-theme");
  document.documentElement.removeAttribute("data-density");
});

describe("App", () => {
  it("wires the whole deck: HUD goes live, panels fill, optional feeds state absence", async () => {
    render(<App />);
    // The snapshot lands: the beacon flips live and the roster names the agent.
    await waitFor(() => expect(screen.getByText("live")).toBeTruthy());
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("SYNAPSE");
    expect(screen.getByText("worker")).toBeTruthy();
    // Hub-recorded facts flow to the rail: dead letters + the pending quorum.
    expect(screen.getByText("dead letters · nobody listening")).toBeTruthy();
    expect(screen.getByText("pending approvals · awaiting a second operator")).toBeTruthy();
    expect(screen.getByText("in quantum · requested by op-a")).toBeTruthy();
    // The version pin chip carries the hub version + epoch prefix.
    expect(screen.getByText(/v0\.98\.5/).textContent).toContain("epoch deadbeef");
    // Optional feeds answer 404 → the reliability panel states that plainly.
    await waitFor(() =>
      expect(screen.getByText(/does not serve reliability evidence yet/)).toBeTruthy(),
    );
    await userEvent.click(screen.getByRole("tab", { name: "audit" }));
    await waitFor(() => expect(screen.getByText(/Receipts feed absent/u)).toBeTruthy());
    expect(screen.getByText(/Operator actions feed absent/u)).toBeTruthy();
    expect(screen.getByText("The board is empty — no tasks declared.")).toBeTruthy();
  });

  it("opens the palette on Ctrl+K and toggles the theme end to end", async () => {
    const { unmount } = render(<App />);
    await waitFor(() => expect(screen.getByText("live")).toBeTruthy());
    // Ctrl+K opens; Escape closes.
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "k", ctrlKey: true }));
    await waitFor(() => expect(screen.getByLabelText("Search commands")).toBeTruthy());
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    await waitFor(() => expect(screen.queryByLabelText("Search commands")).toBeNull());
    // The theme toggle flips the document attribute and persists the choice.
    screen.getByLabelText("Switch to light theme").click();
    await waitFor(() =>
      expect(document.documentElement.getAttribute("data-theme")).toBe("light"),
    );
    expect(localStorage.getItem("cockpit-theme")).toBe("light");
    unmount();
  });

  it("arms fleet time-travel and states the unserved reconstruction surface", async () => {
    render(<App />);
    await waitFor(() => expect(screen.getByText("live")).toBeTruthy());
    screen.getByText("time travel").click();
    await waitFor(() =>
      expect(screen.getByText("state-at surface not served (--feeds-db)")).toBeTruthy(),
    );
    screen.getByText("back to now").click();
    await waitFor(() => expect(screen.getByText("time travel")).toBeTruthy());
  });

  it("unlocks with the session bearer and clears the live deck when that bearer is revoked", async () => {
    let revoked = false;
    const fetcher = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const authorization = new Headers(init?.headers).get("Authorization");
      if (url.startsWith("/snapshot.json")) {
        if (authorization !== "Bearer current-token" || revoked) {
          return Promise.resolve(new Response("no", { status: 401 }));
        }
        return Promise.resolve(new Response(JSON.stringify(SNAPSHOT), { status: 200 }));
      }
      return Promise.resolve(new Response("nf", { status: 404 }));
    });
    vi.stubGlobal("fetch", fetcher);
    render(<App />);

    await waitFor(() => expect(screen.getByText("Unlock operator cockpit")).toBeTruthy());
    expect(screen.queryByText("worker")).toBeNull();
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "k", ctrlKey: true }));
    expect(screen.queryByLabelText("Search commands")).toBeNull();
    await userEvent.type(screen.getByLabelText("Dashboard bearer token"), "wrong-token");
    await userEvent.click(screen.getByText("unlock cockpit"));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("refused"));
    expect(sessionStorage.getItem(COCKPIT_BEARER_KEY)).toBeNull();

    await userEvent.type(screen.getByLabelText("Dashboard bearer token"), "current-token");
    await userEvent.click(screen.getByText("unlock cockpit"));
    await waitFor(() => expect(screen.getByText("live")).toBeTruthy());
    expect(screen.getByText("worker")).toBeTruthy();
    expect(sessionStorage.getItem(COCKPIT_BEARER_KEY)).toBe("current-token");
    expect(localStorage.getItem(COCKPIT_BEARER_KEY)).toBeNull();

    revoked = true;
    await authenticatedFetch("/snapshot.json");
    await waitFor(() => expect(screen.getByText("Unlock operator cockpit")).toBeTruthy());
    expect(screen.queryByText("worker")).toBeNull();
    expect(
      fetcher.mock.calls.every(([input]) => !String(input).includes("current-token")),
    ).toBe(true);
  });
});
