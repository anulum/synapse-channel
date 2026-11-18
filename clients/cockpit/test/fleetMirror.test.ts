// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE CHANNEL — the Fleet mirror's parser, freshness, board, claims and scope feed

import { afterEach, describe, expect, it, vi } from "vitest";

import {
  MIRROR_STALE_MS,
  createMirrorFeed,
  evidenceLine,
  mirrorBoard,
  mirrorClaimGroups,
  mirrorFreshness,
  mirrorStatusOf,
  parseFleetMirror,
  parseMirrorAccess,
  type MirrorState,
} from "../src/lib/fleetMirror";

afterEach(() => {
  vi.useRealTimers();
});

const PEER = {
  peer_id: "hub-a",
  cursor: 40,
  events: 41,
  last_success_at: 1_800_000_000.5,
  consecutive_failures: 0,
  status_written_at: 1_800_000_001,
  caught_up: true,
  budget_exhausted_reason: null,
};

const TASK = {
  task_id: "T2",
  status: "in_progress",
  title: "Second",
  claimed_by: "P/worker",
  claim_hub: "hub-b",
  board_provenance: { hub: "hub-b", seq: 7 },
  board_conflict: null,
};

function exportOf(changes: Record<string, unknown> = {}, snapshot: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    version: 1,
    source_id: "fleet-1",
    exported_at: 1_800_000_100,
    advisory: true,
    snapshot: {
      advisory: "Advisory: Fleet's view, not a hub's state.",
      generated_at: 1_800_000_099,
      peers: [PEER],
      tasks: [
        TASK,
        { ...TASK, task_id: "T1", title: "First", board_conflict: { hubs: ["hub-a", "hub-b"] } },
        { ...TASK, task_id: "T3", claimed_by: null, claim_hub: null, board_provenance: null },
        { ...TASK, task_id: "T4", claim_hub: null },
      ],
      progress_notes: 3,
      ...snapshot,
    },
    ...changes,
  };
}

describe("parseFleetMirror", () => {
  it("reads a version-one export whole", () => {
    const mirror = parseFleetMirror(exportOf());
    expect(mirror).not.toBeNull();
    expect(mirror?.sourceId).toBe("fleet-1");
    expect(mirror?.exportedAt).toBe(1_800_000_100);
    expect(mirror?.generatedAt).toBe(1_800_000_099);
    expect(mirror?.advisoryText).toContain("Advisory");
    expect(mirror?.progressNotes).toBe(3);
    expect(mirror?.skippedRows).toBe(0);
    expect(mirror?.peers).toEqual([
      {
        peerId: "hub-a",
        cursor: 40,
        events: 41,
        lastSuccessAt: 1_800_000_000.5,
        consecutiveFailures: 0,
        caughtUp: true,
        drainLimit: null,
      },
    ]);
    expect(mirror?.tasks.map((task) => task.taskId)).toEqual(["T2", "T1", "T3", "T4"]);
  });

  it("refuses an envelope or a snapshot outside the contract", () => {
    const refused: unknown[] = [
      null,
      [],
      "export",
      exportOf({ version: 2 }),
      exportOf({ version: "1" }),
      exportOf({ advisory: false }),
      exportOf({ source_id: "-bad" }),
      exportOf({ source_id: 7 }),
      exportOf({ exported_at: -1 }),
      exportOf({ exported_at: Number.NaN }),
      exportOf({ extra: 1 }),
      exportOf({ snapshot: [] }),
      exportOf({}, { advisory: 7 }),
      exportOf({}, { generated_at: "now" }),
      exportOf({}, { progress_notes: 1.5 }),
      exportOf({}, { extra: true }),
      exportOf({}, { peers: {} }),
      exportOf({}, { tasks: Array.from({ length: 10001 }, () => TASK) }),
    ];
    for (const raw of refused) expect(parseFleetMirror(raw), JSON.stringify(raw)?.slice(0, 80)).toBeNull();
  });

  it("leaves out and counts each row that does not read, and a repeated identity", () => {
    const peers = [
      PEER,
      { ...PEER, peer_id: "hub-c", consecutive_failures: null, caught_up: null, budget_exhausted_reason: "pages", last_success_at: null },
      { ...PEER, peer_id: "" },
      { ...PEER, peer_id: "hub-d", cursor: -1 },
      { ...PEER, peer_id: "hub-e", events: 1.5 },
      { ...PEER, peer_id: "hub-f", consecutive_failures: "1" },
      { ...PEER, peer_id: "hub-g", last_success_at: "then" },
      { ...PEER, peer_id: "hub-h", status_written_at: -2 },
      { ...PEER, peer_id: "hub-i", caught_up: "yes" },
      { ...PEER, peer_id: "hub-j", budget_exhausted_reason: "memory" },
      { ...PEER, peer_id: "hub-k", extra: 1 },
      PEER,
      "peer",
    ];
    const tasks = [
      TASK,
      { ...TASK, task_id: "" },
      { ...TASK, task_id: "T5", status: 1 },
      { ...TASK, task_id: "T6", title: null },
      { ...TASK, task_id: "T7", claimed_by: 3 },
      { ...TASK, task_id: "T8", claim_hub: 3 },
      { ...TASK, task_id: "T9", board_provenance: [] },
      { ...TASK, task_id: "T10", board_conflict: "yes" },
      { ...TASK, task_id: "T11", extra: 1 },
      TASK,
    ];
    const mirror = parseFleetMirror(exportOf({}, { peers, tasks }));
    expect(mirror?.peers.map((peer) => peer.peerId)).toEqual(["hub-a", "hub-c"]);
    expect(mirror?.peers[1]).toMatchObject({ consecutiveFailures: null, caughtUp: null, drainLimit: "pages" });
    expect(mirror?.tasks.map((task) => task.taskId)).toEqual(["T2"]);
    expect(mirror?.skippedRows).toBe(11 + 9);
  });
});

