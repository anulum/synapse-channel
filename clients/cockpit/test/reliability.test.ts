// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — reliability-evidence parsing and polling tests

import { afterEach, describe, expect, it, vi } from "vitest";
import {
  createReliabilityStore,
  orderOwners,
  parseReliability,
  type ReliabilityState,
} from "../src/lib/reliability";

afterEach(() => {
  vi.useRealTimers();
});

describe("parseReliability", () => {
  it("parses the CLI report shape verbatim", () => {
    const report = parseReliability({
      as_of: 1783037052.5,
      generated_from_seq: 5202,
      note: "audit signals, not scores",
      owners: [
        {
          owner: "alpha/aster",
          stale_claims: 4,
          conflict_pairs: 14,
          declared_failed_checks: 0,
          broken_handoffs: 1,
        },
      ],
      findings: [
        {
          kind: "stale_claim",
          owner: "alpha/aster",
          task_id: "t1",
          seq: 1333,
          ts: 1782255984.0,
          detail: "lease expired",
          evidence: { lease_expires_at: 1782259584.0 },
        },
      ],
    });
    expect(report).toEqual({
      asOf: 1783037052.5,
      generatedFromSeq: 5202,
      note: "audit signals, not scores",
      owners: [
        {
          owner: "alpha/aster",
          staleClaims: 4,
          conflictPairs: 14,
          declaredFailedChecks: 0,
          brokenHandoffs: 1,
        },
      ],
      findings: [
        {
          kind: "stale_claim",
          owner: "alpha/aster",
          taskId: "t1",
          seq: 1333,
          ts: 1782255984.0,
          detail: "lease expired",
          evidence: { lease_expires_at: 1782259584.0 },
        },
      ],
    });
  });

  it("rejects non-objects and defaults every missing field safely", () => {
    expect(parseReliability(null)).toBeNull();
    expect(parseReliability([1])).toBeNull();
    const empty = parseReliability({ owners: "junk", findings: [{}] });
    expect(empty?.asOf).toBeNull();
    expect(empty?.note).toBe("");
    expect(empty?.owners).toEqual([]);
    expect(empty?.findings).toEqual([
      { kind: "", owner: "", taskId: "", seq: 0, ts: null, detail: "", evidence: {} },
    ]);
    expect(parseReliability({ findings: "junk" })?.findings).toEqual([]);
  });
});

describe("orderOwners", () => {
  it("orders by recorded volume, then name, without mutating the input", () => {
    const owners = [
      { owner: "quiet", staleClaims: 0, conflictPairs: 0, declaredFailedChecks: 0, brokenHandoffs: 0 },
      { owner: "b-loud", staleClaims: 2, conflictPairs: 3, declaredFailedChecks: 0, brokenHandoffs: 0 },
      { owner: "a-loud", staleClaims: 5, conflictPairs: 0, declaredFailedChecks: 0, brokenHandoffs: 0 },
    ];
    const before = [...owners];
    expect(orderOwners(owners).map((owner) => owner.owner)).toEqual(["a-loud", "b-loud", "quiet"]);
    expect(owners).toEqual(before);
  });
});

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), { status });
}

