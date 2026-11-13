// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — findings feed parsing and store lifecycle tests

import { afterEach, describe, expect, it, vi } from "vitest";
import { createFindingsStore, parseFindings, type FindingsState } from "../src/lib/findings";

afterEach(() => {
  vi.useRealTimers();
});

const SERVED = {
  findings: [
    {
      seq: 42,
      ts: 1783.5,
      statement: "the receipt list repeats every assessment note",
      subkind: "defect",
      evidence_kind: "measured",
      claim_status: "reference-validated",
      freshness: "verified-at-source",
      lifecycle: "active",
      evidence_ref: "src/synapse_channel/dashboard.py:244",
      provenance: { project: "SYNAPSE", actor: "claude-b0f9", session: "s1", source_event_seq: 40, ts: 1783.5 },
      verified_at_source: { checked_this_session: true, source_ref: "dashboard.py", by: "claude-b0f9", at: 1783.5 },
    },
    {
      seq: 17,
      ts: 1700,
      statement: "the poll downloads the closed tasks",
      subkind: "measurement",
      evidence_kind: null,
      claim_status: null,
      freshness: "untraceable",
      lifecycle: "retracted",
      evidence_ref: null,
      provenance: { project: "SYNAPSE", actor: "codex-01a1" },
      verified_at_source: { checked_this_session: false, source_ref: "", by: "codex-01a1", at: 1700 },
    },
  ],
  total: 9,
  next_before: 17,
};

describe("parseFindings", () => {
  it("shapes each served row with its attested author and its honesty axes", () => {
    expect(parseFindings(SERVED)).toEqual({
      findings: [
        {
          seq: 42,
          ts: 1783.5,
          statement: "the receipt list repeats every assessment note",
          subkind: "defect",
          actor: "claude-b0f9",
          project: "SYNAPSE",
          evidenceKind: "measured",
          claimStatus: "reference-validated",
          freshness: "verified-at-source",
          lifecycle: "active",
          evidenceRef: "src/synapse_channel/dashboard.py:244",
        },
        {
          seq: 17,
          ts: 1700,
          statement: "the poll downloads the closed tasks",
          subkind: "measurement",
          actor: "codex-01a1",
          project: "SYNAPSE",
          evidenceKind: "",
          claimStatus: "",
          freshness: "untraceable",
          lifecycle: "retracted",
          evidenceRef: "",
        },
      ],
      total: 9,
    });
  });

  it("refuses a payload that is not an object", () => {
    expect(parseFindings(null)).toBeNull();
    expect(parseFindings("findings")).toBeNull();
    expect(parseFindings([SERVED])).toBeNull();
  });

  it("reads a missing or malformed list as no findings and counts the page", () => {
    expect(parseFindings({})).toEqual({ findings: [], total: 0 });
    expect(parseFindings({ findings: "x", total: 3 })).toEqual({ findings: [], total: 3 });
  });

  it("skips entries that are not objects and fills the gaps of a sparse one", () => {
    const report = parseFindings({
      findings: [null, 7, ["x"], { seq: Number.NaN, ts: "late", provenance: "x", freshness: 3 }],
      total: Number.POSITIVE_INFINITY,
    });
    expect(report).toEqual({
      findings: [
        {
          seq: 0,
          ts: null,
          statement: "",
          subkind: "",
          actor: "",
          project: "",
          evidenceKind: "",
          claimStatus: "",
          freshness: "",
          lifecycle: "",
          evidenceRef: "",
        },
      ],
      total: 1,
    });
  });
});

describe("createFindingsStore", () => {
  it("reads /findings.json: absent without an event store, then live", async () => {
    vi.useFakeTimers();
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(new Response("findings: start the dashboard with --feeds-db\n", { status: 404 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(SERVED)));
    const states: FindingsState[] = [];
    const store = createFindingsStore({ fetcher, pollMs: 1000, now: () => 1 });
    store.subscribe((state) => states.push(state));
    await vi.waitFor(() => {
      expect(states.some((state) => state.status === "absent")).toBe(true);
    });
    await vi.advanceTimersByTimeAsync(1000);
    await vi.waitFor(() => {
      expect(states.at(-1)?.status).toBe("live");
    });
    expect(states.at(-1)?.data?.findings.map((row) => row.seq)).toEqual([42, 17]);
    expect(fetcher.mock.calls[0]?.[0]).toBe("/findings.json");
    store.stop();
  });

  it("takes another address; on its defaults it surfaces the relative-URL failure", async () => {
    vi.useFakeTimers();
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify(SERVED)));
    const store = createFindingsStore({ url: "/elsewhere.json", fetcher });
    const states: FindingsState[] = [];
    store.subscribe((state) => states.push(state));
    await vi.waitFor(() => {
      expect(states.at(-1)?.status).toBe("live");
    });
    expect(fetcher.mock.calls[0]?.[0]).toBe("/elsewhere.json");
    store.stop();

    const defaults = createFindingsStore();
    const defaultStates: FindingsState[] = [];
    defaults.subscribe((state) => defaultStates.push(state));
    await vi.waitFor(() => {
      expect(defaultStates.at(-1)?.status).toBe("error");
    });
    defaults.stop();
  });
});
