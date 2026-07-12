// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — command palette catalogue and ranking

// Ctrl/Cmd+K opens a list of everything the cockpit can do from the
// keyboard. Navigation commands steer panels the pointer already steers;
// governed write commands open focused forms and never bypass the hub.

import type { DashboardCapabilities } from "./access";

export { sendOperatorMessage, type OperatorSendResult } from "./operatorActions";

/** What a palette entry does when chosen. */
export type CommandKind =
  | "focus-agent"
  | "inspect-agent"
  | "inspect-task"
  | "trace-task"
  | "toggle-theme"
  | "toggle-density"
  | "toggle-travel"
  | "clear-focus"
  | "operator-message"
  | "operator-task-declare"
  | "operator-task-update";

/** One palette entry. */
export interface Command {
  readonly id: string;
  readonly kind: CommandKind;
  readonly title: string;
  /** The argument the kind acts on (agent name, task id); "" for toggles. */
  readonly subject: string;
  /** Extra matched text (project names, aliases). */
  readonly keywords: string;
}

/** Build the command list from what the fleet currently shows. */
export function buildCommands(
  agents: readonly string[],
  taskIds: readonly string[],
  capabilities: DashboardCapabilities,
): Command[] {
  const commands: Command[] = [
    { id: "toggle-theme", kind: "toggle-theme", title: "toggle theme (dark / light)", subject: "", keywords: "palette colours" },
    { id: "toggle-density", kind: "toggle-density", title: "toggle density (cozy / compact)", subject: "", keywords: "rows spacing" },
    { id: "toggle-travel", kind: "toggle-travel", title: "time travel (scrub fleet state)", subject: "", keywords: "history replay state-at" },
    { id: "clear-focus", kind: "clear-focus", title: "clear focus lens", subject: "", keywords: "my work reset" },
  ];
  if (capabilities.message_send)
    commands.push({ id: "operator-message", kind: "operator-message", title: "operator: send a message…", subject: "", keywords: "write chat relay say" });
  if (capabilities.task_declare)
    commands.push({ id: "operator-task-declare", kind: "operator-task-declare", title: "operator: declare a task…", subject: "", keywords: "write board create dependency" });
  if (capabilities.task_update)
    commands.push({ id: "operator-task-update", kind: "operator-task-update", title: "operator: update a task…", subject: "", keywords: "write board status note progress" });
  for (const agent of agents) {
    commands.push(
      { id: `focus:${agent}`, kind: "focus-agent", title: `focus ${agent}`, subject: agent, keywords: "lens my work" },
      { id: `agent:${agent}`, kind: "inspect-agent", title: `inspect agent ${agent}`, subject: agent, keywords: "drawer detail" },
    );
  }
  for (const taskId of taskIds) {
    commands.push(
      { id: `task:${taskId}`, kind: "inspect-task", title: `inspect task ${taskId}`, subject: taskId, keywords: "drawer detail board" },
      { id: `trace:${taskId}`, kind: "trace-task", title: `trace ${taskId}`, subject: taskId, keywords: "causality causes effects" },
    );
  }
  return commands;
}

/** How many matches the palette shows. */
export const PALETTE_SHOWN = 12;

/**
 * Rank commands against a query: prefix beats word-start beats substring,
 * over title first and keywords second; an empty query shows the toggles
 * and the write (the static head of the list).
 */
export function matchCommands(commands: readonly Command[], query: string): Command[] {
  const needle = query.trim().toLowerCase();
  if (needle === "") return commands.slice(0, PALETTE_SHOWN);
  const scored: { command: Command; score: number }[] = [];
  for (const command of commands) {
    const title = command.title.toLowerCase();
    const keywords = command.keywords.toLowerCase();
    let score = -1;
    if (title.startsWith(needle)) score = 0;
    else if (title.includes(` ${needle}`)) score = 1;
    else if (title.includes(needle)) score = 2;
    else if (keywords.includes(needle)) score = 3;
    if (score >= 0) scored.push({ command, score });
  }
  scored.sort((a, b) => a.score - b.score || a.command.title.localeCompare(b.command.title));
  return scored.slice(0, PALETTE_SHOWN).map((entry) => entry.command);
}
