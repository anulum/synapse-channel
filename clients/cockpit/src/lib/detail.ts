// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — detail selectors: everything the fleet knows about one name

// A click on a roster row or a board card opens a drawer, and this module
// decides what it says — pure joins over data the cockpit already holds
// (roster, claims, dead letters, the observed event window). Nothing is
// fetched, nothing is invented: the drawer is a different cut of the same
// attested material, and its event list states the same observed-window
// bound the log itself lives under.

import type { ClaimView } from "./claims";
import type { DeadLetterView } from "./deadLetters";
import type { BoardTask } from "./board";
import type { RosterEntry } from "./roster";
import type { CockpitEvent } from "../types";

/** How many recent events a drawer lists. */
export const DETAIL_EVENTS_SHOWN = 12;

/** The agent drawer's material. */
export interface AgentDetail {
  readonly name: string;
  /** The roster row, or null when the identity is not in the roster. */
  readonly entry: RosterEntry | null;
  /** Claims this identity holds, as the claims lib ranked them. */
  readonly claims: readonly ClaimView[];
  /** Dead letters addressed TO this identity — its unread mailbox. */
  readonly deadLetters: readonly DeadLetterView[];
  /** Its newest events in the observed window, newest first. */
  readonly recentEvents: readonly CockpitEvent[];
  /** Events in the window beyond the shown cut. */
  readonly moreEvents: number;
}

/** The task drawer's material. */
export interface TaskDetail {
  readonly taskId: string;
  /** The board card, or null when the task is not on the board. */
  readonly task: BoardTask | null;
  /** The claim holding this task, when one does. */
  readonly claim: ClaimView | null;
  /** The task's events in the observed window, newest first. */
  readonly recentEvents: readonly CockpitEvent[];
  readonly moreEvents: number;
}

/** Join everything the cockpit holds about one agent identity. */
export function agentDetail(
  name: string,
  roster: readonly RosterEntry[],
  claims: readonly ClaimView[],
  deadLetters: readonly DeadLetterView[],
  events: readonly CockpitEvent[],
): AgentDetail {
  const matching = events.filter((event) => event.actor === name);
  return {
    name,
    entry: roster.find((entry) => entry.agent === name) ?? null,
    claims: claims.filter((view) => view.claim.owner === name),
    deadLetters: deadLetters.filter((letter) => letter.target === name),
    recentEvents: matching.slice(0, DETAIL_EVENTS_SHOWN),
    moreEvents: Math.max(0, matching.length - DETAIL_EVENTS_SHOWN),
  };
}

/** Join everything the cockpit holds about one task. */
export function taskDetail(
  taskId: string,
  board: readonly BoardTask[],
  claims: readonly ClaimView[],
  events: readonly CockpitEvent[],
): TaskDetail {
  const matching = events.filter((event) => event.taskId === taskId);
  return {
    taskId,
    task: board.find((task) => task.taskId === taskId) ?? null,
    claim: claims.find((view) => view.claim.task_id === taskId) ?? null,
    recentEvents: matching.slice(0, DETAIL_EVENTS_SHOWN),
    moreEvents: Math.max(0, matching.length - DETAIL_EVENTS_SHOWN),
  };
}
