// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — the operations feed on the wire: documents, URLs, the list feed, one chain, one proof

import { afterEach, describe, expect, it, vi } from "vitest";

import type { FeedState } from "../src/lib/feed";
import { fetchAndVerify } from "../src/lib/merkleVerify";
import type { OperationRow, OperationsPage } from "../src/lib/operations";
import {
  OPERATIONS_POLL_MS,
  OPERATIONS_SOURCE,
  createOperationsFeed,
  detailText,
  fetchOperationChain,
  operationChainUrl,
  operationsUrl,
  parseOperationChain,
  parseOperationsPage,
} from "../src/lib/operationsFeed";
import { CHAIN } from "./operationsFixture";

vi.mock("../src/lib/merkleVerify", () => ({ fetchAndVerify: vi.fn(async () => ({ kind: "unserved" })) }));

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

function response(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function row(operationId: string, state: string, declaredSeq: number): OperationRow {
  return {
    operationId,
    taskId: "T1",
    declarer: "P/worker",
    declaredSeq,
    state,
    latestSeq: declaredSeq + 1,
    latestKind: "answer",
    latestAt: 1_800_000_000,
  };
}

describe("parseOperationsPage", () => {
  it("reads the served page and keeps its cursor, total, clock, custody and scope", () => {
    const page = parseOperationsPage({
      operations: [
        { operation_id: "op-a", task_id: "T1", declarer: "P/worker", declared_seq: 9, state: "declared", latest_seq: 9, latest_kind: "declared", latest_at: 1_800_000_000 },
      ],
      total: 12,
      next_before: 9,
      limit: 1,
      as_of_seq: 40,
      custody: "not recorded here",
      scope: { scope: "project:P", applied: true, rows_in_page: 1, rows_in_scope: 1 },
    });
    expect(page).toEqual({
      operations: [{ ...row("op-a", "declared", 9), latestSeq: 9, latestKind: "declared" }],
      unreadable: 0,
      total: 12,
      nextBefore: 9,
      asOfSeq: 40,
      custody: "not recorded here",
      scope: { scope: "project:P", applied: true, reason: "" },
    });
  });

  it("counts the rows it cannot read and says not recorded for each missing field", () => {
    const page = parseOperationsPage({
      operations: [
        { operation_id: "op-b", declared_seq: 3, latest_at: Number.POSITIVE_INFINITY },
        { operation_id: "", declared_seq: 4 },
        { operation_id: "op-c", declared_seq: -1 },
        { operation_id: "op-d", declared_seq: 1.5 },
        { operation_id: "op-e" },
        "op-f",
        null,
        [1],
      ],
      next_before: null,
    });
    expect(page?.unreadable).toBe(7);
    expect(page?.operations).toEqual([
      { operationId: "op-b", taskId: "", declarer: "", declaredSeq: 3, state: "", latestSeq: null, latestKind: "", latestAt: null },
    ]);
    expect(page).toMatchObject({ total: null, nextBefore: null, asOfSeq: null, custody: "", scope: null });
  });

  it("refuses a payload that is not a list document", () => {
    for (const raw of [null, "x", 3, [], { operations: {} }, { chain: [] }]) expect(parseOperationsPage(raw)).toBeNull();
  });
});

describe("parseOperationChain", () => {
  it("reads every link with its fields as text, in the order the report gives them", () => {
    const chain = parseOperationChain({ ...CHAIN, scope: { scope: "project:Q", applied: false, reason: "one record is named" } });
    expect(chain?.chain.map((link) => [link.seq, link.kind, link.actor])).toEqual([
      [4, "declared", "P/worker"],
      [6, "approval", "P/approver"],
      [7, "answer", "P/worker"],
      [8, "outcome", "P/worker"],
      [9, "verification", "P/checker"],
    ]);
    expect(chain?.chain[3]?.detail).toEqual([["state", "confirmed_done"], ["external_effect", "true"], ["boundary", "{\"at\":2}"]]);
    expect(chain?.chain[4]?.detail).toEqual([["result", "verified"], ["effect", "null"]]);
    expect(chain?.chain[1]?.detail).toEqual([["state", "approved"], ["reason", "\"\""]]);
    expect(chain).toMatchObject({
      present: true,
      operationId: "op-verified",
      state: "verified",
      digestCheck: "matches",
      attempts: [{ attemptId: "att-1", executor: "P/worker", state: "verified" }],
      unreadable: 0,
      asOfSeq: 40,
      custody: "not recorded here",
      scope: { scope: "project:Q", applied: false, reason: "one record is named" },
    });
  });

  it("reads the recomputed digest and the kept intent's fields, and drops a field it cannot read", () => {
    const chain = parseOperationChain({
      ...CHAIN,
      recomputed_sha256: "b".repeat(64),
      intent_fields: [
        { field: "budget", canonical: '"not_applicable"' },
        { field: "", canonical: "1" },
        { field: "deadline" },
        "tool",
        { field: "egress", canonical: "[]" },
      ],
    });
    expect(chain?.recomputedSha256).toBe("b".repeat(64));
    expect(chain?.intentFields).toEqual([
      { field: "budget", canonical: '"not_applicable"' },
      { field: "egress", canonical: "[]" },
    ]);
    expect(parseOperationChain(CHAIN)).toMatchObject({ recomputedSha256: "", intentFields: [] });
  });

  it("drops what it cannot read and counts the links", () => {
    const chain = parseOperationChain({
      present: true,
      chain: [{ seq: "4" }, { seq: 5, detail: "x" }, null],
      attempts: [{ attempt_id: "" }, { attempt_id: "att-2" }, 7],
    });
    expect(chain?.chain).toEqual([{ seq: 5, at: null, kind: "", actor: "", detail: [] }]);
    expect(chain?.unreadable).toBe(2);
    expect(chain?.attempts).toEqual([{ attemptId: "att-2", executor: "", state: "" }]);
    expect(chain).toMatchObject({ operationId: "", asOfSeq: null, custody: "", scope: null });
  });

  it("reads an unknown id as present false, and refuses what is not a chain", () => {
    const absent = parseOperationChain({ operation_id: "op-never", state: "not recorded", chain: [], present: false });
    expect(absent).toMatchObject({ present: false, operationId: "op-never", state: "not recorded", chain: [] });
    for (const raw of [null, [], { chain: [] }, { present: "yes", chain: [] }, { present: true, chain: {} }]) {
      expect(parseOperationChain(raw)).toBeNull();
    }
  });

  it("renders a detail value that is not text as its JSON", () => {
    expect([detailText("x"), detailText(""), detailText(2), detailText(false), detailText([1]), detailText(undefined)]).toEqual([
      "x", "\"\"", "2", "false", "[1]", "null",
    ]);
  });
});

describe("urls", () => {
  it("asks for a page before a cursor, and for the scope", () => {
    expect(operationsUrl({ kind: "all" })).toBe("/operations.json");
    expect(operationsUrl({ kind: "all" }, 9)).toBe("/operations.json?before=9");
    expect(operationsUrl({ kind: "project", namespace: "P" }, 9)).toBe("/operations.json?before=9&scope=project%3AP");
    expect(operationsUrl({ kind: "none" })).toBe("/operations.json?scope=none");
  });

  it("encodes one chain's id and asks for the scope so the feed can say it shows the chain whole", () => {
    expect(operationChainUrl("op a&b", { kind: "all" })).toBe("/operations.json?operation=op%20a%26b");
    expect(operationChainUrl("op-1", { kind: "group", name: "ships" })).toBe("/operations.json?operation=op-1&scope=group%3Aships");
  });
});

describe("createOperationsFeed", () => {
  it("polls one page and publishes it live", async () => {
    vi.useFakeTimers();
    const fetcher = vi.fn(async (_url: RequestInfo | URL) => response(200, { operations: [], total: 0, next_before: null, as_of_seq: 3 }));
    const feed = createOperationsFeed({ kind: "project", namespace: "P" }, 5, fetcher as unknown as typeof fetch);
    const states: FeedState<OperationsPage>[] = [];
    feed.subscribe((state) => states.push(state));
    await vi.waitFor(() => expect(states.at(-1)?.status).toBe("live"));
    expect(fetcher.mock.calls[0]?.[0]).toBe("/operations.json?before=5&scope=project%3AP");
    expect(states.at(-1)?.data).toMatchObject({ total: 0, asOfSeq: 3 });
    await vi.advanceTimersByTimeAsync(OPERATIONS_POLL_MS);
    expect(fetcher).toHaveBeenCalledTimes(2);
    feed.stop();
  });

  it("uses the cockpit's authenticated fetch when none is given", async () => {
    const fetcher = vi.fn(async (_url: RequestInfo | URL) => response(404, {}));
    vi.stubGlobal("fetch", fetcher);
    const feed = createOperationsFeed({ kind: "all" });
    const states: FeedState<OperationsPage>[] = [];
    feed.subscribe((state) => states.push(state));
    await vi.waitFor(() => expect(states.at(-1)?.status).toBe("absent"));
    expect(String(fetcher.mock.calls[0]?.[0])).toContain("/operations.json");
    feed.stop();
  });
});

describe("fetchOperationChain", () => {
  it("reads one chain, a missing feed, a failure and a payload that is not a chain", async () => {
    const answer = (status: number, body: unknown) => vi.fn(async () => response(status, body)) as unknown as typeof fetch;
    const loaded = await fetchOperationChain("op-verified", { kind: "all" }, answer(200, CHAIN));
    expect(loaded.kind === "loaded" && loaded.chain.chain.length).toBe(5);
    expect(await fetchOperationChain("op-1", { kind: "all" }, answer(404, {}))).toEqual({ kind: "absent" });
    expect(await fetchOperationChain("op-1", { kind: "all" }, answer(500, {}))).toEqual({ kind: "error", message: "hub returned 500" });
    expect(await fetchOperationChain("op-1", { kind: "all" }, answer(200, { present: 1 }))).toEqual({
      kind: "error",
      message: "the chain payload was not a chain document",
    });
  });

  it("says why a read threw", async () => {
    const thrown = vi.fn(async () => { throw new Error("offline"); }) as unknown as typeof fetch;
    const odd = vi.fn(async () => { throw "odd"; }) as unknown as typeof fetch;
    expect(await fetchOperationChain("op-1", { kind: "all" }, thrown)).toEqual({ kind: "error", message: "offline" });
    expect(await fetchOperationChain("op-1", { kind: "all" }, odd)).toEqual({ kind: "error", message: "odd" });
  });

  it("asks with the cockpit's authenticated fetch by default", async () => {
    const fetcher = vi.fn(async () => response(200, CHAIN));
    vi.stubGlobal("fetch", fetcher);
    const result = await fetchOperationChain("op-verified", { kind: "all" });
    expect(result.kind).toBe("loaded");
  });
});

describe("OPERATIONS_SOURCE", () => {
  it("hands the tab the list feed, the chain read and the proof check", async () => {
    const fetcher = vi.fn(async (_url: RequestInfo | URL) => response(200, CHAIN));
    vi.stubGlobal("fetch", fetcher);
    const feed = OPERATIONS_SOURCE.openList({ kind: "project", namespace: "P" }, 7);
    feed.stop();
    const chain = await OPERATIONS_SOURCE.readChain("op-verified", { kind: "all" });
    expect(chain.kind).toBe("loaded");
    expect(fetcher.mock.calls.map((call) => String(call[0]))).toEqual(
      expect.arrayContaining(["/operations.json?before=7&scope=project%3AP", "/operations.json?operation=op-verified"]),
    );
    expect(await OPERATIONS_SOURCE.verifyRow(4)).toEqual({ kind: "unserved" });
    expect(vi.mocked(fetchAndVerify)).toHaveBeenCalledWith(4);
  });
});
