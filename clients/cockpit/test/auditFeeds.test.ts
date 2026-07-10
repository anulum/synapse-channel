// @vitest-environment jsdom
// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — durable audit feed parsing and bounded polling tests

import { afterEach, describe, expect, it, vi } from "vitest";

import {
  createOperatorActionsStore,
  createReceiptsStore,
  parseOperatorActionsPage,
  parseReceiptsPage,
  type OperatorActionsState,
  type ReceiptsState,
} from "../src/lib/auditFeeds";
import {
  cockpitAuthSnapshot,
  resetCockpitAuth,
  unlockCockpit,
} from "../src/lib/auth";

function receipt(seq: number, summary = `receipt ${seq}`): Record<string, unknown> {
  return {
    seq,
    ts: seq * 10,
    receipt_id: `delivery:${seq}`,
    kind: "delivery",
    subject: `target-${seq}`,
    actor: "operator/test",
    status: "delivered",
    summary,
    source_event_kind: "delivery_receipt_immediate",
    payload: {},
  };
}

function action(seq: number): Record<string, unknown> {
  return {
    seq,
    ts: seq * 10,
    action: "release",
    direction: "out",
    status: "applied",
    applied: true,
    pending: false,
    namespace: "TEAM",
    task_id: `T-${seq}`,
    operator: "operator/test",
    agent: "",
    requester: "",
    approver: "",
    reason: "",
    detail: "released",
  };
}

function page(key: "receipts" | "actions", rows: unknown[], nextCursor: number): Response {
  return new Response(
    JSON.stringify({ present: true, [key]: rows, next_cursor: nextCursor, log_end_seq: nextCursor }),
  );
}

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  sessionStorage.clear();
  resetCockpitAuth();
});

describe("audit page parsers", () => {
  it("parses strict receipt and operator-action rows plus empty present pages", () => {
    expect(parseReceiptsPage({ present: true, receipts: [receipt(7)], next_cursor: 7 })).toEqual({
      rows: [
        {
          seq: 7,
          ts: 70,
          receiptId: "delivery:7",
          kind: "delivery",
          subject: "target-7",
          actor: "operator/test",
          status: "delivered",
          summary: "receipt 7",
          sourceEventKind: "delivery_receipt_immediate",
        },
      ],
      nextCursor: 7,
    });
    expect(parseOperatorActionsPage({ present: true, actions: [action(8)], next_cursor: 8 })).toEqual({
      rows: [
        {
          seq: 8,
          ts: 80,
          action: "release",
          direction: "out",
          status: "applied",
          applied: true,
          pending: false,
          namespace: "TEAM",
          taskId: "T-8",
          operator: "operator/test",
          agent: "",
          requester: "",
          approver: "",
          reason: "",
          detail: "released",
        },
      ],
      nextCursor: 8,
    });
    expect(parseReceiptsPage({ present: true, receipts: [], next_cursor: 0 })).toEqual({
      rows: [],
      nextCursor: 0,
    });
    expect(parseOperatorActionsPage({ present: true, actions: [], next_cursor: 0 })).toEqual({
      rows: [],
      nextCursor: 0,
    });
  });

  it("rejects malformed documents and every malformed receipt field", () => {
    expect(parseReceiptsPage(null)).toBeNull();
    expect(parseReceiptsPage([])).toBeNull();
    expect(parseReceiptsPage({ present: false, receipts: [], next_cursor: 0 })).toBeNull();
    expect(parseReceiptsPage({ present: true, receipts: "bad", next_cursor: 0 })).toBeNull();
    expect(parseReceiptsPage({ present: true, receipts: [], next_cursor: -1 })).toBeNull();
    expect(parseReceiptsPage({ present: true, receipts: ["bad"], next_cursor: 1 })).toBeNull();

    const textFields = [
      "receipt_id",
      "kind",
      "subject",
      "actor",
      "status",
      "summary",
      "source_event_kind",
    ];
    for (const field of textFields) {
      expect(
        parseReceiptsPage({
          present: true,
          receipts: [{ ...receipt(1), [field]: 7 }],
          next_cursor: 1,
        }),
        field,
      ).toBeNull();
    }
    for (const bad of ["1", -1, 1.5, Number.MAX_SAFE_INTEGER + 1]) {
      expect(
        parseReceiptsPage({ present: true, receipts: [{ ...receipt(1), seq: bad }], next_cursor: 1 }),
      ).toBeNull();
    }
    for (const bad of ["1", -1, Number.POSITIVE_INFINITY]) {
      expect(
        parseReceiptsPage({ present: true, receipts: [{ ...receipt(1), ts: bad }], next_cursor: 1 }),
      ).toBeNull();
    }
  });

  it("rejects every malformed operator-action field", () => {
    expect(
      parseOperatorActionsPage({ present: true, actions: ["bad"], next_cursor: 1 }),
    ).toBeNull();
    const textFields = [
      "action",
      "direction",
      "status",
      "namespace",
      "task_id",
      "operator",
      "agent",
      "requester",
      "approver",
      "reason",
      "detail",
    ];
    for (const field of textFields) {
      expect(
        parseOperatorActionsPage({
          present: true,
          actions: [{ ...action(1), [field]: null }],
          next_cursor: 1,
        }),
        field,
      ).toBeNull();
    }
    for (const field of ["applied", "pending"]) {
      expect(
        parseOperatorActionsPage({
          present: true,
          actions: [{ ...action(1), [field]: "yes" }],
          next_cursor: 1,
        }),
        field,
      ).toBeNull();
    }
    expect(parseOperatorActionsPage({ present: true, actions: [{ ...action(1), seq: -1 }], next_cursor: 1 })).toBeNull();
    expect(parseOperatorActionsPage({ present: true, actions: [{ ...action(1), ts: NaN }], next_cursor: 1 })).toBeNull();
  });
});

