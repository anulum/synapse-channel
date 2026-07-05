// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — snapshot parsing, freshness, and polling tests

import { describe, expect, it } from "vitest";
import {
  createSnapshotStore,
  parseSnapshot,
  withFreshness,
  type SnapshotState,
  type SnapshotStore,
} from "../src/lib/snapshot";

/** A fetch stub returning a fixed JSON body with a status. */
function jsonFetcher(body: unknown, status = 200): typeof fetch {
  return (async () => ({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  })) as unknown as typeof fetch;
}

/** Resolve on the first published state matching `predicate`, then unsubscribe. */
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

describe("parseSnapshot", () => {
  it("rejects non-object payloads", () => {
    expect(parseSnapshot(null)).toBeNull();
    expect(parseSnapshot("nope")).toBeNull();
    expect(parseSnapshot([1, 2, 3])).toBeNull();
    expect(parseSnapshot(42)).toBeNull();
  });

  it("fills safe empty defaults for a bare object", () => {
    const snapshot = parseSnapshot({});
    expect(snapshot).not.toBeNull();
    expect(snapshot?.online_agents).toEqual([]);
    expect(snapshot?.fleet.agents.live).toEqual([]);
    expect(snapshot?.fleet.claims.active).toBe(0);
    expect(snapshot?.risk.level).toBe("green");
    expect(snapshot?.manifest).toEqual([]);
  });

  it("parses a full fleet and risk section", () => {
    const snapshot = parseSnapshot({
      online_agents: ["a", "b"],
      state: { active_claims: [] },
      board: { ready: ["t1"] },
      manifest: [{ id: "cap" }],
      fleet: {
        agents: { live: ["a"], waiters: ["b-rx"], missing_waiters: ["a-rx"] },
        claims: {
          active: 1,
          stale: 1,
          active_claims: [
            {
              owner: "a",
              task_id: "t1",
              paths: ["x.py"],
              lease_expires_at: 123.5,
              git: { branch: "feat", base: "", auto_release_on: "push" },
            },
          ],
          stale_claims: [{ owner: "b", task_id: "t2", paths: ["y.py"], stale: true }],
        },
        branch_conflicts: [{ owner_a: "a", owner_b: "b" }],
      },
      risk: {
        level: "amber",
        signals: [{ level: "amber", category: "blocked_task", subject: "t3", detail: "waiting" }],
        safe_next_work: ["t4"],
      },
    });
    expect(snapshot?.online_agents).toEqual(["a", "b"]);
    expect(snapshot?.fleet.claims.active_claims[0]?.lease_expires_at).toBe(123.5);
    expect(snapshot?.fleet.claims.active_claims[0]?.git?.base).toBe("main");
    expect(snapshot?.fleet.claims.stale_claims[0]?.stale).toBe(true);
    expect(snapshot?.risk.level).toBe("amber");
    expect(snapshot?.risk.signals[0]?.subject).toBe("t3");
  });

  it("coerces lease timestamps and drops non-object git", () => {
    const snapshot = parseSnapshot({
      fleet: {
        claims: {
          active_claims: [
            { owner: "a", task_id: "t1", lease_expires_at: "200", git: 7, paths: 5 },
            { owner: "b", task_id: "t2", lease_expires_at: "later" },
          ],
        },
      },
    });
    const claims = snapshot?.fleet.claims.active_claims ?? [];
    expect(claims[0]?.lease_expires_at).toBe(200);
    expect(claims[0]?.git).toBeNull();
    expect(claims[0]?.paths).toEqual([]);
    expect(claims[1]?.lease_expires_at).toBeNull();
    // Count falls back to the parsed array length when absent.
    expect(snapshot?.fleet.claims.active).toBe(2);
  });

  it("normalises unknown risk levels to green", () => {
    expect(parseSnapshot({ risk: { level: "chartreuse" } })?.risk.level).toBe("green");
    expect(parseSnapshot({ risk: { level: "red" } })?.risk.level).toBe("red");
  });

  it("coerces non-string scalars to empty strings and drops non-string list items", () => {
    const snapshot = parseSnapshot({
      online_agents: ["ok", 5, null],
      fleet: {
        claims: {
          active_claims: [
            { owner: 42, task_id: true, paths: ["a.py", 9], git: { branch: 1, auto_release_on: 2 } },
          ],
        },
      },
    });
    const claim = snapshot?.fleet.claims.active_claims[0];
    expect(snapshot?.online_agents).toEqual(["ok"]);
    expect(claim?.owner).toBe("");
    expect(claim?.task_id).toBe("");
    expect(claim?.paths).toEqual(["a.py"]);
    expect(claim?.git?.branch).toBe("");
  });
});

describe("withFreshness", () => {
  const base: SnapshotState = {
    snapshot: parseSnapshot({}),
    status: "live",
    fetchedAt: 1000,
    error: null,
  };

  it("leaves a never-fetched state untouched", () => {
    const connecting: SnapshotState = { ...base, snapshot: null, fetchedAt: null, status: "connecting" };
    expect(withFreshness(connecting, 99999)).toBe(connecting);
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

  it("reports an error when the fetch itself throws", async () => {
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

  it("keeps the last good snapshot but reports stale when a later poll fails after the threshold", async () => {
    let clock = 1000;
    let call = 0;
    const fetcher = (async () => {
      call += 1;
      if (call === 1) {
        return { ok: true, status: 200, json: async () => ({ online_agents: ["a"] }) };
      }
      clock = 1000 + 7000; // now past the 6s stale threshold
      throw new Error("dropped");
    }) as unknown as typeof fetch;
    const store = createSnapshotStore({ fetcher, pollMs: 5, staleAfterMs: 6000, now: () => clock });
    await firstState(store, (state) => state.status === "live");
    const stale = await firstState(store, (state) => state.error === "dropped");
    expect(stale.status).toBe("stale");
    expect(stale.snapshot?.online_agents).toEqual(["a"]);
    store.stop();
  });

  it("keeps the last good snapshot and stays live when a later poll fails within the threshold", async () => {
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

  it("falls back to the global fetch and default cadence with no options", () => {
    // Exercises the option defaults; the immediate poll to /snapshot.json fails
    // in the test runtime, which is fine — the store is stopped straight away.
    const store = createSnapshotStore();
    expect(() => store.stop()).not.toThrow();
  });

  it("ignores a poll that resolves after stop", async () => {
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

  it("stops polling and clears listeners on stop", async () => {
    const store = createSnapshotStore({ fetcher: jsonFetcher({}), pollMs: 5, now: () => 1 });
    await firstState(store, (state) => state.status === "live");
    store.stop();
    let notified = false;
    store.subscribe(() => {
      notified = true;
    });
    // A fresh subscriber still gets the current-state replay exactly once...
    expect(notified).toBe(true);
    notified = false;
    await new Promise((resolve) => setTimeout(resolve, 20));
    // ...but no further polls fire after stop.
    expect(notified).toBe(false);
  });
});

describe("configuration pinning fields", () => {
  it("carries hub_version and config_epoch when the hub reports them", () => {
    const parsed = parseSnapshot({ hub_version: "0.98.0", config_epoch: "abc123" });
    expect(parsed?.hub_version).toBe("0.98.0");
    expect(parsed?.config_epoch).toBe("abc123");
    const bare = parseSnapshot({});
    expect(bare?.hub_version).toBe("");
    expect(bare?.config_epoch).toBe("");
  });
});
