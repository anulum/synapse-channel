// @vitest-environment jsdom
// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — session-only bearer and authenticated fetch tests

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  authenticatedFetch,
  cockpitAuthSnapshot,
  COCKPIT_BEARER_KEY,
  fetchWithCockpitAuth,
  lockCockpit,
  resetCockpitAuth,
  subscribeCockpitAuth,
  unlockCockpit,
} from "../src/lib/auth";

beforeEach(() => {
  window.sessionStorage.clear();
  resetCockpitAuth();
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  window.sessionStorage.clear();
});

describe("cockpit bearer session", () => {
  it("initializes from session storage, trims unlocks, notifies, and locks idempotently", () => {
    expect(cockpitAuthSnapshot().phase).toBe("probing");
    expect(cockpitAuthSnapshot().phase).toBe("probing");
    expect(unlockCockpit("   ")).toBe(false);

    const listener = vi.fn();
    const unsubscribe = subscribeCockpitAuth(listener);
    expect(unlockCockpit("  current-token  ")).toBe(true);
    expect(window.sessionStorage.getItem(COCKPIT_BEARER_KEY)).toBe("current-token");
    expect(cockpitAuthSnapshot()).toMatchObject({ phase: "unlocked", reason: null });
    expect(listener).toHaveBeenCalledTimes(1);

    lockCockpit("revoked");
    const locked = cockpitAuthSnapshot();
    expect(locked).toMatchObject({ phase: "locked", reason: "revoked" });
    expect(window.sessionStorage.getItem(COCKPIT_BEARER_KEY)).toBeNull();
    lockCockpit("revoked");
    expect(cockpitAuthSnapshot().revision).toBe(locked.revision);
    expect(listener).toHaveBeenCalledTimes(2);

    unsubscribe();
    resetCockpitAuth();
    expect(cockpitAuthSnapshot()).toMatchObject({ phase: "probing", reason: null });
    expect(listener).toHaveBeenCalledTimes(2);
  });

  it("recognizes a pre-existing non-empty bearer and ignores an empty stored value", () => {
    window.sessionStorage.setItem(COCKPIT_BEARER_KEY, "stored");
    expect(cockpitAuthSnapshot().phase).toBe("unlocked");

    resetCockpitAuth();
    window.sessionStorage.setItem(COCKPIT_BEARER_KEY, "  ");
    expect(cockpitAuthSnapshot().phase).toBe("probing");
  });

  it("fails safe when browser storage reads, writes, or removals throw", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("read denied");
    });
    expect(cockpitAuthSnapshot().phase).toBe("probing");
    vi.restoreAllMocks();

    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("write denied");
    });
    expect(unlockCockpit("token")).toBe(false);
    vi.restoreAllMocks();

    vi.spyOn(Storage.prototype, "removeItem").mockImplementation(() => {
      throw new Error("remove denied");
    });
    lockCockpit("storage changed");
    expect(cockpitAuthSnapshot().phase).toBe("locked");
    resetCockpitAuth();
    expect(cockpitAuthSnapshot().phase).toBe("probing");
  });
});

describe("authenticated fetch", () => {
  it("probes an open dashboard without a bearer and preserves caller headers", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response("ok", { status: 200 }));
    const response = await fetchWithCockpitAuth(
      "/snapshot.json",
      { headers: { "X-Cockpit-Test": "yes" } },
      fetcher,
    );
    expect(response.status).toBe(200);
    const headers = new Headers(fetcher.mock.calls[0]?.[1]?.headers);
    expect(headers.get("X-Cockpit-Test")).toBe("yes");
    expect(headers.has("Authorization")).toBe(false);
    expect(cockpitAuthSnapshot().phase).toBe("open");

    await fetchWithCockpitAuth("/optional.json", undefined, fetcher);
    expect(cockpitAuthSnapshot().phase).toBe("open");
  });

  it("merges Request and init headers and attaches the current bearer", async () => {
    expect(unlockCockpit("secret")).toBe(true);
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(null, { status: 204 }));
    const request = new Request("https://cockpit.invalid/snapshot.json", {
      headers: { "X-Request": "kept", "X-Override": "old" },
    });
    await fetchWithCockpitAuth(
      request,
      { headers: { "X-Override": "new" } },
      fetcher,
    );
    const headers = new Headers(fetcher.mock.calls[0]?.[1]?.headers);
    expect(headers.get("Authorization")).toBe("Bearer secret");
    expect(headers.get("X-Request")).toBe("kept");
    expect(headers.get("X-Override")).toBe("new");
    expect(cockpitAuthSnapshot().phase).toBe("unlocked");
  });

  it("clears the bearer and publishes one lock generation on repeated 401s", async () => {
    expect(unlockCockpit("wrong")).toBe(true);
    const listener = vi.fn();
    const unsubscribe = subscribeCockpitAuth(listener);
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response("no", { status: 401 }));

    await fetchWithCockpitAuth("/snapshot.json", undefined, fetcher);
    const first = cockpitAuthSnapshot();
    expect(first.phase).toBe("locked");
    expect(first.reason).toContain("refused");
    expect(window.sessionStorage.getItem(COCKPIT_BEARER_KEY)).toBeNull();
    await fetchWithCockpitAuth("/events.json", undefined, fetcher);
    expect(cockpitAuthSnapshot().revision).toBe(first.revision);
    expect(listener).toHaveBeenCalledTimes(1);
    unsubscribe();
  });

  it("does not let a delayed 401 erase a newer credential generation", async () => {
    expect(unlockCockpit("old-token")).toBe(true);
    let resolveResponse: ((response: Response) => void) | undefined;
    const fetcher = vi.fn<typeof fetch>().mockImplementation(
      () =>
        new Promise<Response>((resolve) => {
          resolveResponse = resolve;
        }),
    );
    const pending = fetchWithCockpitAuth("/snapshot.json", undefined, fetcher);
    expect(unlockCockpit("new-token")).toBe(true);
    resolveResponse?.(new Response("late refusal", { status: 401 }));
    await pending;
    expect(cockpitAuthSnapshot().phase).toBe("unlocked");
    expect(sessionStorage.getItem(COCKPIT_BEARER_KEY)).toBe("new-token");
  });

  it("uses the call-time global fetch through the production adapter", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response("missing", { status: 404 }));
    vi.stubGlobal("fetch", fetcher);
    expect((await authenticatedFetch("/optional.json")).status).toBe(404);
    expect(fetcher).toHaveBeenCalledOnce();
    expect(cockpitAuthSnapshot().phase).toBe("open");
  });
});
