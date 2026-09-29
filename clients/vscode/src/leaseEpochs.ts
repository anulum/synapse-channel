// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — the editor names its own lease's fencing epoch on release

/**
 * Remember the fencing epoch of each lease granted to the editor identity.
 *
 * A hub started with `--require-fencing-epoch` (forced by `--team-secure` and
 * `--secure`) refuses a release that names no epoch. The epoch comes only from
 * the editor's own `claim_granted` or `handoff_granted`, never from a state
 * snapshot: copying the current epoch from the hub would let a superseded
 * writer pass the fence.
 */

import { type HubStateChangedFrame } from "./hubProtocol.js";

/** Per-task epochs of the leases one editor identity holds. */
export class LeaseEpochMemory {
  private readonly epochs = new Map<string, number>();

  /** Record a grant to `identity`; forget a release or a handoff away. */
  observe(frame: HubStateChangedFrame, identity: string): void {
    if (frame.operation !== "release" && frame.owner === identity && frame.epoch !== null) {
      this.epochs.set(frame.taskId, frame.epoch);
    } else if (frame.operation !== "claim") {
      this.epochs.delete(frame.taskId);
    }
  }

  /** The epoch held for `taskId`, if this identity was granted one. */
  epochFor(taskId: string): number | undefined {
    return this.epochs.get(taskId);
  }

  /** Forget every epoch, for a new hub or identity. */
  clear(): void {
    this.epochs.clear();
  }
}

/** Release wire fields for `taskId`, naming the remembered epoch when there is one. */
export function releaseFields(taskId: string, memory: LeaseEpochMemory): Record<string, unknown> {
  const epoch = memory.epochFor(taskId);
  return epoch === undefined ? { task_id: taskId } : { task_id: taskId, epoch };
}
