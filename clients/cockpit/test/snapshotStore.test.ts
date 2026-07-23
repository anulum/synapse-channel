// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — snapshot polling and freshness lifecycle tests

import { describe, expect, it } from "vitest";

import { parseSnapshot } from "../src/lib/snapshotParser";
import {
  createSnapshotStore,
  withFreshness,
  type SnapshotState,
  type SnapshotStore,
} from "../src/lib/snapshotStore";

function jsonFetcher(body: unknown, status = 200): typeof fetch {
  return (async () => ({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  })) as unknown as typeof fetch;
}

function firstState(
  store: SnapshotStore,
  predicate: (state: SnapshotState) => boolean,
): Promise<SnapshotState> {
  return new Promise((resolve) => {
    const unsubscribe = store.subscribe((state) => {
      if (predicate(state)) {
        unsubscribe();
        resolve(state);
      }
    });
  });
}

describe("withFreshness", () => {
  const base: SnapshotState = {
    snapshot: parseSnapshot({}),
    status: "live",
    fetchedAt: 1000,
    error: null,
  };

  it("leaves states without complete freshness evidence untouched", () => {
    const noTime: SnapshotState = { ...base, fetchedAt: null, status: "connecting" };
    const noSnapshot: SnapshotState = { ...base, snapshot: null, status: "connecting" };
    expect(withFreshness(noTime, 99999)).toBe(noTime);
    expect(withFreshness(noSnapshot, 99999)).toBe(noSnapshot);
  });

  it("flips to stale past the threshold", () => {
    expect(withFreshness(base, 1000 + 7000, 6000).status).toBe("stale");
  });

  it("stays live within the threshold and preserves identity", () => {
    const fresh = withFreshness(base, 1000 + 100, 6000);
    expect(fresh.status).toBe("live");
    expect(fresh).toBe(base);
  });
});

describe("createSnapshotStore", () => {
  it("replays the connecting state to a new subscriber", () => {
    const store = createSnapshotStore({ fetcher: jsonFetcher({}), pollMs: 100000 });
    let seen: SnapshotState | undefined;
    const unsubscribe = store.subscribe((state) => {
      seen ??= state;
    });
    expect(seen?.status).toBe("connecting");
    unsubscribe();
    store.stop();
  });

  it("publishes a live snapshot on a successful poll", async () => {
    const store = createSnapshotStore({
      url: "/custom-snapshot.json",
      fetcher: jsonFetcher({ online_agents: ["a"], fleet: { agents: { live: ["a"] } } }),
      pollMs: 100000,
      now: () => 5000,
    });
    const live = await firstState(store, (state) => state.status === "live");
    expect(live.snapshot?.online_agents).toEqual(["a"]);
    expect(live.fetchedAt).toBe(5000);
    expect(live.error).toBeNull();
    store.stop();
  });

  it("reports an error when the hub returns a non-2xx status", async () => {
    const store = createSnapshotStore({ fetcher: jsonFetcher({}, 503), pollMs: 100000 });
    const failed = await firstState(store, (state) => state.status === "error");
    expect(failed.error).toContain("503");
    expect(failed.snapshot).toBeNull();
    store.stop();
  });

  it("reports an error when the payload is not an object", async () => {
    const store = createSnapshotStore({ fetcher: jsonFetcher("scalar"), pollMs: 100000 });
    const failed = await firstState(store, (state) => state.status === "error");
    expect(failed.error).toContain("not an object");
    store.stop();
  });

  it("reports an Error rejection message", async () => {
    const fetcher = (async () => {
      throw new Error("network down");
    }) as unknown as typeof fetch;
    const store = createSnapshotStore({ fetcher, pollMs: 100000 });
    const failed = await firstState(store, (state) => state.status === "error");
    expect(failed.error).toBe("network down");
    store.stop();
  });

  it("stringifies a non-Error rejection reason", async () => {
    const fetcher = (async () => {
      throw "kernel panic";
    }) as unknown as typeof fetch;
    const store = createSnapshotStore({ fetcher, pollMs: 100000 });
    const failed = await firstState(store, (state) => state.status === "error");
    expect(failed.error).toBe("kernel panic");
    store.stop();
  });

  it("keeps the last good snapshot but reports stale after the threshold", async () => {
    let clock = 1000;
    let call = 0;
    const fetcher = (async () => {
      call += 1;
      if (call === 1) {
        return { ok: true, status: 200, json: async () => ({ online_agents: ["a"] }) };
      }
      clock = 8000;
      throw new Error("dropped");
    }) as unknown as typeof fetch;
    const store = createSnapshotStore({ fetcher, pollMs: 5, staleAfterMs: 6000, now: () => clock });
    await firstState(store, (state) => state.status === "live");
    const stale = await firstState(store, (state) => state.error === "dropped");
    expect(stale.status).toBe("stale");
    expect(stale.snapshot?.online_agents).toEqual(["a"]);
    store.stop();
  });

  it("keeps the last good snapshot live when a later poll fails while fresh", async () => {
    let call = 0;
    const fetcher = (async () => {
      call += 1;
      if (call === 1) {
        return { ok: true, status: 200, json: async () => ({ online_agents: ["a"] }) };
      }
      throw new Error("blip");
    }) as unknown as typeof fetch;
    const store = createSnapshotStore({ fetcher, pollMs: 5, staleAfterMs: 6000, now: () => 2000 });
    await firstState(store, (state) => state.status === "live");
    const held = await firstState(store, (state) => state.error === "blip");
    expect(held.status).toBe("live");
    expect(held.snapshot?.online_agents).toEqual(["a"]);
    store.stop();
  });

  it("falls back to the authenticated global fetch and default options", () => {
    const store = createSnapshotStore();
    expect(() => store.stop()).not.toThrow();
  });

  it("ignores a rejected poll after stop", async () => {
    let rejectPending: ((reason: Error) => void) | undefined;
    const fetcher = (() =>
      new Promise((_resolve, reject) => {
        rejectPending = reject;
      })) as unknown as typeof fetch;
    const store = createSnapshotStore({ fetcher, pollMs: 100000, now: () => 1 });
    let errored = false;
    store.subscribe((state) => {
      if (state.error !== null) errored = true;
    });
    store.stop();
    rejectPending?.(new Error("late"));
    await new Promise((resolve) => setTimeout(resolve, 5));
    expect(errored).toBe(false);
  });

  it("ignores a successful poll after stop", async () => {
    let resolvePending: ((response: Response) => void) | undefined;
    const fetcher = (() =>
      new Promise<Response>((resolve) => {
        resolvePending = resolve;
      })) as unknown as typeof fetch;
    const store = createSnapshotStore({ fetcher, pollMs: 100000, now: () => 1 });
    let live = false;
    store.subscribe((state) => {
      if (state.status === "live") live = true;
    });
    store.stop();
    resolvePending?.({ ok: true, status: 200, json: async () => ({}) } as Response);
    await new Promise((resolve) => setTimeout(resolve, 5));
    expect(live).toBe(false);
  });

  it("stops polling and clears listeners on stop", async () => {
    const store = createSnapshotStore({ fetcher: jsonFetcher({}), pollMs: 5, now: () => 1 });
    await firstState(store, (state) => state.status === "live");
    store.stop();
    let notified = false;
    store.subscribe(() => {
      notified = true;
    });
    expect(notified).toBe(true);
    notified = false;
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(notified).toBe(false);
  });
});
