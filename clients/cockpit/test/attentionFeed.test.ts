// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE CHANNEL — authenticated attention feed contract tests

import { describe, expect, it, vi } from "vitest";

import { createAttentionStore, parseAttentionReport } from "../src/lib/attentionFeed";

describe("parseAttentionReport", () => {
  it("keeps observer absence distinct and drops unrendered private data", () => {
    const report = parseAttentionReport({
      version: 1,
      state: "missing_observer",
      remaining: 0,
      snoozed_count: 0,
      account_label: "Secret account",
      alerts: [{
        key: "approval:TASK-1",
        kind: "approval",
        subject: "TASK-1",
        severity: "critical",
        state: "expired",
        action: "Review the pending approval",
        observed_at: 100,
        expires_at: 200,
        task_body: "Secret task body",
      }],
    });
    expect(report?.state).toBe("missing_observer");
    expect(report?.alerts[0]?.state).toBe("expired");
    expect(JSON.stringify(report)).not.toContain("Secret");
  });

  it("refuses unknown alert states, nonfinite timestamps and oversized pages", () => {
    const base = {
      version: 1,
      state: "active",
      remaining: 0,
      snoozed_count: 0,
      alerts: [{
        key: "delivery:1",
        kind: "failed_delivery",
        subject: "1",
        severity: "critical",
        state: "open",
        action: "Inspect receipt",
        observed_at: 100,
        expires_at: null,
      }],
    };
    expect(parseAttentionReport({ ...base, alerts: [{ ...base.alerts[0], state: "approved" }] })).toBeNull();
    expect(parseAttentionReport({ ...base, alerts: [{ ...base.alerts[0], observed_at: Infinity }] })).toBeNull();
    expect(parseAttentionReport({ ...base, alerts: Array(201).fill(base.alerts[0]) })).toBeNull();
    expect(parseAttentionReport(null)).toBeNull();
    expect(parseAttentionReport({ ...base, version: 2 })).toBeNull();
    expect(parseAttentionReport({ ...base, alerts: null })).toBeNull();
    expect(parseAttentionReport({ ...base, state: "unknown" })).toBeNull();
    for (const patch of [
      { remaining: 1.5 }, { snoozed_count: 1.5 },
      { remaining: -1 }, { snoozed_count: -1 },
    ]) expect(parseAttentionReport({ ...base, ...patch })).toBeNull();
    expect(parseAttentionReport({ ...base, alerts: [null] })).toBeNull();
    for (const patch of [
      { key: "" }, { key: "x".repeat(257) },
      { kind: "unknown" }, { subject: 1 }, { subject: "x".repeat(257) },
      { action: 1 }, { action: "x".repeat(257) },
      { severity: "unknown" }, { state: "unknown" },
      { observed_at: "now" }, { observed_at: Infinity },
      { expires_at: "later" }, { expires_at: Infinity },
    ]) expect(parseAttentionReport({ ...base, alerts: [{ ...base.alerts[0], ...patch }] })).toBeNull();
    expect(parseAttentionReport({ ...base, state: "quiet", alerts: [
      { ...base.alerts[0], kind: "stale_data", severity: "warning" },
      { ...base.alerts[0], kind: "quota_reset", severity: "info" },
    ] })?.alerts).toHaveLength(2);
  });

  it("polls the authenticated attention endpoint and publishes the parsed report", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify({
      version: 1, state: "quiet", remaining: 0, snoozed_count: 0, alerts: [],
    })));
    const feed = createAttentionStore({ url: "/owner/attention.json", pollMs: 1000, fetcher });
    const states: string[] = [];
    const unsubscribe = feed.subscribe((state) => states.push(state.status));
    await vi.waitFor(() => expect(states.at(-1)).toBe("live"));
    expect(fetcher.mock.calls[0]?.[0]).toBe("/owner/attention.json");
    unsubscribe();
    feed.stop();
  });
});
