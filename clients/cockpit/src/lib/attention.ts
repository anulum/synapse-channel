// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE CHANNEL — deterministic evidence-ranked fleet attention queue

import type { PendingApprovalView } from "./approvals";
import type { BoardTask } from "./board";
import type { BranchConflictView, ClaimView } from "./claims";
import type { CommunicationModel } from "./communications";
import type { DeadLetterView } from "./deadLetters";
import type { WaitRow } from "./waits";

export type AttentionLevel = "critical" | "warning";
export type AttentionKind =
  | "branch_conflict"
  | "dead_letter"
  | "failed_route"
  | "stale_claim"
  | "missing_waiter"
  | "blocked_task"
  | "deferred_route"
  | "pending_approval"
  | "coordination_wait";

export type AttentionAction =
  | { readonly kind: "agent"; readonly id: string }
  | { readonly kind: "task"; readonly id: string }
  | { readonly kind: "route"; readonly source: string; readonly target: string };

export interface AttentionItem {
  readonly id: string;
  readonly level: AttentionLevel;
  readonly kind: AttentionKind;
  readonly subject: string;
  readonly evidence: string;
  /** Exact evidence timestamp in epoch seconds, null when the source has none. */
  readonly observedAt: number | null;
  readonly action: AttentionAction | null;
}

export interface AttentionInputs {
  readonly conflicts: readonly BranchConflictView[];
  readonly deadLetters: readonly DeadLetterView[];
  readonly communication: CommunicationModel;
  readonly claims: readonly ClaimView[];
  readonly missingWaiters: readonly string[];
  readonly board: readonly BoardTask[];
  readonly approvals: readonly PendingApprovalView[];
  readonly waits: readonly WaitRow[];
}

const KIND_RANK: Record<AttentionKind, number> = {
  branch_conflict: 0,
  dead_letter: 1,
  failed_route: 2,
  stale_claim: 3,
  missing_waiter: 4,
  blocked_task: 5,
  deferred_route: 6,
  pending_approval: 7,
  coordination_wait: 8,
};

function conflictEvidence(conflict: BranchConflictView): string {
  if (conflict.description !== "") return conflict.description;
  const paths = conflict.paths.length;
  return `${paths} overlapping ${paths === 1 ? "path" : "paths"}`;
}

function blockedEvidence(task: BoardTask): string {
  const unmet = task.dependsOn.filter((dependency) => !dependency.satisfied);
  if (unmet.length === 0) return `hub status: ${task.status}`;
  return `unmet: ${unmet.map((dependency) => dependency.taskId).join(", ")}`;
}

function taskAction(taskId: string): AttentionAction | null {
  return taskId === "" ? null : { kind: "task", id: taskId };
}

/**
 * Merge current fleet evidence into one stable queue. Ordering is categorical,
 * never an opaque score: critical before warning, then a documented kind rank,
 * oldest available evidence, and finally stable id.
 */
