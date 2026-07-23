// @vitest-environment jsdom
// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — retained durable audit cursor lifecycle tests

import { afterEach, describe, expect, it, vi } from "vitest";

import {
  createOperatorActionsStore,
  createReceiptsStore,
  type OperatorActionsState,
  type ReceiptsState,
} from "../src/lib/auditFeedStore";
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

describe("bounded audit stores", () => {
  it("uses each public endpoint and cadence default", async () => {
    vi.useFakeTimers();
    const receiptFetch = vi
      .fn<typeof fetch>()
      .mockResolvedValue(page("receipts", [], 0));
    const receiptStates: ReceiptsState[] = [];
    const receipts = createReceiptsStore({ fetcher: receiptFetch });
    receipts.subscribe((state) => receiptStates.push(state));
    await vi.waitFor(() => expect(receiptStates.at(-1)?.status).toBe("live"));
    expect(receiptFetch.mock.calls[0]?.[0]).toBe("/receipts.json?since=0&limit=50");
    receipts.stop();

    const globalFetch = vi.fn<typeof fetch>().mockResolvedValue(page("actions", [], 0));
    vi.stubGlobal("fetch", globalFetch);
    const actionStates: OperatorActionsState[] = [];
    const actions = createOperatorActionsStore();
    actions.subscribe((state) => actionStates.push(state));
    await vi.waitFor(() => expect(actionStates.at(-1)?.status).toBe("live"));
    expect(globalFetch.mock.calls[0]?.[0]).toBe("/operator-actions.json?since=0&limit=50");
    actions.stop();
  });

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

    let resolveAbsent: ((response: Response) => void) | undefined;
    const absentFetch = vi.fn<typeof fetch>().mockImplementation(
      () =>
        new Promise<Response>((resolve) => {
          resolveAbsent = resolve;
        }),
    );
    const absentStates: ReceiptsState[] = [];
    const absent = createReceiptsStore({ fetcher: absentFetch, pollMs: 1_000 });
    absent.subscribe((state) => absentStates.push(state));
    const absentCount = absentStates.length;
    absent.stop();
    resolveAbsent?.(new Response("not configured", { status: 404 }));
    await vi.advanceTimersByTimeAsync(10);
    expect(absentStates).toHaveLength(absentCount);

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
