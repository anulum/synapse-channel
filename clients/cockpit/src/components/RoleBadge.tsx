// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — textual browser-principal role badge

import type { JSX } from "react";

import type { DashboardAccessState } from "../lib/access";

interface RoleBadgeProps {
  readonly access: DashboardAccessState;
  readonly onChangeAccess: () => void;
}

/** Display server-authored orientation only; capabilities remain outside this component. */
export function RoleBadge({ access, onChangeAccess }: RoleBadgeProps): JSX.Element {
  const descriptor = access.descriptor;
  const role = descriptor?.role ?? "unavailable";
  const label = descriptor === null
    ? "access unavailable"
    : `${descriptor.role} · ${descriptor.principal}`;
  return (
    <div className={`role-badge role-badge--${role}`}>
      <span className="role-badge__label">access</span>
      <strong className="role-badge__identity">{label}</strong>
      <button
        type="button"
        className="role-badge__change"
        aria-label="change access"
        onClick={onChangeAccess}
      >
        change
      </button>
    </div>
  );
}
