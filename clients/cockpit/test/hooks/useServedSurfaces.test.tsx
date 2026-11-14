// @vitest-environment jsdom
// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — the capability summary is read only while someone looks at it

import { cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useServedSurfaces } from "../../src/hooks/useServedSurfaces";

const SUMMARY = {
  surfaces: [{ name: "waits", path: "/waits.json", flag: "--feeds-db", configured: true }],
};

function summaryResponse(): Promise<Response> {
  return Promise.resolve(new Response(JSON.stringify(SUMMARY), { status: 200 }));
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("useServedSurfaces", () => {
  it("reads nothing while the assistant is closed", async () => {
    const fetcher = vi.fn<typeof fetch>(summaryResponse);
    vi.stubGlobal("fetch", fetcher);

    const { result } = renderHook(() => useServedSurfaces(false, 1));
    await Promise.resolve();

    expect(fetcher).not.toHaveBeenCalled();
    expect(result.current.status).toBe("connecting");
  });

  it("reads the summary when opened, forgets it when closed, and rereads for a new bearer", async () => {
    const fetcher = vi.fn<typeof fetch>(summaryResponse);
    vi.stubGlobal("fetch", fetcher);
    const { result, rerender } = renderHook(
      ({ active, revision }) => useServedSurfaces(active, revision),
      { initialProps: { active: true, revision: 1 } },
    );

    await waitFor(() => expect(result.current.status).toBe("live"));
    expect(result.current.data?.configured).toBe(1);
    expect(fetcher).toHaveBeenCalledOnce();

    rerender({ active: false, revision: 1 });
    expect(result.current).toEqual({ data: null, status: "connecting", fetchedAt: null, error: null });

    rerender({ active: true, revision: 2 });
    await waitFor(() => expect(result.current.status).toBe("live"));
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
});
