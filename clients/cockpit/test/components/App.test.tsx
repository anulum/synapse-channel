// @vitest-environment jsdom
// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — app shell smoke tests: the wired deck against a stubbed hub

import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
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

function accessDescriptor(role: "viewer" | "operator" | "admin"): object {
  const writes = role !== "viewer";
  return {
    version: 1,
    principal: role,
    role,
    capabilities: {
      read: true,
      message_send: writes,
      task_declare: writes,
      task_update: writes,
    },
    operator_armed: true,
    trust_boundary: "presentation hints only; HTTP and hub policy enforce writes",
  };
}

beforeEach(() => {
  window.history.replaceState(null, "", "/cockpit/");
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
      if (url.startsWith("/dashboard-access.json")) {
        return Promise.resolve(new Response(JSON.stringify(accessDescriptor("viewer"))));
      }
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
    await waitFor(() => expect(screen.getByText("worker")).toBeTruthy());
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("SYNAPSE");
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
    await waitFor(() => expect(screen.getByText("worker")).toBeTruthy());
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

  it("arms fleet history from the URL workspace and states an unserved reconstruction surface", async () => {
    render(<App />);
    await waitFor(() => expect(screen.getByText("worker")).toBeTruthy());
    const modeGroup = screen.getByRole("group", { name: "Fleet evidence time mode" });
    within(modeGroup).getByRole("button", { name: "history" }).click();
    await waitFor(() =>
      expect(screen.getByText("state-at surface not served (--feeds-db)")).toBeTruthy(),
    );
    expect(location.search).toContain("replay=history");
    within(modeGroup).getByRole("button", { name: "live" }).click();
    await waitFor(() => expect(location.search).not.toContain("replay"));
  });

  it("restores and updates shareable communication filters through the app shell", async () => {
    window.history.replaceState(
      null,
      "",
      "/cockpit/?panel=fleet&comm=quantum&delivery=failed",
    );
    render(<App />);
    await waitFor(() => expect(screen.getByText("worker")).toBeTruthy());
    expect(screen.getByRole("tab", { name: "fleet" }).getAttribute("aria-selected")).toBe("true");
    const query = screen.getByLabelText("identity or project") as HTMLInputElement;
    const health = screen.getByLabelText("delivery health") as HTMLSelectElement;
    expect(query.value).toBe("quantum");
    expect(health.value).toBe("failed");

    await userEvent.clear(query);
    await userEvent.type(query, "worker");
    await userEvent.selectOptions(health, "unknown");
    expect(location.search).toContain("comm=worker");
    expect(location.search).toContain("delivery=unknown");
    await userEvent.click(screen.getByRole("button", { name: "clear filters" }));
    expect(location.search).not.toContain("comm=");
    expect(location.search).not.toContain("delivery=");
  });

  it("keeps an exact selected event in a principal-scoped guided incident draft", async () => {
    window.history.replaceState(null, "", "/cockpit/?event=42");
    render(<App />);
    await waitFor(() => expect(screen.getByText("worker")).toBeTruthy());
    await userEvent.click(screen.getByRole("tab", { name: "incident" }));
    await userEvent.type(screen.getByLabelText("Incident title"), "Exact event review");
    await userEvent.click(screen.getByRole("button", { name: /continue to evidence/u }));
    expect(location.search).toContain("panel=incident");
    expect(location.search).toContain("incident=evidence");
    expect(location.search).toContain("event=42");
    await userEvent.click(screen.getByRole("button", { name: "add current selection" }));
    expect(screen.getByText("1 explicit reference")).toBeTruthy();
    expect(localStorage.getItem("synapse-cockpit-incident-v1:viewer")).toContain("event:42");
    await userEvent.click(screen.getByRole("button", { name: "open" }));
    expect(screen.getByRole("tab", { name: /signal log/u }).getAttribute("aria-selected")).toBe("true");
    expect(location.search).not.toContain("incident=");
    expect(location.search).toContain("event=42");
  });

  it("unlocks with the session bearer and clears the live deck when that bearer is revoked", async () => {
    let revoked = false;
    const fetcher = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const authorization = new Headers(init?.headers).get("Authorization");
      if (url.startsWith("/dashboard-access.json") || url.startsWith("/snapshot.json")) {
        if (authorization !== "Bearer current-token" || revoked) {
          return Promise.resolve(new Response("no", { status: 401 }));
        }
        if (url.startsWith("/dashboard-access.json")) {
          return Promise.resolve(new Response(JSON.stringify(accessDescriptor("operator"))));
        }
        return Promise.resolve(new Response(JSON.stringify(SNAPSHOT), { status: 200 }));
      }
      return Promise.resolve(new Response("nf", { status: 404 }));
    });
    vi.stubGlobal("fetch", fetcher);
    render(<App />);

    await waitFor(() => expect(screen.getByText("Unlock cockpit")).toBeTruthy());
    expect(screen.queryByText("worker")).toBeNull();
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "k", ctrlKey: true }));
    expect(screen.queryByLabelText("Search commands")).toBeNull();
    await userEvent.type(screen.getByLabelText("Dashboard bearer token"), "wrong-token");
    await userEvent.click(screen.getByText("unlock cockpit"));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("refused"));
    expect(sessionStorage.getItem(COCKPIT_BEARER_KEY)).toBeNull();

    await userEvent.type(screen.getByLabelText("Dashboard bearer token"), "current-token");
    await userEvent.click(screen.getByText("unlock cockpit"));
    await waitFor(() => expect(screen.getByText("worker")).toBeTruthy());
    expect(sessionStorage.getItem(COCKPIT_BEARER_KEY)).toBe("current-token");
    expect(localStorage.getItem(COCKPIT_BEARER_KEY)).toBeNull();

    revoked = true;
    await authenticatedFetch("/snapshot.json");
    await waitFor(() => expect(screen.getByText("Unlock cockpit")).toBeTruthy());
    expect(screen.queryByText("worker")).toBeNull();
    expect(
      fetcher.mock.calls.every(([input]) => !String(input).includes("current-token")),
    ).toBe(true);
  });

  it("removes write DOM and restores command focus on a capability downgrade", async () => {
    let role: "viewer" | "operator" = "operator";
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.startsWith("/dashboard-access.json")) {
          return Promise.resolve(new Response(JSON.stringify(accessDescriptor(role))));
        }
        if (url.startsWith("/snapshot.json")) {
          return Promise.resolve(new Response(JSON.stringify(SNAPSHOT)));
        }
        return Promise.resolve(new Response("nf", { status: 404 }));
      }),
    );
    render(<App />);
    await waitFor(() => expect(screen.getByText("operator · operator")).toBeTruthy());
    const trigger = screen.getByRole("button", { name: "Open command palette" });
    await userEvent.click(trigger);
    expect(screen.getAllByText(/^operator:/u)).toHaveLength(3);

    role = "viewer";
    act(() => window.dispatchEvent(new Event("focus")));
    await waitFor(() => expect(screen.getByText("viewer · viewer")).toBeTruthy());
    await waitFor(() => expect(screen.queryByLabelText("Search commands")).toBeNull());
    expect(document.activeElement).toBe(trigger);
    expect(screen.getByText("Dashboard access changed; write controls were removed.")).toBeTruthy();

    await userEvent.click(trigger);
    expect(screen.queryByText(/^operator:/u)).toBeNull();
    await userEvent.type(screen.getByLabelText("Search commands"), "operator");
    expect(screen.getByText("no command matches")).toBeTruthy();
  });
});
