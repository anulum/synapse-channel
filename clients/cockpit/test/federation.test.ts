// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — federation-posture parsing and feed tests

import { afterEach, describe, expect, it, vi } from "vitest";
import {
  contestedNamespaces,
  createFederationStore,
  parseFederation,
  type FederationState,
} from "../src/lib/federation";

afterEach(() => {
  vi.useRealTimers();
});

describe("parseFederation", () => {
  it("parses the proposed posture contract", () => {
    const posture = parseFederation({
      hub_id: "syn-635eb7b8",
      domain: "workstation",
      peerings: [
        { domain: "ml350", state: "active", imported_at: 1783000000.5, fingerprint: "ab:cd" },
        { domain: "laptop", state: "revoked" },
      ],
      namespaces: [
        { namespace: "REPO", outcome: "local", owner_hub: "syn-635eb7b8", contesting: [] },
        {
          namespace: "SHARED",
          outcome: "partitioned",
          owner_hub: "",
          contesting: ["syn-a", "syn-b", 7],
        },
      ],
    });
    expect(posture).toEqual({
      hubId: "syn-635eb7b8",
      domain: "workstation",
      peerings: [
        { domain: "ml350", state: "active", importedAt: 1783000000.5, fingerprint: "ab:cd" },
        { domain: "laptop", state: "revoked", importedAt: null, fingerprint: "" },
      ],
      namespaces: [
        { namespace: "REPO", outcome: "local", ownerHub: "syn-635eb7b8", contesting: [] },
        { namespace: "SHARED", outcome: "partitioned", ownerHub: "", contesting: ["syn-a", "syn-b"] },
      ],
    });
  });

  it("rejects non-objects and tolerates missing sections", () => {
    expect(parseFederation(null)).toBeNull();
    expect(parseFederation([])).toBeNull();
    const empty = parseFederation({ peerings: "junk", namespaces: "junk" });
    expect(empty).toEqual({ hubId: "", domain: "", peerings: [], namespaces: [] });
    const junkEntries = parseFederation({ peerings: ["junk"], namespaces: [42] });
    expect(junkEntries?.peerings).toEqual([
      { domain: "", state: "", importedAt: null, fingerprint: "" },
    ]);
    expect(junkEntries?.namespaces).toEqual([
      { namespace: "", outcome: "", ownerHub: "", contesting: [] },
    ]);
  });
});

describe("contestedNamespaces", () => {
  it("returns exactly the partitioned namespaces", () => {
    const posture = parseFederation({
      namespaces: [
        { namespace: "calm", outcome: "local" },
        { namespace: "split", outcome: "partitioned", contesting: ["a", "b"] },
        { namespace: "away", outcome: "remote" },
      ],
    });
    expect(posture).not.toBeNull();
    if (posture === null) throw new Error("unreachable");
    expect(contestedNamespaces(posture).map((entry) => entry.namespace)).toEqual(["split"]);
  });
});

describe("createFederationStore", () => {
  it("polls through the shared feed lifecycle: absent, then live", async () => {
    vi.useFakeTimers();
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(new Response("no", { status: 404 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ hub_id: "syn-x" })));
    const states: FederationState[] = [];
    const store = createFederationStore({ fetcher, pollMs: 1000, now: () => 7_000 });
    store.subscribe((state) => states.push(state));

    await vi.waitFor(() => {
      expect(states.at(-1)?.status).toBe("absent");
    });
    await vi.advanceTimersByTimeAsync(1000);
    await vi.waitFor(() => {
      expect(states.at(-1)?.status).toBe("live");
    });
    expect(states.at(-1)?.data?.hubId).toBe("syn-x");
    expect(states.at(-1)?.fetchedAt).toBe(7_000);
    store.stop();
  });

  it("runs on its defaults and surfaces the relative-URL failure", async () => {
    vi.useFakeTimers();
    const states: FederationState[] = [];
    const store = createFederationStore();
    store.subscribe((state) => states.push(state));
    await vi.waitFor(() => {
      expect(states.at(-1)?.status).toBe("error");
    });
    store.stop();
  });
});
