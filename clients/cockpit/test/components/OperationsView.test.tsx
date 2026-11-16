// @vitest-environment jsdom
// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — the operations tab: the list by state, one chain and each link's evidence

import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { OperationsView } from "../../src/components/OperationsView";
import { ScopeContext } from "../../src/components/ScopeTag";
import type { VerifyResult } from "../../src/lib/merkleVerify";
import type { OperationsSource } from "../../src/lib/operations";
import { createOperationsFeed, fetchOperationChain } from "../../src/lib/operationsFeed";
import type { CockpitScope } from "../../src/lib/scope";

const verifyRow = vi.fn<(seq: number) => Promise<VerifyResult>>();
let fetcher = vi.fn(async (_input: RequestInfo | URL) => new Response("{}", { status: 500 }));

beforeEach(() => {
  verifyRow.mockReset();
});

afterEach(() => {
  cleanup();
});

function listRow(operationId: string, state: string, declaredSeq: number): Record<string, unknown> {
  return {
    operation_id: operationId,
    task_id: "T1",
    declarer: "P/worker",
    declared_seq: declaredSeq,
    state,
    latest_seq: declaredSeq + 2,
    latest_kind: "answer",
    latest_at: 1_800_000_000,
  };
}

const PAGE = {
  operations: [
    listRow("op-open", "attempt open", 30),
    listRow("op-refused", "refused: not_approved", 20),
    listRow("op-new", "brand new state", 15),
    listRow("op-verified", "verified", 10),
  ],
  total: 4,
  next_before: null,
  limit: 50,
  as_of_seq: 44,
  custody: "not recorded here",
};

function chain(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    operation_id: "op-verified",
    task_id: "T1",
    state: "verified",
    intent_sha256: "b".repeat(64),
    digest_check: "matches",
    attempts: [{ attempt_id: "att-1", executor: "P/worker", state: "verified" }],
    chain: [
      { seq: 10, at: 1_800_000_000, kind: "declared", actor: "P/worker", detail: { effect: "file" } },
      { seq: 12, at: 1_800_000_001, kind: "approval", actor: "", detail: { state: "approved", reason: "<b>trust me</b>" } },
      { seq: 13, at: null, kind: "answer", actor: "P/worker", detail: { answer: "approved_fresh" } },
      { seq: 14, at: 1_800_000_003, kind: "custody", actor: "R/x", detail: {} },
    ],
    as_of_seq: 44,
    as_of: 1_800_000_009,
    custody: "not recorded here",
    present: true,
    ...overrides,
  };
}

type Route = (url: string) => { status: number; body: unknown };

/** Answer the tab's reads from `route`, through the real list feed and chain read. */
function stubRoutes(route: Route): typeof fetcher {
  fetcher = vi.fn(async (input: RequestInfo | URL) => {
    const answer = route(String(input));
    return new Response(JSON.stringify(answer.body), { status: answer.status });
  });
  return fetcher;
}

function urls(calls: typeof fetcher): string[] {
  return calls.mock.calls.map((call) => String(call[0]));
}

const SOURCE: OperationsSource = {
  openList: (scope, before) => createOperationsFeed(scope, before, fetcher as unknown as typeof fetch),
  readChain: (operationId, scope) => fetchOperationChain(operationId, scope, fetcher as unknown as typeof fetch),
  verifyRow,
};

function renderView(scope: CockpitScope = { kind: "all" }): void {
  render(
    <ScopeContext.Provider value={scope}>
      <OperationsView source={SOURCE} />
    </ScopeContext.Provider>,
  );
}

