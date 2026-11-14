// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — the capability summary parser and feed

import { describe, expect, it, vi } from "vitest";

import type { FeedState } from "../src/lib/feed";
import {
  createServedSurfacesStore,
  parseServedSurfaces,
  SERVED_SURFACES_PATH,
  type ServedSurfaces,
} from "../src/lib/servedSurfaces";

const WAITS = { name: "waits", path: "/waits.json", flag: "--feeds-db", configured: true };
const ATTENTION = {
  name: "attention",
  path: "/attention.json",
  flag: "--attention-store",
  configured: false,
};

describe("parseServedSurfaces", () => {
  it("keeps every well-formed row and counts from the rows, not the server's counts", () => {
    expect(parseServedSurfaces({ surfaces: [WAITS, ATTENTION], configured: 9, total: 9 })).toEqual({
      surfaces: [WAITS, ATTENTION],
      configured: 1,
      total: 2,
    });
  });

  it("drops each malformed row on its own", () => {
    const parsed = parseServedSurfaces({
      surfaces: [
        WAITS,
        null,
        [],
        "waits",
        { ...ATTENTION, name: 7 },
        { ...ATTENTION, path: null },
        { ...ATTENTION, flag: undefined },
        { ...ATTENTION, configured: "no" },
      ],
    });
    expect(parsed).toEqual({ surfaces: [WAITS], configured: 1, total: 1 });
  });

  it("refuses anything that is not a summary", () => {
    for (const raw of [null, "summary", [WAITS], {}, { surfaces: "all" }]) {
      expect(parseServedSurfaces(raw)).toBeNull();
    }
  });
});

describe("createServedSurfacesStore", () => {
  it("reads the summary path and publishes it live", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify({ surfaces: [ATTENTION] }), { status: 200 }),
    );
    const store = createServedSurfacesStore({ fetcher, now: () => 42 });
    const states: FeedState<ServedSurfaces>[] = [];
    store.subscribe((state) => states.push(state));
    await vi.waitFor(() => expect(states.at(-1)?.status).toBe("live"));
    store.stop();

    expect(String(fetcher.mock.calls[0]?.[0])).toBe(SERVED_SURFACES_PATH);
    expect(states.at(-1)).toEqual({
      data: { surfaces: [ATTENTION], configured: 0, total: 1 },
      status: "live",
      fetchedAt: 42,
      error: null,
    });
  });

  it("calls a dashboard without the summary absent", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response("", { status: 404 }));
    const store = createServedSurfacesStore({ url: "/elsewhere.json", pollMs: 5, fetcher });
    const states: FeedState<ServedSurfaces>[] = [];
    store.subscribe((state) => states.push(state));
    await vi.waitFor(() => expect(states.at(-1)?.status).toBe("absent"));
    store.stop();

    expect(String(fetcher.mock.calls[0]?.[0])).toBe("/elsewhere.json");
    expect(states.at(-1)?.data).toBeNull();
  });
});
