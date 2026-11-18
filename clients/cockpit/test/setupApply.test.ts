// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — the setup writes and receipt reads: what is sent, what is read, what is said

import { afterEach, describe, expect, it, vi } from "vitest";

import {
  applySetupPlan,
  newRequestId,
  parseIssuedPlan,
  parseSetupReceipt,
  readBackReceipt,
  readBackVerdict,
  requestSetupPlan,
  restartedProcesses,
  SETUP_APPLY_SOURCE,
  SETUP_APPLY_URL,
  SETUP_PLAN_URL,
  SETUP_PREFLIGHT_URL,
  SETUP_RECEIPTS_URL,
  type IssuedSetupPlan,
  type SetupReceipt,
} from "../src/lib/setupApply";

const PLAN_ID = "plan_0123456789abcdefghijkl";
const NONCE = "nonce_0123456789abcdefghijk";
const DIGEST = "a".repeat(64);
const RECEIPT_DIGEST = "b".repeat(64);
const REQUEST_ID = "7f1d3c3e-8a51-4c43-9a55-3f1f8a2a4b10";

const HUB_EFFECT = {
  id: "establish_local_loopback_hub",
  disposition: "planned",
  authority: "operator_restart_authority",
  disruption: "service_start",
  reversible: true,
  process_id: 4242,
};
const WAITER_EFFECT = { ...HUB_EFFECT, id: "establish_identity_waiter", authority: "operator_confirmation", process_id: null };

const PLAN_DOCUMENT = {
  version: 1,
  plan_id: PLAN_ID,
  plan_digest: DIGEST,
  confirmation_nonce: NONCE,
  request_id: REQUEST_ID,
  profile: "local-single-user",
  profile_version: 1,
  configuration_generation: "c".repeat(64),
  cli_plan_digest: "d".repeat(64),
  expires_at: 1_800_000_300,
  effects: [HUB_EFFECT, WAITER_EFFECT],
  mutates_local_state: true,
};

const PLAN: IssuedSetupPlan = {
  planId: PLAN_ID,
  planDigest: DIGEST,
  confirmationNonce: NONCE,
  requestId: REQUEST_ID,
  profile: "local-single-user",
  expiresAt: 1_800_000_300,
  effects: [
    { id: "establish_local_loopback_hub", disposition: "planned", disruption: "service_start", reversible: true, processId: 4242 },
    { id: "establish_identity_waiter", disposition: "planned", disruption: "service_start", reversible: true, processId: null },
  ],
};

const EFFECT_RECEIPT = { kind: "establish_local_loopback_hub", target: "synapse-hub.service", before: "unsatisfied", after: "satisfied" };

const RECEIPT_DOCUMENT = {
  version: 1,
  sequence: 4,
  request_id: REQUEST_ID,
  plan_id: PLAN_ID,
  plan_digest: DIGEST,
  principal_id: "admin",
  capability: "setup_apply",
  outcome: "applied",
  reason: "none",
  effects: [EFFECT_RECEIPT],
  receipt_digest: RECEIPT_DIGEST,
};

const RECEIPT: SetupReceipt = {
  sequence: 4,
  requestId: REQUEST_ID,
  planId: PLAN_ID,
  capability: "setup_apply",
  outcome: "applied",
  reason: "none",
  effects: [EFFECT_RECEIPT],
  receiptDigest: RECEIPT_DIGEST,
};

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function plain(status: number, body: string): Response {
  return new Response(body, { status, headers: { "Content-Type": "text/plain" } });
}

type Answer = (url: string, init?: RequestInit) => Response;

function fetcherOf(answer: Answer) {
  return vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => answer(String(url), init));
}