describe("OperationsView", () => {
  it("lists the operations by state in the report's order, with an unknown state shown verbatim", async () => {
    stubRoutes(() => ({ status: 200, body: PAGE }));
    renderView();
    expect(screen.getByText("reading the journalled operations…")).toBeTruthy();
    await screen.findByText("4 of 4 declared operations, newest first, as of journal seq 44");
    const groups = screen.getAllByRole("list").map((list) => list.getAttribute("aria-labelledby"));
    const heads = groups.map((id) => document.getElementById(String(id))?.textContent);
    expect(heads).toEqual(["verified 1", "attempt open 1", "refused: not_approved 1", "brand new state not a state this cockpit knows; shown as the hub gave it 1"]);
    expect(screen.getByText("not a state this cockpit knows; shown as the hub gave it")).toBeTruthy();
    const row = screen.getByRole("button", { name: /op-refused/u });
    expect(row.textContent).toContain("declared by P/worker");
    expect(row.textContent).toContain("latest: answer at seq 22 · 2027-01-15T08:00:00.000Z");
    expect(screen.getByText("Choose an operation to read its chain.")).toBeTruthy();
  });

  it("opens one chain, link by link, with the fields its rows hold and nothing more", async () => {
    const fetcher = stubRoutes((url) => ({ status: 200, body: url.includes("operation=") ? chain() : PAGE }));
    renderView();
    await userEvent.click(await screen.findByRole("button", { name: /op-verified/u }));
    const section = await screen.findByRole("region", { name: "Chain of operation op-verified" });
    expect(urls(fetcher)).toContain("/operations.json?operation=op-verified");
    expect(screen.getByRole("button", { name: /op-verified/u }).getAttribute("aria-pressed")).toBe("true");
    const links = within(within(section).getByRole("list", { name: "Recorded rows" })).getAllByRole("listitem");
    expect(links.map((link) => link.querySelector(".op-link__phase")?.textContent)).toEqual(["A", "B", "C", "?"]);
    expect(links.map((link) => link.querySelector(".op-link__kind")?.textContent)).toEqual(["intent", "approval", "hub's answer", "custody"]);
    expect(links[1]?.textContent).toContain("by not recorded");
    expect(links[1]?.querySelector("b")).toBeNull();
    expect(links[1]?.textContent).toContain("<b>trust me</b>");
    expect(links[2]?.querySelector(".op-link__time")?.textContent).toBe("not recorded");
    expect(links[3]?.querySelector("dl")).toBeNull();
    expect(section.textContent).toContain(`intent ${"b".repeat(64)} · the kept intent's canonical digest matches`);
    expect(section.textContent).toContain("attempt att-1 by P/worker");
    expect(section.textContent).toContain("as of journal seq 44");
    expect(section.textContent).toContain("custody: not recorded here");
  });

  it("brings the chosen chain into view, as it sits below the list in a narrow inspector", async () => {
    const scroll = vi.fn();
    Object.defineProperty(Element.prototype, "scrollIntoView", { configurable: true, value: scroll });
    try {
      stubRoutes((url) => ({ status: 200, body: url.includes("operation=") ? chain() : PAGE }));
      renderView();
      await userEvent.click(await screen.findByRole("button", { name: /op-verified/u }));
      await screen.findByRole("list", { name: "Recorded rows" });
      expect(scroll).toHaveBeenCalledWith({ block: "nearest" });
      expect(scroll.mock.contexts.at(-1)).toBe(screen.getByRole("region", { name: "Chain of operation op-verified" }));
    } finally {
      Reflect.deleteProperty(Element.prototype, "scrollIntoView");
    }
  });

  it("checks each link's inclusion proof in the browser and says what it found", async () => {
    stubRoutes((url) => ({ status: 200, body: url.includes("operation=") ? chain() : PAGE }));
    renderView();
    await userEvent.click(await screen.findByRole("button", { name: /op-verified/u }));
    const open = await screen.findByRole("button", { name: "open evidence (seq 10)" });
    const outcomes = [
      [{ kind: "verified", root: "f".repeat(64) }, `seq 10 is committed to root ${"f".repeat(64)}`],
      [{ kind: "mismatch" }, "the proof does not reach the root: do not trust this row"],
      [{ kind: "absent", note: "seq beyond the store" }, "no proof: seq beyond the store"],
      [{ kind: "unserved" }, "this dashboard serves no inclusion proofs (/merkle-proof.json)"],
      [{ kind: "error", message: "offline" }, "the proof could not be checked: offline"],
    ] as const;
    for (const [result, text] of outcomes) {
      let finish: (value: typeof result) => void = () => undefined;
      verifyRow.mockReturnValueOnce(new Promise((done) => { finish = done; }));
      await userEvent.click(open);
      expect(screen.getByText("checking the inclusion proof…")).toBeTruthy();
      expect((open as HTMLButtonElement).disabled).toBe(true);
      finish(result);
      expect(await screen.findByText(text)).toBeTruthy();
    }
    expect(verifyRow).toHaveBeenCalledWith(10);
  });

  it("says what a silent attempt and an unread report mean, and marks a digest that differs", async () => {
    let state = "attempt open, silent";
    let check = "differs";
    stubRoutes((url) => ({ status: 200, body: url.includes("operation=") ? chain({ state, digest_check: check }) : PAGE }));
    renderView();
    await userEvent.click(await screen.findByRole("button", { name: /op-open/u }));
    expect(await screen.findByText(/an open attempt ends only by its report, an observed effect or a seal/u)).toBeTruthy();
    expect(document.querySelector(".op-digest--differs")?.textContent).toContain("differs from the recorded digest");
    state = "outcome reported, unknown";
    check = "something else";
    await userEvent.click(screen.getByRole("button", { name: /op-verified/u }));
    expect(await screen.findByText(/the outcome stays unknown until an observer reads it/u)).toBeTruthy();
    expect(document.querySelector(".op-digest--plain")?.textContent).toContain("something else");
  });

  it("states a chain without an intent, an id with nothing recorded, and a scope it does not narrow", async () => {
    let body: Record<string, unknown> = chain({ intent_sha256: "", digest_check: "no intent", attempts: [], task_id: "", custody: "", as_of_seq: null });
    stubRoutes((url) => ({ status: 200, body: url.includes("operation=") ? body : PAGE }));
    renderView({ kind: "project", namespace: "Q" });
    await userEvent.click(await screen.findByRole("button", { name: /op-verified/u }));
    const section = await screen.findByRole("region", { name: "Chain of operation op-verified" });
    expect(section.textContent).toContain("no intent document was kept · no intent to check");
    expect(section.textContent).toContain("task not recorded");
    expect(section.textContent).toContain("as of journal seq not recorded");
    expect(section.textContent).toContain("custody: not recorded");
    expect(within(section).queryByRole("list", { name: "Attempts" })).toBeNull();
    body = { ...chain(), scope: { scope: "project:Q", applied: false, reason: "one record is named" }, present: false, operation_id: "op-refused" };
    await userEvent.click(screen.getByRole("button", { name: /op-refused/u }));
    expect(await screen.findByText("Nothing is recorded under op-refused.")).toBeTruthy();
    body = { ...chain(), scope: { scope: "project:Q", applied: false, reason: "one record is named" } };
    await userEvent.click(screen.getByRole("button", { name: /op-open/u }));
    expect(await screen.findByText("One named operation is shown whole; the scope does not narrow it.")).toBeTruthy();
  });

  it("says when the dashboard serves no chain, or the chain could not be read", async () => {
    let status = 404;
    stubRoutes((url) => (url.includes("operation=") ? { status, body: {} } : { status: 200, body: PAGE }));
    renderView();
    await userEvent.click(await screen.findByRole("button", { name: /op-verified/u }));
    expect(await screen.findByText(/This dashboard does not serve operations/u)).toBeTruthy();
    status = 500;
    await userEvent.click(screen.getByRole("button", { name: /op-open/u }));
    expect(await screen.findByText("The chain could not be read: hub returned 500")).toBeTruthy();
  });

  it("says the feed is absent, empty, empty in a scope, unreadable in part, or failing", async () => {
    stubRoutes(() => ({ status: 404, body: {} }));
    renderView();
    expect(await screen.findByText(/Start it with --feeds-db/u)).toBeTruthy();
    cleanup();
    stubRoutes(() => ({ status: 200, body: { operations: [], total: 0, next_before: null, as_of_seq: 2 } }));
    renderView();
    expect(await screen.findByText("No operation is declared in this store.")).toBeTruthy();
    cleanup();
    const scoped = { operations: [{ declared_seq: 1 }], scope: { scope: "project:Q", applied: true } };
    stubRoutes(() => ({ status: 200, body: scoped }));
    renderView({ kind: "project", namespace: "Q" });
    expect(await screen.findByText("No declared operation is in this scope.")).toBeTruthy();
    expect(screen.getByText("1 rows could not be read and are not shown")).toBeTruthy();
    expect(screen.getByText("0 of not recorded declared operations, newest first, as of journal seq not recorded")).toBeTruthy();
    cleanup();
    stubRoutes(() => ({ status: 500, body: {} }));
    renderView();
    expect(await screen.findByText("The operations could not be read: hub returned 500")).toBeTruthy();
  });

  it("pages to older operations by the cursor and back to the newest", async () => {
    const fetcher = stubRoutes((url) => ({
      status: 200,
      body: url.includes("before=") ? { ...PAGE, operations: [listRow("op-old", "declared", 3)], next_before: null } : { ...PAGE, next_before: 10 },
    }));
    renderView({ kind: "project", namespace: "P" });
    await userEvent.click(await screen.findByRole("button", { name: "older operations" }));
    expect(await screen.findByRole("button", { name: /op-old/u })).toBeTruthy();
    expect(urls(fetcher)).toContain("/operations.json?before=10&scope=project%3AP");
    expect(screen.queryByRole("button", { name: "older operations" })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "newest operations" }));
    expect(await screen.findByRole("button", { name: /op-verified/u })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "newest operations" })).toBeNull();
  });

  it("leaves blank fields of a row as not recorded", async () => {
    const bare = { operation_id: "op-bare", declared_seq: 5 };
    stubRoutes(() => ({ status: 200, body: { ...PAGE, operations: [bare] } }));
    renderView();
    const row = await screen.findByRole("button", { name: /op-bare/u });
    expect(row.textContent).toContain("task not recorded");
    expect(row.textContent).toContain("declared by not recorded");
    expect(row.textContent).toContain("latest: not recorded at seq not recorded · not recorded");
    await waitFor(() => expect(document.querySelector(".op-state__text")?.textContent).toBe("not recorded"));
  });
});

