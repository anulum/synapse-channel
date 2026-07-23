// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — semantic projection of durable events into cockpit lanes

import type { CockpitEvent, EventKind } from "../types";
import { laneOf, SEVERITY_OF } from "./events";
import type { StoredEvent } from "./eventTailParser";

/** How much of a chat payload the label keeps before an ellipsis. */
const CHAT_LABEL_LIMIT = 160;

function asString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function trimmed(text: string): string {
  return text.length > CHAT_LABEL_LIMIT ? `${text.slice(0, CHAT_LABEL_LIMIT)}…` : text;
}

/**
 * Map one stored hub event onto the cockpit event model. Every mapping keeps
 * the hub's seq and ts verbatim. A kind this build does not know renders on
 * the chatter lane under its own name — shown, never hidden, never dressed up.
 */
export function mapStoredEvent(stored: StoredEvent): CockpitEvent {
  const payload = stored.payload;
  const taskId = asString(payload["task_id"]);
  let kind: EventKind;
  let actor: string;
  let label: string;

  if (stored.kind === "claim") {
    kind = "claim";
    actor = asString(payload["owner"]);
    label = `claimed ${taskId}`;
  } else if (stored.kind === "release") {
    kind = "release";
    actor = asString(payload["owner"]);
    label = `released ${taskId}`;
  } else if (stored.kind === "ledger_progress") {
    kind = asString(payload["kind"]) === "finding" ? "finding" : "chat";
    actor = asString(payload["author"]);
    const text = trimmed(asString(payload["text"]));
    label = taskId === "" ? text : `${taskId}: ${text}`;
  } else if (stored.kind === "ledger_task") {
    kind = "task";
    actor = asString(payload["created_by"]);
    const status = asString(payload["status"]);
    label = `task ${taskId}${status === "" ? "" : ` (${status})`}`;
  } else if (stored.kind === "chat") {
    kind = "chat";
    actor = asString(payload["sender"]);
    label = trimmed(asString(payload["payload"]));
  } else if (stored.kind === "dead_letter_escalation") {
    kind = "conflict";
    actor = asString(payload["target"]);
    const count = payload["count"];
    label = `dead-letter escalation: ${asString(payload["target"])}${
      typeof count === "number" ? ` · ${count} undelivered` : ""
    }`;
  } else if (stored.kind === "dead_letter_forwarding") {
    kind = "finding";
    actor = asString(payload["target"]);
    const forwarded = payload["count"];
    const origin = asString(payload["origin_hub_id"]);
    const owner = asString(payload["owner_hub_id"]);
    const direction = asString(payload["direction"]);
    label = `dead-letter forward${direction === "" ? "" : ` (${direction})`}: ${asString(payload["target"])}${
      typeof forwarded === "number" ? ` · ${forwarded} undelivered` : ""
    }${origin !== "" && owner !== "" ? ` · ${origin} → ${owner}` : ""}`;
  } else {
    kind = "chat";
    actor = "";
    label = stored.kind;
  }

  return {
    seq: stored.seq,
    ts: stored.ts,
    kind,
    lane: laneOf(kind),
    severity: SEVERITY_OF[kind],
    actor,
    label,
    taskId,
    payload: stored.payload,
  };
}