function sentBody(call: unknown[] | undefined): unknown {
  return JSON.parse(String((call?.[1] as RequestInit | undefined)?.body));
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("parseIssuedPlan", () => {
  it("reads a plan whole: its confirmation, its profile, its expiry and each effect", () => {
    expect(parseIssuedPlan(PLAN_DOCUMENT)).toEqual(PLAN);
  });

  it.each([
    ["no version", { ...PLAN_DOCUMENT, version: 2 }],
    ["no mutation flag", { ...PLAN_DOCUMENT, mutates_local_state: false }],
    ["a short plan id", { ...PLAN_DOCUMENT, plan_id: "short" }],
    ["an upper-case digest", { ...PLAN_DOCUMENT, plan_digest: "A".repeat(64) }],
    ["no digest", { ...PLAN_DOCUMENT, plan_digest: null }],
    ["a nonce with a space", { ...PLAN_DOCUMENT, confirmation_nonce: "nonce with space 0123456789" }],
    ["no nonce", { ...PLAN_DOCUMENT, confirmation_nonce: 7 }],
    ["no request id", { ...PLAN_DOCUMENT, request_id: "" }],
    ["no profile", { ...PLAN_DOCUMENT, profile: null }],
    ["no expiry", { ...PLAN_DOCUMENT, expires_at: 0 }],
    ["no effect list", { ...PLAN_DOCUMENT, effects: "all" }],
    ["one unreadable effect", { ...PLAN_DOCUMENT, effects: [HUB_EFFECT, { ...WAITER_EFFECT, id: 7 }] }],
    ["an effect that is not an object", { ...PLAN_DOCUMENT, effects: [null] }],
    ["an effect without a disposition", { ...PLAN_DOCUMENT, effects: [{ ...HUB_EFFECT, disposition: "" }] }],
    ["an effect without a disruption", { ...PLAN_DOCUMENT, effects: [{ ...HUB_EFFECT, disruption: 3 }] }],
    ["an effect without reversibility", { ...PLAN_DOCUMENT, effects: [{ ...HUB_EFFECT, reversible: "yes" }] }],
    ["a process id of one", { ...PLAN_DOCUMENT, effects: [{ ...HUB_EFFECT, process_id: 1 }] }],
    ["a process id that is text", { ...PLAN_DOCUMENT, effects: [{ ...HUB_EFFECT, process_id: "4242" }] }],
    ["no process id field", { ...PLAN_DOCUMENT, effects: [{ ...HUB_EFFECT, process_id: undefined }] }],
  ])("refuses a plan with %s", (_label, raw) => {
    expect(parseIssuedPlan(raw)).toBeNull();
  });

  it("refuses anything that is not a plan document", () => {
    for (const raw of [null, [], "plan", 7]) expect(parseIssuedPlan(raw)).toBeNull();
  });
});

describe("parseSetupReceipt", () => {
  it("reads a receipt's sequence, request, plan, outcome, reason, effects and digest", () => {
    expect(parseSetupReceipt(RECEIPT_DOCUMENT)).toEqual(RECEIPT);
  });

  it("reads a refusal before any plan, which names none", () => {
    const refused = { ...RECEIPT_DOCUMENT, plan_id: null, outcome: "denied", reason: "capability_denied", effects: [] };
    expect(parseSetupReceipt(refused)).toEqual({
      ...RECEIPT,
      planId: null,
      outcome: "denied",
      reason: "capability_denied",
      effects: [],
    });
  });

  it.each([
    ["no sequence", { ...RECEIPT_DOCUMENT, sequence: 0 }],
    ["no request id", { ...RECEIPT_DOCUMENT, request_id: null }],
    ["a plan id that is a number", { ...RECEIPT_DOCUMENT, plan_id: 9 }],
    ["no capability", { ...RECEIPT_DOCUMENT, capability: "" }],
    ["no outcome", { ...RECEIPT_DOCUMENT, outcome: undefined }],
    ["no reason", { ...RECEIPT_DOCUMENT, reason: 1 }],
    ["a short digest", { ...RECEIPT_DOCUMENT, receipt_digest: "b" }],
    ["no digest", { ...RECEIPT_DOCUMENT, receipt_digest: undefined }],
    ["an unreadable effect", { ...RECEIPT_DOCUMENT, effects: [{ ...EFFECT_RECEIPT, after: null }] }],
    ["an effect without a target", { ...RECEIPT_DOCUMENT, effects: [{ ...EFFECT_RECEIPT, target: "" }] }],
    ["an effect without a kind", { ...RECEIPT_DOCUMENT, effects: [{ ...EFFECT_RECEIPT, kind: 1 }] }],
    ["an effect without a before", { ...RECEIPT_DOCUMENT, effects: [{ ...EFFECT_RECEIPT, before: undefined }] }],
    ["an effect list that is not a list", { ...RECEIPT_DOCUMENT, effects: {} }],
  ])("refuses a receipt with %s", (_label, raw) => {
    expect(parseSetupReceipt(raw)).toBeNull();
  });

  it("refuses anything that is not a receipt", () => {
    for (const raw of [null, [], "receipt"]) expect(parseSetupReceipt(raw)).toBeNull();
  });
});

describe("readBackVerdict", () => {
  it("says applied only for an applied receipt whose every effect is satisfied after", () => {
    expect(readBackVerdict(RECEIPT)).toBe("applied");
    const unverified = { ...RECEIPT, effects: [EFFECT_RECEIPT, { ...EFFECT_RECEIPT, after: "unverifiable" }] };
    expect(readBackVerdict(unverified)).toBe("unknown");
    expect(readBackVerdict({ ...RECEIPT, effects: [] })).toBe("unknown");
  });

  it("says unchanged when the host already held the profile", () => {
    expect(readBackVerdict({ ...RECEIPT, outcome: "already_satisfied", effects: [] })).toBe("unchanged");
  });

  it.each(["refused", "denied", "rate_limited", "expired", "drifted"])("says refused for %s", (outcome) => {
    expect(readBackVerdict({ ...RECEIPT, outcome, reason: "plan_replayed" })).toBe("refused");
  });

  it.each(["partial", "authorised", "planned"])("says unknown for %s", (outcome) => {
    expect(readBackVerdict({ ...RECEIPT, outcome })).toBe("unknown");
  });

  it("says unknown when no receipt was read back", () => {
    expect(readBackVerdict(null)).toBe("unknown");
  });
});

describe("restartedProcesses", () => {
  it("names the process of each hub the plan restarts", () => {
    expect(restartedProcesses(PLAN)).toEqual([4242]);
    expect(restartedProcesses({ ...PLAN, effects: [] })).toEqual([]);
  });
});

describe("newRequestId", () => {
  it("makes a fresh version-4 request id", () => {
    const first = newRequestId();
    expect(first).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/u);
    expect(newRequestId()).not.toBe(first);
  });
});