describe("the approval view", () => {
  const recorded = "b".repeat(64);
  const awaiting = (overrides: Record<string, unknown> = {}): Record<string, unknown> =>
    chain({
      operation_id: "op-awaiting",
      state: "awaiting approval",
      intent_sha256: recorded,
      recomputed_sha256: recorded,
      digest_check: "matches",
      attempts: [],
      intent_fields: [
        { field: "budget", canonical: '"not_applicable"' },
        { field: "tool", canonical: '{"name":"render-report","version":"1.0.0"}' },
      ],
      chain: [
        { seq: 10, at: 1_800_000_000, kind: "declared", actor: "P/worker", detail: { effect: "file" } },
        { seq: 12, at: 1_800_000_001, kind: "approval", actor: "P/worker", detail: { state: "requested", reason: "<b>trust me</b>\nline two" } },
      ],
      ...overrides,
    });

  async function openAwaiting(body: Record<string, unknown>): Promise<HTMLElement> {
    stubRoutes((url) => ({ status: 200, body: url.includes("operation=") ? body : PAGE }));
    renderView();
    await userEvent.click(await screen.findByRole("button", { name: /op-open/u }));
    return screen.findByRole("region", { name: "Approval" });
  }

  afterEach(() => {
    Reflect.deleteProperty(navigator, "clipboard");
  });

  it("shows the kept intent field by field, both digests, the requester's words as text, and the commands", async () => {
    const region = await openAwaiting(awaiting());

    const fields = within(within(region).getByLabelText(/The intent, field by field/u)).getAllByRole("term");
    expect(fields.map((field) => field.textContent)).toEqual(["budget", "tool"]);
    expect(region.textContent).toContain('{"name":"render-report","version":"1.0.0"}');
    expect(region.textContent).toContain(`recorded digest ${recorded}`);
    expect(region.textContent).toContain(`recomputed digest ${recorded}`);
    expect(region.textContent).toContain("Equal: the document shown is the one the recorded digest names.");
    const words = region.querySelector(".op-words__text");
    expect(words?.textContent).toBe("<b>trust me</b>\nline two");
    expect(words?.querySelector("b")).toBeNull();
    const commands = [...region.querySelectorAll(".op-command__text")].map((node) => node.textContent ?? "");
    expect(commands).toHaveLength(2);
    for (const command of commands) {
      expect(command).toContain(`--subject sha256:${recorded}`);
      expect(command).not.toMatch(/token/iu);
    }
    expect(region.textContent).toContain("This cockpit does not approve.");
  });

  it("copies a command, and says so when the clipboard is unavailable", async () => {
    const writeText = vi.fn(async (_text: string) => undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    const region = await openAwaiting(awaiting());
    const buttons = within(region).getAllByRole("button", { name: /^Copy the command/u });
    await userEvent.click(buttons[0] as HTMLElement);
    expect(writeText).toHaveBeenCalledWith(`synapse approvals decide --name "<approver>" --subject sha256:${recorded} --approve`);
    expect(await within(region).findByText("Command copied")).toBeTruthy();
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: undefined });
    await userEvent.click(buttons[1] as HTMLElement);
    expect(await within(region).findByText("Clipboard unavailable; select the command to copy it.")).toBeTruthy();
  });

  it("refuses an intent whose recomputed digest differs, and offers no command", async () => {
    const region = await openAwaiting(awaiting({ recomputed_sha256: "d".repeat(64), digest_check: "differs" }));

    expect(within(region).getByRole("alert").textContent).toContain("must not be approved");
    expect(region.textContent).toContain(`recomputed digest ${"d".repeat(64)}`);
    expect(region.querySelector(".op-command")).toBeNull();
    expect(region.textContent).not.toContain("synapse approvals decide");
  });

  it("says there is nothing to approve without a kept intent, and has no view outside awaiting approval", async () => {
    const region = await openAwaiting(awaiting({ intent_sha256: "", recomputed_sha256: "", intent_fields: [], digest_check: "no intent" }));
    expect(within(region).getByRole("alert").textContent).toContain("nothing an approval could name");
    cleanup();
    stubRoutes((url) => ({ status: 200, body: url.includes("operation=") ? chain() : PAGE }));
    renderView();
    await userEvent.click(await screen.findByRole("button", { name: /op-verified/u }));
    await screen.findByRole("list", { name: "Recorded rows" });
    expect(screen.queryByRole("region", { name: "Approval" })).toBeNull();
  });

  it("names the absence of a request and of its words", async () => {
    const declaredOnly = [{ seq: 10, at: 1, kind: "declared", actor: "P/worker", detail: {} }];
    let region = await openAwaiting(awaiting({ chain: declaredOnly }));
    expect(region.querySelector(".op-words__text")?.textContent).toBe("No approval request is recorded.");
    cleanup();
    const silent = [...declaredOnly, { seq: 12, at: 1, kind: "approval", actor: "P/worker", detail: { state: "requested", reason: "" } }];
    region = await openAwaiting(awaiting({ chain: silent }));
    expect(region.querySelector(".op-words__text")?.textContent).toBe("The request gave no reason.");
  });
});
