// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — sessions, waits, and health-anomalies parsing + feed tests

import { afterEach, describe, expect, it, vi } from "vitest";
import { createHealthAnomaliesStore, parseHealthAnomalies } from "../src/lib/healthAnomalies";
import { createSessionsStore, parseSessions } from "../src/lib/sessions";
import { createWaitsStore, parseWaits } from "../src/lib/waits";
import { factsOf, toastsBetween } from "../src/lib/toasts";

afterEach(() => {
  vi.useRealTimers();
});

describe("parseSessions", () => {
  it("shapes rows newest-first with the task_id cost bridge intact", () => {
    const report = parseSessions({
      generated_from_seq: 7000,
      as_of: 1783.5,
      totals: { total_tokens: 12345, junk: "x" },
      sessions: [
        { agent: "a", session_id: "s1", task_id: "t1", turns: 3, errors: 0, abstentions: 1, input_tokens: 100, output_tokens: 50, total_tokens: 150, seq: 10, ts: 100, cost_usd: 0.0123 },
        { agent: "b", session_id: "s2", turns: 1, seq: 20, ts: 200 },
        { agent: "c", session_id: "s3", turns: 1, seq: 30, ts: 200 },
        "junk",
      ],
      note: "n",
    });
    // Equal-ts tie breaks by seq descending.
    expect(report?.sessions.map((row) => row.sessionId)).toEqual(["s3", "s2", "s1"]);
    expect(report?.sessions[2]).toMatchObject({ taskId: "t1", costUsd: 0.0123, totalTokens: 150 });
    expect(report?.sessions[1]).toMatchObject({ taskId: "", costUsd: null, totalTokens: 0 });
    expect(report?.totals).toEqual({ total_tokens: 12345 });
    expect(report?.generatedFromSeq).toBe(7000);
    expect(parseSessions(null)).toBeNull();
    expect(parseSessions({})?.sessions).toEqual([]);
    expect(parseSessions({ as_of: "junk", totals: "junk" })?.asOf).toBeNull();
  });
});

describe("parseWaits", () => {
  it("shapes gates and defaults counts from the rows", () => {
    const report = parseWaits({
      present: true,
      waits: [
        { task_id: "t", title: "T", who: "a", on_what: ["d1", 5], since: 100, status: "open" },
        "junk",
        { task_id: "u" },
      ],
      wait_count: 2,
      log_end_seq: 7000,
      note: "n",
    });
    expect(report?.waits).toHaveLength(2);
    expect(report?.waits[0]).toMatchObject({ taskId: "t", onWhat: ["d1"], since: 100 });
    expect(report?.waits[1]).toMatchObject({ taskId: "u", onWhat: [], since: null });
    expect(report?.waitCount).toBe(2);
    expect(parseWaits({ waits: [{ task_id: "x" }] })?.waitCount).toBe(1);
    expect(parseWaits({ present: false })?.present).toBe(false);
    expect(parseWaits(null)).toBeNull();
  });
});

describe("parseHealthAnomalies", () => {
  it("shapes the three anomaly classes with spoken details", () => {
    const report = parseHealthAnomalies({
      present: true,
      orphaned: [{ task_id: "o1", owner: "a", seq: 5, age_seconds: 600 }, "junk"],
      dangling: [
        { task_id: "d1", depends_on: ["ghost", 4] },
        { task_id: "d2" },
        { task_id: "d3", depends_on: "solo-dep" },
      ],
      stale: [{ task_id: "s1", owner: "b", age_seconds: 120 }, { task_id: "s2" }],
      anomaly_count: 5,
    });
    expect(report?.orphaned[0]?.detail).toBe("claim is the task's last word · 10 min");
    expect(report?.dangling[0]?.detail).toBe("depends on absent ghost");
    expect(report?.dangling[1]?.detail).toBe("depends on an absent task");
    expect(report?.dangling[2]?.detail).toBe("depends on absent solo-dep");
    expect(report?.stale[0]?.detail).toBe("unreleased and silent · 2 min");
    expect(report?.stale[1]?.detail).toBe("unreleased and silent");
    expect(report?.anomalyCount).toBe(5); // server-stated count wins over row arithmetic
    expect(parseHealthAnomalies({ orphaned: [{ task_id: "x" }] })?.anomalyCount).toBe(1);
    expect(parseHealthAnomalies({ orphaned: [{ task_id: "x" }] })?.orphaned[0]?.detail).toBe(
      "claim is the task's last word",
    );
    expect(parseHealthAnomalies(null)).toBeNull();
    expect(parseHealthAnomalies({ present: false })?.present).toBe(false);
  });
});

describe("config-epoch drift toast", () => {
  it("fires on a changed epoch, stays silent on first sight and empty epochs", () => {
    const before = factsOf([], [], [], null, "aaaa1111");
    const after = factsOf([], [], [], null, "bbbb2222");
    const toasts = toastsBetween(before, after);
    expect(toasts).toHaveLength(1);
    expect(toasts[0]?.text).toBe("hub config epoch changed: aaaa1111 → bbbb2222");
    expect(toastsBetween(factsOf([], [], [], null, ""), after)).toEqual([]);
    expect(toastsBetween(before, factsOf([], [], [], null, ""))).toEqual([]);
    expect(toastsBetween(before, before)).toEqual([]);
  });
});

describe("surface stores ride the shared feed lifecycle", () => {
  it("absent then live for each of the three feeds", async () => {
    vi.useFakeTimers();
    const bodies: Record<string, string> = {
      "/sessions.json": JSON.stringify({ sessions: [], totals: {}, note: "" }),
      "/waits.json": JSON.stringify({ present: true, waits: [], note: "" }),
      "/health-anomalies.json": JSON.stringify({ present: true, orphaned: [], dangling: [], stale: [], anomaly_count: 0 }),
    };
    for (const [factory, url] of [
      [createSessionsStore, "/sessions.json"],
      [createWaitsStore, "/waits.json"],
      [createHealthAnomaliesStore, "/health-anomalies.json"],
    ] as const) {
      const fetcher = vi
        .fn<typeof fetch>()
        .mockResolvedValueOnce(new Response("no", { status: 404 }))
        .mockResolvedValueOnce(new Response(bodies[url] as string));
      const states: { status: string }[] = [];
      const store = factory({ fetcher, pollMs: 1000, now: () => 1 });
      store.subscribe((state) => states.push(state));
      await vi.waitFor(() => {
        expect(states.some((state) => state.status === "absent")).toBe(true);
      });
      await vi.advanceTimersByTimeAsync(1000);
      await vi.waitFor(() => {
        expect(states.at(-1)?.status).toBe("live");
      });
      expect(fetcher.mock.calls[0]?.[0]).toBe(url);
      store.stop();
    }
  });

  it("each store runs on its defaults and surfaces the relative-URL failure", async () => {
    vi.useFakeTimers();
    for (const factory of [createSessionsStore, createWaitsStore, createHealthAnomaliesStore]) {
      const states: { status: string }[] = [];
      const store = factory();
      store.subscribe((state) => states.push(state));
      await vi.waitFor(() => {
        expect(states.at(-1)?.status).toBe("error");
      });
      store.stop();
    }
  });
});
