// @vitest-environment jsdom
// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — dashboard access polling hook tests

import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { useDashboardAccess } from "../../src/hooks/useDashboardAccess";
import { cockpitAuthSnapshot, resetCockpitAuth, unlockCockpit } from "../../src/lib/auth";

function descriptor(role: "viewer" | "operator" = "viewer"): object {
  const writes = role === "operator";
  return {
    version: 1,
    principal: role,
    role,
    capabilities: {
      read: true,
      message_send: writes,
      task_declare: writes,
      task_update: writes,
    },
    operator_armed: true,
    trust_boundary: "presentation hints only; HTTP and hub policy enforce writes",
  };
}

beforeEach(() => {
  sessionStorage.clear();
  resetCockpitAuth();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  sessionStorage.clear();
});

it("does not request access while the auth boundary is blocked", () => {
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  const { result } = renderHook(() => useDashboardAccess(true, 1));
  expect(result.current.phase).toBe("loading");
  expect(fetcher).not.toHaveBeenCalled();
});

it("probes with the session bearer and publishes the strict descriptor", async () => {
  expect(unlockCockpit("operator-secret")).toBe(true);
  const revision = cockpitAuthSnapshot().revision;
  const fetcher = vi.fn(
    async (_input: RequestInfo | URL, _init?: RequestInit) => new Response(JSON.stringify(
      descriptor(fetcher.mock.calls.length === 1 ? "operator" : "viewer"),
    )),
  );
  vi.stubGlobal("fetch", fetcher);
  const { result } = renderHook(() => useDashboardAccess(false, revision, 60_000));
  expect(result.current.phase).toBe("loading");
  await waitFor(() => expect(result.current.phase).toBe("ready"));
  const headers = new Headers(fetcher.mock.calls[0]?.[1]?.headers);
  expect(headers.get("Authorization")).toBe("Bearer operator-secret");
  expect(result.current.descriptor?.role).toBe("operator");
  await act(async () => new Promise((resolve) => setTimeout(resolve, 0)));
  act(() => window.dispatchEvent(new Event("focus")));
  await waitFor(() => expect(result.current.descriptor?.role).toBe("viewer"));
});

it("fails closed on malformed access and resets to loading for a new revision", async () => {
  const fetcher = vi
    .fn<() => Promise<Response>>()
    .mockResolvedValueOnce(new Response(JSON.stringify({ role: "root" })))
    .mockResolvedValueOnce(new Response(JSON.stringify(descriptor("viewer"))));
  vi.stubGlobal("fetch", fetcher);
  const { result, rerender } = renderHook(
    ({ revision }) => useDashboardAccess(false, revision, 60_000),
    { initialProps: { revision: 1 } },
  );
  await waitFor(() => expect(result.current.phase).toBe("unavailable"));
  rerender({ revision: 2 });
  expect(result.current.phase).toBe("loading");
  await waitFor(() => expect(result.current.descriptor?.role).toBe("viewer"));
});

it("ignores a result that resolves after unmount", async () => {
  let resolveResponse: ((response: Response) => void) | undefined;
  const pending = new Promise<Response>((resolve) => {
    resolveResponse = resolve;
  });
  const fetcher = vi.fn(async () => pending);
  vi.stubGlobal("fetch", fetcher);
  const { unmount } = renderHook(() => useDashboardAccess(false, 1, 10));
  act(() => window.dispatchEvent(new Event("focus")));
  expect(fetcher).toHaveBeenCalledOnce();
  unmount();
  await act(async () => {
    resolveResponse?.(new Response(JSON.stringify(descriptor())));
    await pending;
  });
});

it("re-polls on the bounded interval", async () => {
  const fetcher = vi.fn(async () => new Response(JSON.stringify(descriptor())));
  vi.stubGlobal("fetch", fetcher);
  renderHook(() => useDashboardAccess(false, 1, 10));
  await waitFor(() => expect(fetcher.mock.calls.length).toBeGreaterThanOrEqual(2));
});
