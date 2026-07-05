// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — the board as a Markdown report, stated scope included

// A standup or a handover wants the board as text. The report renders the
// SHOWN board — whatever query or focus narrowed it — and says so in its
// header, with counts per bucket and each task's dependency verdicts, so a
// pasted report can never masquerade as the whole plan when it is a slice.

import type { BoardTask } from "./board";

const BUCKET_ORDER: readonly BoardTask["bucket"][] = ["blocked", "ready", "open", "done"];

function taskLine(task: BoardTask): string {
  const deps =
    task.dependsOn.length === 0
      ? ""
      : ` — depends on ${task.dependsOn
          .map((chip) => `${chip.taskId} (${chip.missing ? "missing" : chip.satisfied ? "satisfied" : "waiting"})`)
          .join(", ")}`;
  const unblocks = task.unblocks.length === 0 ? "" : ` — unblocked ${task.unblocks.join(", ")}`;
  const title = task.title === "" ? "" : ` — ${task.title}`;
  return `- \`${task.taskId}\`${title}${deps}${unblocks}`;
}

/**
 * Render the shown board as Markdown. `scope` names what the reader holds
 * ("full board", "query …", "focus …"); `generatedAt` is injected for
 * reproducibility.
 */
export function renderBoardReport(
  tasks: readonly BoardTask[],
  scope: string,
  generatedAt: string,
): string {
  const lines: string[] = [
    "# Task board report",
    "",
    `- generated: ${generatedAt}`,
    `- scope: ${scope}`,
    `- tasks: ${tasks.length}`,
    "",
  ];
  for (const bucket of BUCKET_ORDER) {
    const inBucket = tasks.filter((task) => task.bucket === bucket);
    if (inBucket.length === 0) continue;
    lines.push(`## ${bucket} · ${inBucket.length}`, "");
    for (const task of inBucket) lines.push(taskLine(task));
    lines.push("");
  }
  if (tasks.length === 0) lines.push("The shown board is empty.", "");
  return lines.join("\n");
}
