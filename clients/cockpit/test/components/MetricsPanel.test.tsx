// @vitest-environment jsdom
// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — metrics panel behaviour tests

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { MetricsPanel } from "../../src/components/MetricsPanel";
import type { LogMetrics, MetricsState } from "../../src/lib/metrics";
import type { SessionsReport, SessionsState } from "../../src/lib/sessions";

afterEach(cleanup);

const METRICS: LogMetrics = {
  source: "store",
  log: { totalEvents: 1234, maxSeq: 1300, firstTs: 1_751_700_000, lastTs: 1_751_800_000 },
  eventsByKind: { chat: 900, claim: 200, release: 134 },
  windows: { last_hour: { events: 42, byKind: { chat: 40, claim: 2 } } },
  note: "counts come from the durable store",
};

function metricsState(data: LogMetrics | null, status: MetricsState["status"], error: string | null = null): MetricsState {
  return { data, status, fetchedAt: data === null ? null : 1, error };
}

function sessionsState(data: SessionsReport | null, status: SessionsState["status"], error: string | null = null): SessionsState {
  return { data, status, fetchedAt: data === null ? null : 1, error };
}

function sessionRow(agent: string, seq: number, costUsd: number | null) {
  return {
    agent,
    sessionId: `s-${seq}`,
    taskId: seq % 2 === 0 ? "t-1" : "",
    turns: 7,
    errors: 0,
    abstentions: 1,
    inputTokens: 100,
    outputTokens: 50,
    totalTokens: 150,
    costUsd,
    seq,
    ts: 1_751_800_000,
  };
}

describe("MetricsPanel", () => {
  it("states absence, failure, and waiting as distinct facts", () => {
    render(<MetricsPanel state={metricsState(null, "absent")} />);
    expect(screen.getByText(/does not serve log metrics yet/)).toBeTruthy();
    cleanup();
    render(<MetricsPanel state={metricsState(null, "error", "boom")} />);
    expect(screen.getByText("Metrics feed failed: boom")).toBeTruthy();
    cleanup();
    render(<MetricsPanel state={metricsState(null, "connecting")} />);
    expect(screen.getByText("Waiting for the hub.")).toBeTruthy();
  });

  it("draws coverage, window bars, the whole-log block, and the note verbatim", () => {
    render(<MetricsPanel state={metricsState(METRICS, "live")} />);
    expect(screen.getByText("1234 events · seq 1300")).toBeTruthy();
    expect(screen.getByText("last hour · 42")).toBeTruthy();
    expect(screen.getByText("whole log")).toBeTruthy();
    expect(screen.getByText("counts come from the durable store")).toBeTruthy();
    // Whole-log bars: chat leads, so its fill is the widest; each kind renders once per block.
    expect(screen.getAllByText("chat")).toHaveLength(2);
    expect(screen.getByText("900")).toBeTruthy();
  });

  it("keeps the sessions block honest across absent, empty, and served states", () => {
    render(<MetricsPanel state={metricsState(METRICS, "live")} sessions={sessionsState(null, "absent")} />);
    expect(screen.getByText(/Session telemetry not served/)).toBeTruthy();
    cleanup();
    render(
      <MetricsPanel
        state={metricsState(METRICS, "live")}
        sessions={sessionsState(
          { generatedFromSeq: 1, asOf: null, totals: {}, sessions: [], note: "" },
          "live",
        )}
      />,
    );
    expect(screen.getByText("No session metrics recorded yet.")).toBeTruthy();
    cleanup();
    const rows = Array.from({ length: 22 }, (_, index) => sessionRow(`agent-${index}`, index, index === 0 ? 1.2345 : null));
    render(
      <MetricsPanel
        state={metricsState(METRICS, "live")}
        sessions={sessionsState(
          { generatedFromSeq: 9, asOf: 1, totals: {}, sessions: rows, note: "estimates only" },
          "live",
        )}
      />,
    );
    expect(screen.getByText("$1.2345")).toBeTruthy();
    expect(screen.getByText("agent-0")).toBeTruthy();
    expect(screen.getByText("+2 more sessions in the feed")).toBeTruthy();
    expect(screen.getByText("estimates only")).toBeTruthy();
  });
});
