// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — the focus lens: one identity's work, held across polls

// "My work" on a read-only cockpit is a LENS, not an account: the operator
// names an identity (their own, or the one they are babysitting) and the
// claims board and task board narrow to it — persistently, across polls and
// reloads, until cleared. The panels state the lens is on; nothing is
// hidden silently.

import type { BoardTask } from "./board";
import type { ClaimView } from "./claims";

/** Claims held by the focused identity. */
export function focusClaims(claims: readonly ClaimView[], focus: string): ClaimView[] {
  if (focus === "") return [...claims];
  return claims.filter((view) => view.claim.owner === focus);
}

/**
 * Board tasks in the focused identity's orbit: the tasks its claims hold,
 * plus the tasks those gate (what my work unblocks is my work too).
 */
export function focusTasks(
  tasks: readonly BoardTask[],
  claims: readonly ClaimView[],
  focus: string,
): BoardTask[] {
  if (focus === "") return [...tasks];
  const held = new Set(
    claims.filter((view) => view.claim.owner === focus).map((view) => view.claim.task_id),
  );
  return tasks.filter(
    (task) =>
      held.has(task.taskId) ||
      task.dependsOn.some((chip) => held.has(chip.taskId)) ||
      task.unblocks.some((dependent) => held.has(dependent)),
  );
}