describe("createReliabilityStore", () => {
  it("reports 404 as absent and keeps re-checking on the poll cadence", async () => {
    vi.useFakeTimers();
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(new Response("not found", { status: 404 }))
      .mockResolvedValueOnce(jsonResponse({ note: "audit signals, not scores", findings: [], owners: [] }));
    const states: ReliabilityState[] = [];
    const store = createReliabilityStore({ fetcher, pollMs: 1000, now: () => 5_000 });
    store.subscribe((state) => states.push(state));

    await vi.waitFor(() => {
      expect(states.at(-1)?.status).toBe("absent");
    });
    await vi.advanceTimersByTimeAsync(1000);
    await vi.waitFor(() => {
      expect(states.at(-1)?.status).toBe("live");
    });
    expect(states.at(-1)?.data?.note).toBe("audit signals, not scores");
    expect(states.at(-1)?.fetchedAt).toBe(5_000);
    store.stop();
  });

  it("keeps the last good report across a failure and reports the reason", async () => {
    vi.useFakeTimers();
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse({ note: "n", findings: [], owners: [] }))
      .mockResolvedValueOnce(new Response("boom", { status: 500 }))
      .mockResolvedValueOnce(jsonResponse([1, 2, 3]));
    const states: ReliabilityState[] = [];
    const store = createReliabilityStore({ fetcher, pollMs: 1000 });
    store.subscribe((state) => states.push(state));

    await vi.waitFor(() => {
      expect(states.at(-1)?.status).toBe("live");
    });
    await vi.advanceTimersByTimeAsync(1000);
    await vi.waitFor(() => {
      expect(states.at(-1)?.status).toBe("error");
    });
    expect(states.at(-1)?.error).toContain("500");
    expect(states.at(-1)?.data?.note).toBe("n");
    await vi.advanceTimersByTimeAsync(1000);
    await vi.waitFor(() => {
      expect(states.at(-1)?.error).toContain("not parseable");
    });
    store.stop();
  });

  it("stringifies a non-Error failure reason", async () => {
    vi.useFakeTimers();
    const fetcher = vi.fn<typeof fetch>().mockRejectedValue("plain string reason");
    const states: ReliabilityState[] = [];
    const store = createReliabilityStore({ fetcher, pollMs: 1000 });
    store.subscribe((state) => states.push(state));
    await vi.waitFor(() => {
      expect(states.at(-1)?.status).toBe("error");
    });
    expect(states.at(-1)?.error).toBe("plain string reason");
    store.stop();
  });

  it("runs on its defaults: a relative URL through the global fetch fails visibly", async () => {
    vi.useFakeTimers();
    // Node's global fetch cannot resolve the browser-relative default URL, so
    // the default-constructed store must surface an error state, not hang.
    const states: ReliabilityState[] = [];
    const store = createReliabilityStore();
    store.subscribe((state) => states.push(state));
    await vi.waitFor(() => {
      expect(states.at(-1)?.status).toBe("error");
    });
    expect(states.at(-1)?.data).toBeNull();
    store.stop();
  });

  it("stops polling and delivering after stop", async () => {
    vi.useFakeTimers();
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValue(jsonResponse({ note: "n", findings: [], owners: [] }));
    const states: ReliabilityState[] = [];
    const store = createReliabilityStore({ fetcher, pollMs: 1000 });
    store.subscribe((state) => states.push(state));
    await vi.waitFor(() => {
      expect(states.at(-1)?.status).toBe("live");
    });
    const calls = fetcher.mock.calls.length;
    store.stop();
    await vi.advanceTimersByTimeAsync(5000);
    expect(fetcher.mock.calls.length).toBe(calls);
  });

  it("drops a late response that lands after stop", async () => {
    vi.useFakeTimers();
    let resolveFetch: ((response: Response) => void) | undefined;
    const fetcher = vi.fn<typeof fetch>().mockImplementation(
      () =>
        new Promise<Response>((resolve) => {
          resolveFetch = resolve;
        }),
    );
    const states: ReliabilityState[] = [];
    const store = createReliabilityStore({ fetcher, pollMs: 1000 });
    store.subscribe((state) => states.push(state));
    const before = states.length;
    store.stop();
    resolveFetch?.(jsonResponse({ note: "late", findings: [], owners: [] }));
    await vi.advanceTimersByTimeAsync(10);
    expect(states.length).toBe(before);
  });

  it("drops a late 404 and a late failure that land after stop", async () => {
    vi.useFakeTimers();
    let resolve404: ((response: Response) => void) | undefined;
    const fetcher404 = vi.fn<typeof fetch>().mockImplementation(
      () =>
        new Promise<Response>((resolve) => {
          resolve404 = resolve;
        }),
    );
    const seen404: ReliabilityState[] = [];
    const store404 = createReliabilityStore({ fetcher: fetcher404, pollMs: 1000 });
    store404.subscribe((state) => seen404.push(state));
    const before404 = seen404.length;
    store404.stop();
    resolve404?.(new Response("gone", { status: 404 }));
    await vi.advanceTimersByTimeAsync(10);
    expect(seen404.length).toBe(before404);

    let rejectFetch: ((reason: Error) => void) | undefined;
    const fetcherFail = vi.fn<typeof fetch>().mockImplementation(
      () =>
        new Promise<Response>((_resolve, reject) => {
          rejectFetch = reject;
        }),
    );
    const seenFail: ReliabilityState[] = [];
    const storeFail = createReliabilityStore({ fetcher: fetcherFail, pollMs: 1000 });
    storeFail.subscribe((state) => seenFail.push(state));
    const beforeFail = seenFail.length;
    storeFail.stop();
    rejectFetch?.(new Error("connection torn down"));
    await vi.advanceTimersByTimeAsync(10);
    expect(seenFail.length).toBe(beforeFail);
  });
});
