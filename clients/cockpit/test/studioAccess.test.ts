// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — vanilla Studio access polling and safe role rendering

// @vitest-environment jsdom

/// <reference types="node" />

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { afterEach, expect, it, vi } from "vitest";

interface AccessState {
  readonly phase: "ready" | "unavailable";
  readonly principal: string | null;
  readonly role: "viewer" | "operator" | "admin" | null;
}

interface StudioAccess {
  parseDescriptor(value: unknown): AccessState | null;
  refresh(): Promise<AccessState>;
  snapshot(): AccessState;
  subscribe(listener: () => void): () => void;
}

const accessSource = readFileSync(
  resolve(process.cwd(), "../../src/synapse_channel/dashboard_assets/studio-access.js"),
  "utf8",
);

function descriptor(
  role: "viewer" | "operator" | "admin",
  principal: string = role,
): object {
  const writes = role !== "viewer";
  return {
    version: 1,
    principal,
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

function loadAccess(): StudioAccess {
  document.body.innerHTML = `
    <script id="syn-studio-config" type="application/json">
      {"accessUrl":"/access","pollMs":1000}
    </script>
    <div role="status" aria-live="polite"><b id="cc-access">loading</b></div>
  `;
  vi.spyOn(globalThis, "setTimeout").mockImplementation(() => 1 as never);
  window.eval(accessSource);
  return (window as typeof window & { SynapseStudioAccess: StudioAccess }).SynapseStudioAccess;
}

afterEach(() => {
  sessionStorage.clear();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it("polls with the session bearer and renders principal text without HTML", async () => {
  const principal = '<img id="pwn" onerror="window.__pwned=1">';
  sessionStorage.setItem("synapse-cockpit-bearer", "viewer-secret");
  const fetchMock = vi.fn(
    async (_input: RequestInfo | URL, _init?: RequestInit) =>
      new Response(JSON.stringify(descriptor("viewer", principal))),
  );
  vi.stubGlobal("fetch", fetchMock);

  const access = loadAccess();
  await vi.waitFor(() => expect(access.snapshot().phase).toBe("ready"));

  const request = fetchMock.mock.calls[0];
  expect(String(request?.[0])).toBe("/access");
  expect(new Headers(request?.[1]?.headers).get("Authorization")).toBe("Bearer viewer-secret");
  expect(document.getElementById("cc-access")?.textContent).toBe(`viewer · ${principal}`);
  expect(document.querySelector("#pwn, img, [onerror]")).toBeNull();
});

it("publishes a downgrade and supports unsubscribe", async () => {
  const fetchMock = vi
    .fn<() => Promise<Response>>()
    .mockResolvedValueOnce(new Response(JSON.stringify(descriptor("operator"))))
    .mockResolvedValueOnce(new Response(JSON.stringify(descriptor("viewer"))));
  vi.stubGlobal("fetch", fetchMock);
  const access = loadAccess();
  await vi.waitFor(() => expect(access.snapshot().role).toBe("operator"));
  const listener = vi.fn();
  const unsubscribe = access.subscribe(listener);

  await access.refresh();
  expect(access.snapshot().role).toBe("viewer");
  expect(listener).toHaveBeenCalledOnce();
  unsubscribe();
  await access.refresh();
  expect(listener).toHaveBeenCalledOnce();
});

it.each([
  new Response("offline", { status: 503 }),
  new Response(JSON.stringify({ role: "root" })),
])("fails visibly for unavailable or malformed access", async (response) => {
  vi.stubGlobal("fetch", vi.fn(async () => response));
  const access = loadAccess();
  await vi.waitFor(() =>
    expect(document.getElementById("cc-access")?.textContent).toBe("access unavailable"),
  );
  expect(access.snapshot().phase).toBe("unavailable");
  expect(access.parseDescriptor({ ...descriptor("viewer"), extra: true })).toBeNull();
});
