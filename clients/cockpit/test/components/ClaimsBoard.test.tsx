// @vitest-environment jsdom
// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — claims board behaviour tests

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { ClaimsBoard } from "../../src/components/ClaimsBoard";
import type { BranchConflictView, ClaimView } from "../../src/lib/claims";

afterEach(cleanup);

function view(
  taskId: string,
  options: Partial<Omit<ClaimView, "claim">> & {
    stale?: boolean;
    paths?: string[];
    git?: { branch: string; base: string; auto_release_on: string } | null;
  } = {},
): ClaimView {
  return {
    claim: {
      task_id: taskId,
      owner: "quantum/claude",
      lease_expires_at: null,
      paths: options.paths ?? [],
      stale: options.stale ?? false,
      git: options.git ?? null,
    },
    urgency: options.urgency ?? "held",
    inConflict: options.inConflict ?? false,
    secondsToExpiry: options.secondsToExpiry ?? null,
  };
}

const CONFLICT: BranchConflictView = {
  ownerA: "quantum/claude",
  branchA: "feat-a",
  baseA: "main",
  ownerB: "quantum/codex",
  branchB: "feat-b",
  baseB: "main",
  paths: ["src/shared.py"],
  description: "two branches touch src/shared.py",
};

describe("ClaimsBoard", () => {
  it("distinguishes waiting from empty", () => {
    render(<ClaimsBoard claims={[]} conflicts={[]} connected={false} />);
    expect(screen.getByText("Waiting for the hub.")).toBeTruthy();
    cleanup();
    render(<ClaimsBoard claims={[]} conflicts={[]} connected />);
    expect(screen.getByText("No file scopes are held right now.")).toBeTruthy();
  });

  it("raises the branch-conflict banner as an alert naming both sides", () => {
    render(<ClaimsBoard claims={[]} conflicts={[CONFLICT, CONFLICT]} connected />);
    const alerts = screen.getAllByRole("alert");
    expect(alerts.length).toBeGreaterThan(0);
    expect(screen.getByText("2 branch conflicts")).toBeTruthy();
    expect(
      screen.getAllByText("quantum/claude (feat-a) vs quantum/codex (feat-b)"),
    ).toHaveLength(2);
    expect(screen.getAllByText("src/shared.py")).toHaveLength(2);
  });

  it("shows lease countdowns, stale tags, branch chips, paths, and the lens", () => {
    render(
      <ClaimsBoard
        connected
        lens="quantum/claude"
        conflicts={[]}
        claims={[
          view("t-live", {
            secondsToExpiry: 95,
            paths: ["src/a.py", "src/b.py"],
            git: { branch: "feat-a", base: "main", auto_release_on: "" },
          }),
          view("t-overdue", { urgency: "stale", stale: true, secondsToExpiry: -61 }),
          view("t-open", {}),
        ]}
      />,
    );
    expect(screen.getByText("lens: quantum/claude")).toBeTruthy();
    expect(screen.getByText("1 stale")).toBeTruthy();
    expect(screen.getByText("1:35")).toBeTruthy();
    expect(screen.getByText("-1:01").className).toContain("claim-row__lease--overdue");
    expect(screen.getByText("no lease")).toBeTruthy();
    expect(screen.getByText("feat-a → main")).toBeTruthy();
    expect(screen.getByText("stale")).toBeTruthy();
    expect(screen.getByText("src/a.py")).toBeTruthy();
  });
});