describe("the access descriptor", () => {
  it("offers the scope only on a version-one observe and advisory answer", () => {
    expect(parseMirrorAccess({ version: 1, observe: true, advisory: true })).toBe(true);
    expect(parseMirrorAccess({ version: 1, observe: false, advisory: true })).toBe(false);
    expect(parseMirrorAccess({ version: 1, observe: true, advisory: false })).toBe(false);
    expect(parseMirrorAccess({ version: 2, observe: true, advisory: true })).toBeNull();
    expect(parseMirrorAccess(null)).toBeNull();
  });
});

describe("freshness, board, claims and evidence", () => {
  it("says how old an export is and when it is stale", () => {
    expect(mirrorFreshness(100, 100_000 + 60_000)).toEqual({ ageSeconds: 60, stale: false });
    expect(mirrorFreshness(100, 100_000 + MIRROR_STALE_MS + 1)).toEqual({ ageSeconds: 300, stale: true });
    expect(mirrorFreshness(200, 100_000)).toEqual({ ageSeconds: 0, stale: false });
    expect(mirrorFreshness(100, 100_000 + 2_000, 1_000).stale).toBe(true);
  });

  it("puts conflicts first on the board and groups claims by the hub that holds them", () => {
    const mirror = parseFleetMirror(exportOf());
    if (mirror === null) throw new Error("export did not parse");
    expect(mirrorBoard(mirror).map((task) => task.taskId)).toEqual(["T1", "T2", "T3", "T4"]);
    expect(mirrorClaimGroups(mirror).map((group) => [group.hub, group.tasks.map((task) => task.taskId)])).toEqual([
      ["", ["T4"]],
      ["hub-b", ["T1", "T2"]],
    ]);
    expect(evidenceLine({ seq: 7, hub: "hub-b" })).toBe("hub: hub-b · seq: 7");
    expect(evidenceLine(null)).toBe("");
  });
});

describe("createMirrorFeed", () => {
  it("maps each answer: live, unavailable keeps the export, incompatible, absent, error", async () => {
    vi.useFakeTimers();
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(new Response(JSON.stringify(exportOf())))
      .mockResolvedValueOnce(new Response("down", { status: 503 }))
      .mockResolvedValueOnce(new Response("old", { status: 409 }))
      .mockResolvedValueOnce(new Response("none", { status: 404 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ version: 1 })))
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValueOnce(new Response("teapot", { status: 418 }));
    const states: MirrorState[] = [];
    const feed = createMirrorFeed({ fetcher, pollMs: 1000, now: () => 9_000 });
    const unsubscribe = feed.subscribe((state) => states.push(state));
    await vi.waitFor(() => {
      expect(states.at(-1)?.status).toBe("live");
    });
    expect(states.at(-1)?.fetchedAt).toBe(9_000);
    for (const expected of ["unavailable", "incompatible", "absent", "error", "error", "error"]) {
      await vi.advanceTimersByTimeAsync(1000);
      await vi.waitFor(() => {
        expect(states.at(-1)?.status).toBe(expected);
      });
      expect(states.at(-1)?.mirror?.sourceId).toBe("fleet-1");
    }
    unsubscribe();
    feed.stop();
  });

  it("withdraws on a 403 and polls no more", async () => {
    vi.useFakeTimers();
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response("gone", { status: 403 }));
    const states: MirrorState[] = [];
    const feed = createMirrorFeed({ fetcher, pollMs: 1000 });
    feed.subscribe((state) => states.push(state));
    await vi.waitFor(() => {
      expect(states.at(-1)?.status).toBe("withdrawn");
    });
    await vi.advanceTimersByTimeAsync(5000);
    expect(fetcher).toHaveBeenCalledTimes(1);
    feed.stop();
  });

  it("publishes nothing once stopped, and runs on its defaults", async () => {
    vi.useFakeTimers();
    let release: (response: Response) => void = () => undefined;
    const fetcher = vi.fn<typeof fetch>().mockReturnValue(new Promise<Response>((resolve) => (release = resolve)));
    const states: MirrorState[] = [];
    const feed = createMirrorFeed({ fetcher });
    feed.subscribe((state) => states.push(state));
    feed.stop();
    release(new Response(JSON.stringify(exportOf())));
    await vi.advanceTimersByTimeAsync(10);
    expect(states.map((state) => state.status)).toEqual(["connecting"]);
    const failing = vi.fn<typeof fetch>().mockRejectedValue(new Error("aborted"));
    const quiet = createMirrorFeed({ fetcher: failing });
    quiet.stop();
    await vi.advanceTimersByTimeAsync(10);
    const defaults = createMirrorFeed();
    const seen: MirrorState[] = [];
    defaults.subscribe((state) => seen.push(state));
    await vi.waitFor(() => {
      expect(seen.at(-1)?.status).toBe("error");
    });
    defaults.stop();
    expect(mirrorStatusOf(500)).toBe("error");
  });
});