describe("requestSetupPlan", () => {
  it("plans the profile the preflight names, under a fresh request id", async () => {
    const fetcher = fetcherOf((url) =>
      url === SETUP_PREFLIGHT_URL ? json(200, { profile: "local-single-user" }) : json(200, PLAN_DOCUMENT),
    );

    expect(await requestSetupPlan(fetcher, () => REQUEST_ID)).toEqual({ kind: "ok", value: PLAN });
    expect(fetcher.mock.calls.map((call) => String(call[0]))).toEqual([SETUP_PREFLIGHT_URL, SETUP_PLAN_URL]);
    const init = fetcher.mock.calls[1]?.[1];
    expect(init?.method).toBe("POST");
    expect(init?.headers).toEqual({ "Content-Type": "application/json" });
    expect(sentBody(fetcher.mock.calls[1])).toEqual({ version: 1, profile: "local-single-user", request_id: REQUEST_ID });
    expect(fetcher.mock.calls[0]?.[1]).toEqual({ cache: "no-store" });
  });

  it("gives the dashboard's own words when the preflight or the plan is refused", async () => {
    const unserved = fetcherOf(() => plain(404, "setup not served: unarmed"));
    const full = fetcherOf((url) =>
      url === SETUP_PREFLIGHT_URL ? json(200, { profile: "local-single-user" }) : plain(503, "setup plan refused: plan_store_full\n"),
    );

    expect(await requestSetupPlan(unserved)).toEqual({ kind: "refused", status: 404, detail: "setup not served: unarmed" });
    expect(await requestSetupPlan(full)).toEqual({
      kind: "refused",
      status: 503,
      detail: "setup plan refused: plan_store_full",
    });
  });

  it("does not repeat a body that is empty, markup or too long", async () => {
    for (const body of ["", "<html>proxy</html>", "x".repeat(301)]) {
      const fetcher = fetcherOf(() => plain(502, body));
      expect(await requestSetupPlan(fetcher)).toEqual({ kind: "refused", status: 502, detail: "dashboard returned 502" });
    }
  });

  it("fails when the preflight names no profile or the answer is not a plan", async () => {
    const nameless = fetcherOf(() => json(200, { profile: 7 }));
    const notPlan = fetcherOf((url) =>
      url === SETUP_PREFLIGHT_URL ? json(200, { profile: "local-single-user" }) : json(200, { plan: "none" }),
    );

    expect(await requestSetupPlan(nameless)).toEqual({ kind: "failed", detail: "the preflight named no profile" });
    expect(await requestSetupPlan(notPlan)).toEqual({ kind: "failed", detail: "the answer was not a plan" });
  });

  it("says why a request threw", async () => {
    const offline = vi.fn(async () => {
      throw new Error("offline");
    }) as unknown as typeof fetch;
    const odd = vi.fn(async () => {
      throw "odd";
    }) as unknown as typeof fetch;

    expect(await requestSetupPlan(offline)).toEqual({ kind: "failed", detail: "offline" });
    expect(await requestSetupPlan(odd)).toEqual({ kind: "failed", detail: "odd" });
  });
});

