// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — fleet time-travel parsing and fetch tests

import { describe, expect, it, vi } from "vitest";
import { fetchStateAt, parseStateAt } from "../src/lib/stateAt";

function document_(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    as_of_seq: 3000,
    log_end_seq: 6882,
    note: "claims and board reconstructed; presence omitted",
    state: {
      generated_at: 1_782_663_600,
      active_claims: [
        {
          task_id: "auth-runtime",
          owner: "a/tx",
          lease_expires_at: 1_782_667_172,
          paths: ["src/auth.py", 42],
          git: { branch: "main", base: "main", auto_release_on: "merge" },
        },
        { task_id: "expired-one", owner: "b/tx", lease_expires_at: 1_782_663_000 },
      ],
    },
    board: {
      tasks: [
        { task_id: "base", title: "Base", status: "done", depends_on: [] },
        { task_id: "child", title: "Child", status: "open", depends_on: ["base"] },
        { task_id: "child2", title: "Child2", status: "open", depends_on: ["base"] },
        { task_id: "stuck", title: "Stuck", status: "open", depends_on: ["ghost"] },
        { task_id: "stuck2", title: "Stuck2", status: "open", depends_on: ["ghost"] },
        { task_id: "done2", title: "Done2", status: "done" },
        { task_id: "", status: "open" },
      ],
    },
    ...overrides,
  };
}

describe("parseStateAt", () => {
  it("shapes claims judged at the moment's own clock", () => {
    const state = parseStateAt(document_());
    expect(state).not.toBeNull();
    expect(state?.asOfSeq).toBe(3000);
    expect(state?.logEndSeq).toBe(6882);
    expect(state?.asOfTs).toBe(1_782_663_600);
    const live = state?.claims.find((view) => view.claim.task_id === "auth-runtime");
    expect(live?.urgency).toBe("held");
    expect(live?.secondsToExpiry).toBe(1_782_667_172 - 1_782_663_600);
    expect(live?.claim.paths).toEqual(["src/auth.py"]);
    expect(live?.claim.git?.branch).toBe("main");
    const expired = state?.claims.find((view) => view.claim.task_id === "expired-one");
    expect(expired?.urgency).toBe("stale");
    expect(expired?.claim.stale).toBe(true);
    expect(expired?.claim.git).toBeNull();
  });

  it("reconstructs board buckets, dependency verdicts, and unblocks", () => {
    const state = parseStateAt(document_());
    const byId = new Map(state?.tasks.map((task) => [task.taskId, task]));
    expect(byId.get("base")?.bucket).toBe("done");
    expect(byId.get("base")?.unblocks).toEqual(["child", "child2"]);
    expect(byId.get("child")?.bucket).toBe("open");
    expect(byId.get("child")?.dependsOn[0]).toMatchObject({ taskId: "base", satisfied: true, missing: false });
    expect(byId.get("stuck")?.bucket).toBe("blocked");
    expect(byId.get("stuck")?.dependsOn[0]).toMatchObject({ taskId: "ghost", missing: true });
    // Blank ids are dropped; blocked ranks first (ties by id), done last.
    expect(state?.tasks.map((task) => task.bucket)).toEqual([
      "blocked",
      "blocked",
      "open",
      "open",
      "done",
      "done",
    ]);
    expect(state?.tasks[0]?.taskId).toBe("stuck");
    expect(state?.tasks[1]?.taskId).toBe("stuck2");
  });

  it("answers an empty or junk moment honestly", () => {
    expect(parseStateAt(null)).toBeNull();
    expect(parseStateAt([1])).toBeNull();
    const empty = parseStateAt({});
    expect(empty).toMatchObject({ asOfSeq: 0, logEndSeq: 0, asOfTs: 0, claims: [], tasks: [] });
    const junkLease = parseStateAt(
      document_({ state: { generated_at: 10, active_claims: [{ task_id: "x", owner: "o", lease_expires_at: "junk", paths: "junk" }] } }),
    );
    expect(junkLease?.claims[0]).toMatchObject({
      urgency: "held",
      secondsToExpiry: null,
      claim: { paths: [], stale: false },
    });
  });
});

describe("fetchStateAt", () => {
  it("clamps the position into the URL and maps outcomes", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify(document_())));
    const loaded = await fetchStateAt(-5, fetcher);
    expect(fetcher.mock.calls[0]?.[0]).toBe("/state-at.json?seq=0");
    expect(loaded.kind).toBe("loaded");
    expect(
      await fetchStateAt(1, vi.fn<typeof fetch>().mockResolvedValue(new Response("no", { status: 404 }))),
    ).toEqual({ kind: "absent" });
    expect(
      await fetchStateAt(1, vi.fn<typeof fetch>().mockResolvedValue(new Response("boom", { status: 500 }))),
    ).toEqual({ kind: "error", message: "hub returned 500" });
    expect(
      await fetchStateAt(1, vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify([1])))),
    ).toEqual({ kind: "error", message: "state-at payload was not an object" });
    expect(await fetchStateAt(1, vi.fn<typeof fetch>().mockRejectedValue(new Error("gone")))).toEqual({
      kind: "error",
      message: "gone",
    });
    expect(await fetchStateAt(1, vi.fn<typeof fetch>().mockRejectedValue("plain"))).toEqual({
      kind: "error",
      message: "plain",
    });
  });

  it("runs on its defaults against the global fetch, which fails visibly in tests", async () => {
    expect((await fetchStateAt(10)).kind).toBe("error");
  });
});