describe("bounded audit stores", () => {
  it("advances by server sequence, de-duplicates rows, and caps retained history", async () => {
    vi.useFakeTimers();
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(page("receipts", [receipt(1), receipt(2)], 2))
      .mockResolvedValueOnce(page("receipts", [receipt(2, "updated"), receipt(3)], 1));
    const states: ReceiptsState[] = [];
    const store = createReceiptsStore({
      fetcher,
      pollMs: 1_000,
      pageLimit: 2,
      retainedLimit: 2,
      now: () => 5_000,
    });
    const unsubscribe = store.subscribe((state) => states.push(state));
    await vi.waitFor(() => expect(states.at(-1)?.status).toBe("live"));
    expect(fetcher.mock.calls[0]?.[0]).toBe("/receipts.json?since=0&limit=2");
    await vi.advanceTimersByTimeAsync(1_000);
    await vi.waitFor(() => expect(states.at(-1)?.data?.map((row) => row.seq)).toEqual([3, 2]));
    expect(states.at(-1)?.data?.[1]?.summary).toBe("updated");
    expect(states.at(-1)?.fetchedAt).toBe(5_000);
    expect(fetcher.mock.calls[1]?.[0]).toBe("/receipts.json?since=2&limit=2");
    unsubscribe();
    store.stop();
  });

  it("distinguishes 404, resets on reappearance, and accepts an empty present feed", async () => {
    vi.useFakeTimers();
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(page("receipts", [receipt(5)], 5))
      .mockResolvedValueOnce(new Response("not configured", { status: 404 }))
      .mockResolvedValueOnce(page("receipts", [], 0));
    const states: ReceiptsState[] = [];
    const store = createReceiptsStore({ fetcher, pollMs: 1_000 });
    store.subscribe((state) => states.push(state));
    await vi.waitFor(() => expect(states.at(-1)?.status).toBe("live"));
    await vi.advanceTimersByTimeAsync(1_000);
    await vi.waitFor(() => expect(states.at(-1)?.status).toBe("absent"));
    expect(states.at(-1)?.data?.map((row) => row.seq)).toEqual([5]);
    await vi.advanceTimersByTimeAsync(1_000);
    await vi.waitFor(() => expect(states.at(-1)?.status).toBe("live"));
    expect(states.at(-1)?.data).toEqual([]);
    expect(fetcher.mock.calls[2]?.[0]).toBe("/receipts.json?since=0&limit=50");
    store.stop();
  });

  it("retains last-good rows across 503 and malformed responses", async () => {
    vi.useFakeTimers();
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(page("actions", [action(4)], 4))
      .mockResolvedValueOnce(new Response("down", { status: 503 }))
      .mockResolvedValueOnce(page("actions", [{ ...action(5), applied: "yes" }], 5));
    const states: OperatorActionsState[] = [];
    const store = createOperatorActionsStore({ fetcher, pollMs: 1_000, now: () => 6_000 });
    store.subscribe((state) => states.push(state));
    await vi.waitFor(() => expect(states.at(-1)?.status).toBe("live"));
    expect(states.at(-1)?.fetchedAt).toBe(6_000);
    await vi.advanceTimersByTimeAsync(1_000);
    await vi.waitFor(() => expect(states.at(-1)?.error).toContain("503"));
    expect(states.at(-1)?.data?.[0]?.seq).toBe(4);
    await vi.advanceTimersByTimeAsync(1_000);
    await vi.waitFor(() => expect(states.at(-1)?.error).toContain("not parseable"));
    expect(states.at(-1)?.data?.[0]?.seq).toBe(4);
    store.stop();
  });

  it("locks cockpit auth on 401 and surfaces non-Error transport failures", async () => {
    vi.useFakeTimers();
    expect(unlockCockpit("audit-token")).toBe(true);
    const globalFetch = vi.fn<typeof fetch>().mockResolvedValue(new Response("revoked", { status: 401 }));
    vi.stubGlobal("fetch", globalFetch);
    const receiptStates: ReceiptsState[] = [];
    const receipts = createReceiptsStore({ pollMs: 1_000 });
    receipts.subscribe((state) => receiptStates.push(state));
    await vi.waitFor(() => expect(cockpitAuthSnapshot().phase).toBe("locked"));
    expect(receiptStates.at(-1)?.error).toContain("401");
    expect(new Headers(globalFetch.mock.calls[0]?.[1]?.headers).get("Authorization")).toBe(
      "Bearer audit-token",
    );
    receipts.stop();

    const actionStates: OperatorActionsState[] = [];
    const actions = createOperatorActionsStore({
      fetcher: vi.fn<typeof fetch>().mockRejectedValue("transport offline"),
      pollMs: 1_000,
      pageLimit: 0,
      retainedLimit: 0,
    });
    actions.subscribe((state) => actionStates.push(state));
    await vi.waitFor(() => expect(actionStates.at(-1)?.error).toBe("transport offline"));
    actions.stop();
  });

  it("drops a late response after stop", async () => {
    vi.useFakeTimers();
    let resolveFetch: ((response: Response) => void) | undefined;
    const fetcher = vi.fn<typeof fetch>().mockImplementation(
      () =>
        new Promise<Response>((resolve) => {
          resolveFetch = resolve;
        }),
    );
    const states: ReceiptsState[] = [];
    const store = createReceiptsStore({ fetcher, pollMs: 1_000 });
    store.subscribe((state) => states.push(state));
    const before = states.length;
    store.stop();
    resolveFetch?.(page("receipts", [receipt(1)], 1));
    await vi.advanceTimersByTimeAsync(10);
    expect(states).toHaveLength(before);

    let rejectFetch: ((reason: Error) => void) | undefined;
    const rejecting = vi.fn<typeof fetch>().mockImplementation(
      () =>
        new Promise<Response>((_resolve, reject) => {
          rejectFetch = reject;
        }),
    );
    const actionStates: OperatorActionsState[] = [];
    const actions = createOperatorActionsStore({ fetcher: rejecting, pollMs: 1_000 });
    actions.subscribe((state) => actionStates.push(state));
    const actionCount = actionStates.length;
    actions.stop();
    rejectFetch?.(new Error("late transport failure"));
    await vi.advanceTimersByTimeAsync(10);
    expect(actionStates).toHaveLength(actionCount);
  });
});
