// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — strict dashboard access descriptor tests

import { describe, expect, it, vi } from "vitest";

import {
  capabilitiesOf,
  DASHBOARD_ACCESS_URL,
  fetchDashboardAccess,
  LOADING_DASHBOARD_ACCESS,
  lostWriteCapability,
  NO_DASHBOARD_CAPABILITIES,
  parseDashboardAccess,
  readOnlyCapabilities,
  UNAVAILABLE_DASHBOARD_ACCESS,
  type DashboardCapabilities,
} from "../src/lib/access";

const VIEWER: DashboardCapabilities = {
  read: true,
  message_send: false,
  task_declare: false,
  task_update: false,
  setup_plan: false,
  setup_apply: false,
};
const OPERATOR: DashboardCapabilities = {
  read: true,
  message_send: true,
  task_declare: true,
  task_update: true,
  setup_plan: false,
  setup_apply: false,
};

function descriptor(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    version: 1,
    principal: "ops-a",
    role: "operator",
    capabilities: OPERATOR,
    operator_armed: true,
    trust_boundary: "presentation hints only; HTTP and hub policy enforce writes",
    ...overrides,
  };
}

describe("parseDashboardAccess", () => {
  it("reads an admin's setup capabilities as the server gives them", () => {
    const admin = { ...OPERATOR, setup_plan: true, setup_apply: true };
    const parsed = parseDashboardAccess(descriptor({ role: "admin", capabilities: admin }));
    expect(parsed?.capabilities).toEqual(admin);
    for (const [plan, apply] of [[true, false], [false, true]] as const) {
      const uneven = { ...OPERATOR, setup_plan: plan, setup_apply: apply };
      const read = parseDashboardAccess(descriptor({ role: "admin", capabilities: uneven }));
      expect([read?.capabilities.setup_plan, read?.capabilities.setup_apply]).toEqual([plan, apply]);
    }
    expect(readOnlyCapabilities(admin)).toEqual({
      ...admin,
      message_send: false,
      task_declare: false,
      task_update: false,
      setup_plan: false,
      setup_apply: false,
    });
  });

  it.each(["viewer", "operator", "admin"] as const)("accepts and freezes a strict %s", (role) => {
    const parsed = parseDashboardAccess(descriptor({ role }));
    expect(parsed).toEqual({ ...descriptor({ role }), capabilities: OPERATOR });
    expect(Object.isFrozen(parsed)).toBe(true);
    expect(Object.isFrozen(parsed?.capabilities)).toBe(true);
  });

  it.each([
    null,
    [],
    { ...descriptor(), extra: true },
    { ...descriptor(), version: 2 },
    { ...descriptor(), principal: "" },
    { ...descriptor(), principal: "bad principal" },
    { ...descriptor(), principal: "a".repeat(65) },
    { ...descriptor(), role: "root" },
    { ...descriptor(), operator_armed: "yes" },
    { ...descriptor(), trust_boundary: null },
    { ...descriptor(), capabilities: null },
    { ...descriptor(), capabilities: [] },
    { ...descriptor(), capabilities: { ...OPERATOR, root: true } },
    { ...descriptor(), capabilities: { ...OPERATOR, read: "yes" } },
    { ...descriptor(), capabilities: { ...OPERATOR, message_send: 1 } },
    { ...descriptor(), capabilities: { ...OPERATOR, task_declare: null } },
    { ...descriptor(), capabilities: { ...OPERATOR, task_update: undefined } },
    { ...descriptor(), capabilities: { ...OPERATOR, setup_plan: "no" } },
    { ...descriptor(), capabilities: { ...OPERATOR, setup_apply: 0 } },
    {
      ...descriptor(),
      capabilities: { read: true, message_send: true, task_declare: true, task_update: true },
    },
  ])("rejects malformed or expanded input %#", (value) => {
    expect(parseDashboardAccess(value)).toBeNull();
  });
});

describe("fetchDashboardAccess", () => {
  it("uses the no-store access route and returns a ready descriptor", async () => {
    const fetcher = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) =>
        new Response(JSON.stringify(descriptor())),
    );
    const access = await fetchDashboardAccess(fetcher);
    expect(fetcher).toHaveBeenCalledWith(DASHBOARD_ACCESS_URL, { cache: "no-store" });
    expect(access.phase).toBe("ready");
    expect(capabilitiesOf(access)).toEqual(OPERATOR);
  });

  it("fails closed on HTTP, malformed JSON documents, and transport errors", async () => {
    const http = vi.fn(async () => new Response("no", { status: 503 }));
    const malformed = vi.fn(async () => new Response(JSON.stringify({ role: "operator" })));
    const invalidJson = vi.fn(async () => new Response("not-json"));
    const network = vi.fn(async () => {
      throw new Error("offline");
    });
    for (const fetcher of [http, malformed, invalidJson, network]) {
      expect(await fetchDashboardAccess(fetcher)).toBe(UNAVAILABLE_DASHBOARD_ACCESS);
    }
  });
});

it("returns no capabilities until a descriptor is ready", () => {
  expect(capabilitiesOf(LOADING_DASHBOARD_ACCESS)).toBe(NO_DASHBOARD_CAPABILITIES);
  expect(capabilitiesOf(UNAVAILABLE_DASHBOARD_ACCESS)).toBe(NO_DASHBOARD_CAPABILITIES);
});

it("detects each write removal but ignores read loss, grants, and stable access", () => {
  expect(lostWriteCapability(OPERATOR, VIEWER)).toBe(true);
  expect(lostWriteCapability(OPERATOR, { ...OPERATOR, message_send: false })).toBe(true);
  expect(lostWriteCapability(OPERATOR, { ...OPERATOR, task_declare: false })).toBe(true);
  expect(lostWriteCapability(OPERATOR, { ...OPERATOR, task_update: false })).toBe(true);
  expect(lostWriteCapability(VIEWER, OPERATOR)).toBe(false);
  expect(lostWriteCapability(VIEWER, { ...VIEWER, read: false })).toBe(false);
  expect(lostWriteCapability(OPERATOR, OPERATOR)).toBe(false);
});

it("takes every write away for a peer scope and keeps the read", () => {
  expect(readOnlyCapabilities(OPERATOR)).toEqual(VIEWER);
  expect(readOnlyCapabilities(VIEWER)).toEqual(VIEWER);
  expect(readOnlyCapabilities({ ...VIEWER, read: false }).read).toBe(false);
});
