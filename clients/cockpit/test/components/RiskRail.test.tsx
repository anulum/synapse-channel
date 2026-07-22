// @vitest-environment jsdom
// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — risk rail behaviour tests

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

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
      orphaned: [{ taskId: "t-orphan", owner: "a", detail: "claim is the last word", seq: 4 }],
      dangling: [{ taskId: "t-dangling", owner: "", detail: "depends on t-ghost", seq: null }],
      stale: [{ taskId: "t-stale", owner: "b", detail: "silent 3 days", seq: 9 }],
      anomalyCount: 3,
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