export function deriveAttentionQueue(input: AttentionInputs): AttentionItem[] {
  const items: AttentionItem[] = [];

  for (const conflict of input.conflicts) {
    const owners = [conflict.ownerA, conflict.ownerB].sort((a, b) => a.localeCompare(b));
    items.push({
      id: `conflict:${owners.join(":")}:${conflict.paths.join("|")}`,
      level: "critical",
      kind: "branch_conflict",
      subject: `${conflict.ownerA || "unknown"} ↔ ${conflict.ownerB || "unknown"}`,
      evidence: conflictEvidence(conflict),
      observedAt: null,
      action: conflict.ownerA === "" ? null : { kind: "agent", id: conflict.ownerA },
    });
  }

  for (const letter of input.deadLetters) {
    if (letter.count <= 0) continue;
    items.push({
      id: `dead-letter:${letter.target}`,
      level: "critical",
      kind: "dead_letter",
      subject: letter.target || "unknown target",
      evidence: `${letter.count} unread; last sender ${letter.lastSender || "unrecorded"}`,
      observedAt: letter.lastTs,
      action: letter.target === "" ? null : { kind: "agent", id: letter.target },
    });
  }

  for (const edge of input.communication.edges) {
    if (edge.failed > 0) {
      items.push({
        id: `failed-route:${edge.id}`,
        level: "critical",
        kind: "failed_route",
        subject: `${edge.source} → ${edge.target}`,
        evidence: `${edge.failed} failed receipt${edge.failed === 1 ? "" : "s"} across ${edge.messages} retained message${edge.messages === 1 ? "" : "s"}`,
        observedAt: edge.lastTs || null,
        action: { kind: "route", source: edge.source, target: edge.target },
      });
    } else if (edge.deferred > 0) {
      items.push({
        id: `deferred-route:${edge.id}`,
        level: "warning",
        kind: "deferred_route",
        subject: `${edge.source} → ${edge.target}`,
        evidence: `${edge.deferred} deferred receipt${edge.deferred === 1 ? "" : "s"} across ${edge.messages} retained message${edge.messages === 1 ? "" : "s"}`,
        observedAt: edge.lastTs || null,
        action: { kind: "route", source: edge.source, target: edge.target },
      });
    }
  }

  for (const claim of input.claims) {
    if (claim.urgency !== "stale") continue;
    items.push({
      id: `stale-claim:${claim.claim.task_id}:${claim.claim.owner}`,
      level: "warning",
      kind: "stale_claim",
      subject: claim.claim.task_id || "unnamed claim",
      evidence: `stale hold by ${claim.claim.owner || "unknown owner"}`,
      observedAt: null,
      action:
        claim.claim.owner === ""
          ? taskAction(claim.claim.task_id)
          : { kind: "agent", id: claim.claim.owner },
    });
  }

  for (const identity of input.missingWaiters) {
    const trimmed = identity.trim();
    if (trimmed === "") continue;
    items.push({
      id: `missing-waiter:${trimmed}`,
      level: "warning",
      kind: "missing_waiter",
      subject: trimmed,
      evidence: "snapshot reports no live waiter",
      observedAt: null,
      action: { kind: "agent", id: trimmed },
    });
  }

  for (const task of input.board) {
    if (task.bucket !== "blocked") continue;
    items.push({
      id: `blocked-task:${task.taskId}`,
      level: "warning",
      kind: "blocked_task",
      subject: task.taskId,
      evidence: blockedEvidence(task),
      observedAt: null,
      action: taskAction(task.taskId),
    });
  }

  input.approvals.forEach((approval, index) => {
    items.push({
      id: `approval:${approval.action}:${approval.namespace}:${approval.taskId}:${index}`,
      level: "warning",
      kind: "pending_approval",
      subject: approval.taskId || approval.namespace || "relay approval",
      evidence: `${approval.action || "relay"}; requested by ${approval.requester || "unrecorded"}`,
      observedAt: null,
      action: taskAction(approval.taskId),
    });
  });

  for (const wait of input.waits) {
    items.push({
      id: `wait:${wait.taskId}:${wait.since ?? "unknown"}`,
      level: "warning",
      kind: "coordination_wait",
      subject: wait.taskId || "unnamed task",
      evidence: `${wait.who || "unowned"} waits on ${wait.onWhat.join(", ") || "unrecorded dependency"}`,
      observedAt: wait.since,
      action: taskAction(wait.taskId),
    });
  }

  return items.sort(
    (a, b) =>
      (a.level === b.level ? 0 : a.level === "critical" ? -1 : 1) ||
      KIND_RANK[a.kind] - KIND_RANK[b.kind] ||
      (a.observedAt ?? Number.POSITIVE_INFINITY) -
        (b.observedAt ?? Number.POSITIVE_INFINITY) ||
      a.id.localeCompare(b.id),
  );
}
