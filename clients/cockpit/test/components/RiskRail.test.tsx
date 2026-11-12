// @vitest-environment jsdom
// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — risk rail behaviour tests

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { RiskRail } from "../../src/components/RiskRail";
import type { HealthAnomalies } from "../../src/lib/healthAnomalies";
import type { WaitsReport } from "../../src/lib/waits";
import type { RiskView } from "../../src/types";

afterEach(cleanup);

const QUIET_RISK: RiskView = { level: "green", signals: [], safe_next_work: [] };

function feedOf<T>(data: T): { data: T; status: "live"; fetchedAt: number; error: null } {
  return { data, status: "live", fetchedAt: 1, error: null };
}

describe("RiskRail", () => {
  it("waits for the hub before claiming anything", () => {
    render(<RiskRail risk={null} />);
    expect(screen.getByText("Waiting for the hub.")).toBeTruthy();
  });

  it("orders the hub's signals with glyphed severities and the verdict chip", () => {
    render(
      <RiskRail
        risk={{
          level: "red",
          signals: [
            { level: "red", category: "conflict", subject: "path a/b", detail: "two owners" },
            { level: "amber", category: "stale", subject: "t-2", detail: "" },
          ],
          safe_next_work: ["t-9", "t-10"],
        }}
      />,
    );
    expect(screen.getByText("▲ red")).toBeTruthy();
    expect(screen.getByText("path a/b")).toBeTruthy();
    expect(screen.getByText("two owners")).toBeTruthy();
    expect(screen.getByText("Safe next work")).toBeTruthy();
    expect(screen.getByText("t-9")).toBeTruthy();
  });

  it("collapses the safe-work tail into a stated count", () => {
    render(
      <RiskRail
        risk={{
          level: "green",
          signals: [],
          safe_next_work: Array.from({ length: 17 }, (_, index) => `task-${index}`),
        }}
      />,
    );
    expect(screen.getByText("+3 more")).toBeTruthy();
  });

  it("keeps every optional section silent when nothing is there to say", () => {
    render(<RiskRail risk={QUIET_RISK} />);
    expect(screen.getByText("No risk signals recorded.")).toBeTruthy();
    expect(document.querySelectorAll(".risk-heuristics")).toHaveLength(0);
  });

  it("names dead letters, hub anomalies, pending gates, approvals, and heuristics in their own sections", () => {
    const anomalies: HealthAnomalies = {
      present: true,
      orphaned: [
        { taskId: "t-orphan", owner: "a", detail: "claim is the last word", seq: 4, dependsOn: [], recorded: null },
      ],
      dangling: [
        { taskId: "t-dangling", owner: "", detail: "depends on t-ghost", seq: null, dependsOn: ["t-ghost"], recorded: null },
      ],
      stale: [{ taskId: "t-stale", owner: "b", detail: "silent 3 days", seq: 9, dependsOn: [], recorded: null }],
      anomalyCount: 3,
      fleetHealth: {
        policyVersion: 1,
        level: "amber",
        generatedAt: 100,
        firstRetainedSeq: 2,
        generatedFromSeq: 40,
        retainedEvents: 39,
        contentionPairs: 1,
        expiredClaims: 0,
        deadLetteredMessages: 2,
        recoveredMessages: 1,
        deadLetterEscalations: 0,
        retention: "current retained log",
      },
    };
    const waits: WaitsReport = {
      present: true,
      waits: [
        { taskId: "t-wait", title: "wired", who: "quantum/claude", onWhat: ["t-dep"], since: 1, status: "declared" },
      ],
      waitCount: 1,
      logEndSeq: 10,
      note: "",
    };
    render(
      <RiskRail
        risk={QUIET_RISK}
        deadLetters={[{ target: "ghost/agent", count: 4, lastSender: "CEO", lastTs: 1_751_800_000 }]}
        anomalyReport={feedOf(anomalies)}
        waits={feedOf(waits)}
        approvals={[{ action: "task_update", namespace: "quantum", taskId: "t-1", requester: "op-a" }]}
        anomalies={[
          { taskId: "t-churn", kind: "claim_churn", count: 5, lastTs: 2, detail: "claimed 5 times" },
          { taskId: "t-lease", kind: "lease_repeat", count: 3, lastTs: 3, detail: "renewed 3 times" },
        ]}
      />,
    );
    expect(screen.getByText("dead letters · nobody listening")).toBeTruthy();
    expect(screen.getByText("4 unread")).toBeTruthy();
    expect(screen.getByText(/last from CEO/).textContent).toContain("last from CEO at");
    expect(screen.getByText("hub health anomalies · 3")).toBeTruthy();
    expect(screen.getByText("local fleet health · amber")).toBeTruthy();
    expect(screen.getByText("1 retained pair(s)")).toBeTruthy();
    expect(
      screen.getByText(/0 expired claim\(s\).*2 receipt-dead-lettered.*1 receipt-recovered/),
    ).toBeTruthy();
    expect(screen.getByText(/retained seq 2–40.*39 event\(s\).*counts only/)).toBeTruthy();
    expect(screen.getByText("orphaned")).toBeTruthy();
    expect(screen.getByText("dangling")).toBeTruthy();
    expect(screen.getByText("stale")).toBeTruthy();
    expect(screen.getByText("pending gates · 1")).toBeTruthy();
    expect(screen.getByText("waits on t-dep")).toBeTruthy();
    expect(screen.getByText("pending approvals · awaiting a second operator")).toBeTruthy();
    expect(screen.getByText("in quantum · requested by op-a")).toBeTruthy();
    expect(screen.getByText("repetition heuristics · observed window")).toBeTruthy();
    expect(screen.getByText("churn")).toBeTruthy();
    expect(screen.getByText("lease")).toBeTruthy();
  });

  it("gives a task with two unfinished prerequisites two rows that React can tell apart", () => {
    const errors: string[] = [];
    const spy = vi.spyOn(console, "error").mockImplementation((...parts: unknown[]) => {
      errors.push(parts.map(String).join(" "));
    });
    const anomalies: HealthAnomalies = {
      present: true,
      orphaned: [],
      dangling: [
        { taskId: "t-notes", owner: "", detail: "waits on unfinished t-upload", seq: 7, dependsOn: ["t-upload"], recorded: true },
        { taskId: "t-notes", owner: "", detail: "waits on unfinished t-audit", seq: 7, dependsOn: ["t-audit"], recorded: true },
      ],
      stale: [],
      anomalyCount: 2,
      fleetHealth: null,
    };
    try {
      render(<RiskRail risk={QUIET_RISK} anomalyReport={feedOf(anomalies)} />);
    } finally {
      spy.mockRestore();
    }

    expect(screen.getByText("waits on unfinished t-upload")).toBeTruthy();
    expect(screen.getByText("waits on unfinished t-audit")).toBeTruthy();
    expect(errors.filter((message) => message.includes("same key"))).toEqual([]);
  });

  it("shows an unfinished prerequisite once: under the gates, not among the anomalies", () => {
    const anomalies: HealthAnomalies = {
      present: true,
      orphaned: [
        { taskId: "t-orphan", owner: "a", detail: "claim is the last word", seq: 4, dependsOn: [], recorded: null },
      ],
      dangling: [
        { taskId: "t-notes", owner: "", detail: "waits on unfinished t-upload", seq: 7, dependsOn: ["t-upload"], recorded: true },
        { taskId: "t-notes", owner: "", detail: "depends on t-ghost, not in the log", seq: 7, dependsOn: ["t-ghost"], recorded: false },
        { taskId: "t-shipped", owner: "", detail: "waits on unfinished t-audit", seq: 9, dependsOn: ["t-audit"], recorded: true },
      ],
      stale: [],
      anomalyCount: 4,
      fleetHealth: null,
    };
    const waits: WaitsReport = {
      present: true,
      waits: [
        { taskId: "t-notes", title: "notes", who: "quantum/docs", onWhat: ["t-upload", "t-ghost"], since: 1, status: "open" },
      ],
      waitCount: 1,
      logEndSeq: 10,
      note: "",
    };
    render(<RiskRail risk={QUIET_RISK} anomalyReport={feedOf(anomalies)} waits={feedOf(waits)} />);

    // The gate keeps the unfinished prerequisite; the anomaly section drops it and says so.
    expect(screen.getByText("waits on t-upload, t-ghost")).toBeTruthy();
    expect(screen.queryByText("waits on unfinished t-upload")).toBeNull();
    expect(screen.getByText("hub health anomalies · 3")).toBeTruthy();
    expect(screen.getByText("1 under pending gates")).toBeTruthy();
    // A name the log does not know stays an anomaly, and so does a finished task
    // that was closed over an open prerequisite: it has no gate.
    expect(screen.getByText("depends on t-ghost, not in the log")).toBeTruthy();
    expect(screen.getByText("waits on unfinished t-audit")).toBeTruthy();
    expect(screen.getByText("claim is the last word")).toBeTruthy();
  });

  it("shows no anomaly section when every row is an unfinished prerequisite under the gates", () => {
    const anomalies: HealthAnomalies = {
      present: true,
      orphaned: [],
      dangling: [
        { taskId: "t-notes", owner: "", detail: "waits on unfinished t-upload", seq: 7, dependsOn: ["t-upload"], recorded: true },
      ],
      stale: [],
      anomalyCount: 1,
      fleetHealth: null,
    };
    const waits: WaitsReport = {
      present: true,
      waits: [{ taskId: "t-notes", title: "notes", who: "", onWhat: ["t-upload"], since: 1, status: "open" }],
      waitCount: 1,
      logEndSeq: 10,
      note: "",
    };
    const { rerender } = render(
      <RiskRail risk={QUIET_RISK} anomalyReport={feedOf(anomalies)} waits={feedOf(waits)} />,
    );

    expect(screen.getByText("pending gates · 1")).toBeTruthy();
    expect(screen.queryByText(/hub health anomalies/)).toBeNull();
    expect(screen.queryByText(/under pending gates/)).toBeNull();

    // Without the gates the row has nowhere else to be shown, so it stays an anomaly.
    rerender(<RiskRail risk={QUIET_RISK} anomalyReport={feedOf(anomalies)} />);
    expect(screen.getByText("hub health anomalies · 1")).toBeTruthy();
    expect(screen.getByText("waits on unfinished t-upload")).toBeTruthy();
    expect(screen.queryByText(/under pending gates/)).toBeNull();
  });

  it("states unrecorded approval fields as dashes and the unowned gate as unowned", () => {
    const waits: WaitsReport = {
      present: true,
      waits: Array.from({ length: 9 }, (_, index) => ({
        taskId: `t-${index}`,
        title: "",
        who: "",
        onWhat: ["t-x"],
        since: null,
        status: "declared",
      })),
      waitCount: 9,
      logEndSeq: 10,
      note: "",
    };
    render(
      <RiskRail
        risk={QUIET_RISK}
        waits={feedOf(waits)}
        approvals={[{ action: "", namespace: "", taskId: "", requester: "" }]}
        deadLetters={[{ target: "ghost", count: 1, lastSender: "", lastTs: null }]}
      />,
    );
    expect(screen.getByText("relay")).toBeTruthy();
    expect(screen.getByText("in — · requested by —")).toBeTruthy();
    expect(screen.getAllByText("unowned")).toHaveLength(8);
    expect(screen.getByText("+1 more gates")).toBeTruthy();
    expect(screen.getByText(/last from — at —/)).toBeTruthy();
  });

  it("highlights matching task evidence across risk sections", () => {
    const waits: WaitsReport = {
      present: true,
      waits: [{ taskId: "t-selected", title: "", who: "agent/a", onWhat: ["t-x"], since: 1, status: "declared" }],
      waitCount: 1,
      logEndSeq: 10,
      note: "",
    };
    render(
      <RiskRail
        risk={{
          level: "amber",
          signals: [{ level: "amber", category: "stale", subject: "t-selected", detail: "late" }],
          safe_next_work: [],
        }}
        waits={feedOf(waits)}
        anomalies={[{ taskId: "t-other", kind: "lease_repeat", count: 2, lastTs: 1, detail: "twice" }]}
        selection={{ kind: "task", id: "t-selected" }}
      />,
    );
    expect(document.querySelectorAll(".risk-row.context-match")).toHaveLength(2);
    expect(screen.getByText("t-other").closest("li")?.className).not.toContain("context-match");
  });
});