describe("applySetupPlan", () => {
  it("confirms the plan with its own request id, digest and nonce, and reads the receipt", async () => {
    const fetcher = fetcherOf(() => json(200, RECEIPT_DOCUMENT));

    expect(await applySetupPlan(PLAN, fetcher)).toEqual({ kind: "ok", value: RECEIPT });
    expect(String(fetcher.mock.calls[0]?.[0])).toBe(SETUP_APPLY_URL);
    expect(fetcher.mock.calls[0]?.[1]?.method).toBe("POST");
    expect(sentBody(fetcher.mock.calls[0])).toEqual({
      version: 1,
      request_id: REQUEST_ID,
      plan_id: PLAN_ID,
      plan_digest: DIGEST,
      confirmation_nonce: NONCE,
      confirm: true,
    });
  });

  it("gives a refusal in the dashboard's own words", async () => {
    const replayed = fetcherOf(() => plain(409, "setup apply refused: plan_replayed"));

    expect(await applySetupPlan(PLAN, replayed)).toEqual({
      kind: "refused",
      status: 409,
      detail: "setup apply refused: plan_replayed",
    });
  });

  it("fails on an answer that is not a receipt, or no answer", async () => {
    const odd = fetcherOf(() => json(200, { receipt: "maybe" }));
    const offline = vi.fn(async () => {
      throw new Error("offline");
    }) as unknown as typeof fetch;

    expect(await applySetupPlan(PLAN, odd)).toEqual({ kind: "failed", detail: "the answer was not a receipt" });
    expect(await applySetupPlan(PLAN, offline)).toEqual({ kind: "failed", detail: "offline" });
  });
});

