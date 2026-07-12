// @vitest-environment jsdom
// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — textual role badge tests

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";

import { RoleBadge } from "../../src/components/RoleBadge";
import {
  UNAVAILABLE_DASHBOARD_ACCESS,
  type DashboardAccessState,
  type DashboardRole,
} from "../../src/lib/access";

function access(role: DashboardRole, principal = "principal-a"): DashboardAccessState {
  return {
    phase: "ready",
    descriptor: {
      version: 1,
      principal,
      role,
      capabilities: { read: true, message_send: false, task_declare: false, task_update: false },
      operator_armed: false,
      trust_boundary: "presentation only",
    },
  };
}

afterEach(cleanup);

it.each(["viewer", "operator", "admin"] as const)("names the %s role in text", (role) => {
  render(<RoleBadge access={access(role)} onChangeAccess={() => {}} />);
  expect(screen.getByText(`${role} · principal-a`)).toBeTruthy();
  expect(document.querySelector(`.role-badge--${role}`)).not.toBeNull();
});

it("fails visibly and offers a keyboard-operable credential change", async () => {
  const onChange = vi.fn();
  render(<RoleBadge access={UNAVAILABLE_DASHBOARD_ACCESS} onChangeAccess={onChange} />);
  expect(screen.getByText("access unavailable")).toBeTruthy();
  await userEvent.click(screen.getByRole("button", { name: "change access" }));
  expect(onChange).toHaveBeenCalledOnce();
});
