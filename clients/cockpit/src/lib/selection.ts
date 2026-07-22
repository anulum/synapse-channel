// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE CHANNEL — shared cockpit selection labels and evidence matchers

import type { ClaimView } from "./claims";
import type { CockpitSelection, FleetSelection } from "./workspace";
import type { CockpitEvent } from "../types";

function text(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

export function identityProject(identity: string): string {
  const slash = identity.indexOf("/");
  return slash > 0 ? identity.slice(0, slash) : identity;
}

export function selectionLabel(selection: CockpitSelection): string {
  if (selection.kind === "route") return `${selection.source} → ${selection.target}`;
  if (selection.kind === "event") return `sequence ${selection.seq}`;
  return selection.id;
}

export function fleetSelectionOf(selection: CockpitSelection | null): FleetSelection | null {
  return selection?.kind === "agent" || selection?.kind === "project" || selection?.kind === "route"
    ? selection
    : null;
}

export function identityMatchesSelection(
  identity: string,
  selection: CockpitSelection | null,
): boolean {
  if (selection === null) return false;
  if (selection.kind === "agent") return identity === selection.id;
  if (selection.kind === "project") return identityProject(identity) === selection.id;
  if (selection.kind === "route") {
    return identity === selection.source || identity === selection.target;
  }
  return false;
}

export function taskMatchesSelection(taskId: string, selection: CockpitSelection | null): boolean {
  return selection?.kind === "task" && taskId === selection.id;
}

export function claimMatchesSelection(view: ClaimView, selection: CockpitSelection | null): boolean {
  return (
    identityMatchesSelection(view.claim.owner, selection) ||
    taskMatchesSelection(view.claim.task_id, selection)
  );
}

export function eventMatchesSelection(
  event: CockpitEvent,
  selection: CockpitSelection | null,
): boolean {
  if (selection === null) return false;
  if (selection.kind === "event") return event.seq === selection.seq;
  if (selection.kind === "task") return event.taskId === selection.id;
  const sender = text(event.payload?.["sender"]);
  const target = text(event.payload?.["target"]);
  if (selection.kind === "route") {
    return sender === selection.source && target === selection.target;
  }
  if (selection.kind === "agent") {
    return event.actor === selection.id || sender === selection.id || target === selection.id;
  }
  return [event.actor, sender, target].some(
    (identity) => identity !== "" && identityProject(identity) === selection.id,
  );
}

export function subjectMatchesSelection(
  subject: string,
  selection: CockpitSelection | null,
): boolean {
  return identityMatchesSelection(subject, selection) || taskMatchesSelection(subject, selection);
}