describe("readBackReceipt", () => {
  it("reads an answered receipt back by its sequence, and keeps it only when the digest matches", async () => {
    const same = fetcherOf(() => json(200, { receipt: RECEIPT_DOCUMENT }));
    const other = fetcherOf(() => json(200, { receipt: { ...RECEIPT_DOCUMENT, receipt_digest: "e".repeat(64) } }));
    const missing = fetcherOf(() => plain(404, "no setup receipt 4"));

    expect(await readBackReceipt(PLAN, RECEIPT, same)).toEqual(RECEIPT);
    expect(String(same.mock.calls[0]?.[0])).toBe(`${SETUP_RECEIPTS_URL}?sequence=4`);
    expect(same.mock.calls[0]?.[1]).toEqual({ cache: "no-store" });
    expect(await readBackReceipt(PLAN, RECEIPT, other)).toBeNull();
    expect(await readBackReceipt(PLAN, RECEIPT, missing)).toBeNull();
  });

  it("finds the newest apply receipt of the plan's request when the apply gave none", async () => {
    const replayed = { ...RECEIPT_DOCUMENT, sequence: 6, outcome: "refused", reason: "plan_replayed", effects: [] };
    const planned = { ...RECEIPT_DOCUMENT, sequence: 3, capability: "setup_plan", outcome: "planned" };
    const stranger = { ...RECEIPT_DOCUMENT, sequence: 7, request_id: "another-request" };
    const fetcher = fetcherOf(() => json(200, { receipts: [stranger, "unreadable", replayed, RECEIPT_DOCUMENT, planned], count: 5 }));

    const found = await readBackReceipt(PLAN, null, fetcher);

    expect(String(fetcher.mock.calls[0]?.[0])).toBe(SETUP_RECEIPTS_URL);
    expect(found?.sequence).toBe(6);
    expect(found?.reason).toBe("plan_replayed");
  });

  it("reads nothing back from a chain that does not show the request, or cannot be read", async () => {
    const onlyPlanned = fetcherOf(() => json(200, { receipts: [{ ...RECEIPT_DOCUMENT, capability: "setup_plan" }] }));
    const noList = fetcherOf(() => json(200, { receipts: "none" }));
    const unavailable = fetcherOf(() => plain(503, "setup receipt chain unavailable"));
    const offline = vi.fn(async () => {
      throw new Error("offline");
    }) as unknown as typeof fetch;

    expect(await readBackReceipt(PLAN, null, onlyPlanned)).toBeNull();
    expect(await readBackReceipt(PLAN, null, noList)).toBeNull();
    expect(await readBackReceipt(PLAN, null, unavailable)).toBeNull();
    expect(await readBackReceipt(PLAN, RECEIPT, offline)).toBeNull();
  });
});

describe("SETUP_APPLY_SOURCE", () => {
  it("asks with the cockpit's authenticated fetch", async () => {
    const fetcher = vi.fn(async (url: RequestInfo | URL) => {
      const path = String(url);
      if (path === SETUP_PREFLIGHT_URL) return json(200, { profile: "local-single-user" });
      if (path === SETUP_PLAN_URL) return json(200, PLAN_DOCUMENT);
      if (path === SETUP_APPLY_URL) return json(200, RECEIPT_DOCUMENT);
      return json(200, { receipt: RECEIPT_DOCUMENT });
    });
    vi.stubGlobal("fetch", fetcher);

    const planned = await SETUP_APPLY_SOURCE.plan();
    const applied = await SETUP_APPLY_SOURCE.apply(PLAN);
    const readBack = await SETUP_APPLY_SOURCE.readBack(PLAN, RECEIPT);

    expect(planned.kind).toBe("ok");
    expect(applied).toEqual({ kind: "ok", value: RECEIPT });
    expect(readBack).toEqual(RECEIPT);
    expect(fetcher.mock.calls.map((call) => String(call[0]))).toEqual([
      SETUP_PREFLIGHT_URL,
      SETUP_PLAN_URL,
      SETUP_APPLY_URL,
      `${SETUP_RECEIPTS_URL}?sequence=4`,
    ]);
  });
});
