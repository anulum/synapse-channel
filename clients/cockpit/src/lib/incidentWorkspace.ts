// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE CHANNEL — local-first guided incident workspace contract

import { selectionLabel } from "./selection";
import type { CockpitSelection, ReplayState } from "./workspace";

export const INCIDENT_DRAFT_SCHEMA = "synapse-cockpit-incident/v1";
export const INCIDENT_EVIDENCE_LIMIT = 64;
export const INCIDENT_TITLE_LIMIT = 120;
export const INCIDENT_HYPOTHESIS_LIMIT = 1_000;
export const INCIDENT_NOTES_LIMIT = 8_000;

/** One operator-selected evidence reference; no relationship is inferred. */
export interface IncidentEvidence {
  readonly key: string;
  readonly selection: CockpitSelection;
  readonly label: string;
  readonly addedAt: string;
  readonly replay: ReplayState;
}

/** One browser-local incident draft scoped to the authenticated principal. */
export interface IncidentDraft {
  readonly schema: typeof INCIDENT_DRAFT_SCHEMA;
  readonly id: string;
  readonly createdAt: string;
  readonly updatedAt: string;
  readonly title: string;
  readonly hypothesis: string;
  readonly notes: string;
  readonly evidence: readonly IncidentEvidence[];
}

/** Self-describing, non-authoritative document handed to an operator. */
export interface IncidentExport {
  readonly schema: typeof INCIDENT_DRAFT_SCHEMA;
  readonly exported_at: string;
  readonly provenance: "local-operator-draft";
  readonly authority: "not-a-hub-receipt-or-signed-audit-bundle";
  readonly evidence_boundary: {
    readonly association: "explicit-operator-selection-only";
    readonly event_material: "sequence-references-only";
    readonly replay: "captured-per-evidence-reference";
  };
  readonly cockpit: {
    readonly hub_version: string;
    readonly config_epoch: string;
    readonly current_replay: ReplayState;
  };
  readonly incident: IncidentDraft;
}

interface StorageLike {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

const CONTROL_CHARACTERS = /[\u0000-\u001f\u007f]/u;
const ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/u;

function recordOf(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function boundedText(value: unknown, limit: number): string | null {
  return typeof value === "string" && value.length <= limit && !CONTROL_CHARACTERS.test(value)
    ? value
    : null;
}

function entity(value: unknown): string | null {
  const parsed = boundedText(value, 512);
  return parsed !== null && parsed.trim() !== "" ? parsed.trim() : null;
}

function selectionOf(value: unknown): CockpitSelection | null {
  const record = recordOf(value);
  if (record === null) return null;
  const kind = record["kind"];
  if (kind === "event") {
    const seq = record["seq"];
    return typeof seq === "number" && Number.isSafeInteger(seq) && seq >= 0
      ? { kind, seq }
      : null;
  }
  if (kind === "route") {
    const source = entity(record["source"]);
    const target = entity(record["target"]);
    return source === null || target === null ? null : { kind, source, target };
  }
  if (kind === "agent" || kind === "project" || kind === "task") {
    const id = entity(record["id"]);
    return id === null ? null : { kind, id };
  }
  return null;
}

function replayOf(value: unknown): ReplayState | null {
  const record = recordOf(value);
  if (record === null) return null;
  if (record["mode"] === "live") return { mode: "live" };
  if (record["mode"] === "history") {
    const at = record["at"];
    return typeof at === "number" && Number.isSafeInteger(at) && at >= 0
      ? { mode: "history", at }
      : null;
  }
  if (record["mode"] === "compare") {
    const a = record["a"];
    const b = record["b"];
    return (
      typeof a === "number" && Number.isSafeInteger(a) && a >= 0 &&
      typeof b === "number" && Number.isSafeInteger(b) && b >= 0
    ) ? { mode: "compare", a, b } : null;
  }
  return null;
}

/** Stable identity for deduplicating one typed selection in the evidence cart. */
export function incidentEvidenceKey(selection: CockpitSelection): string {
  if (selection.kind === "route") return `route:${selection.source}->${selection.target}`;
  if (selection.kind === "event") return `event:${selection.seq}`;
  return `${selection.kind}:${selection.id}`;
}

function evidenceOf(value: unknown): IncidentEvidence | null {
  const record = recordOf(value);
  if (record === null) return null;
  const selection = selectionOf(record["selection"]);
  const replay = replayOf(record["replay"]);
  const key = boundedText(record["key"], 1_100);
  const label = boundedText(record["label"], 1_100);
  const addedAt = boundedText(record["addedAt"], 64);
  if (
    selection === null || replay === null || key === null || label === null || addedAt === null ||
    key !== incidentEvidenceKey(selection) || label !== selectionLabel(selection)
  ) return null;
  return { key, selection, label, addedAt, replay };
}

/** Create a fresh, empty local draft with an injected id and clock. */
export function createIncidentDraft(nowMs: number, id: string): IncidentDraft {
  if (!ID_PATTERN.test(id)) throw new Error("incident id must be a bounded public identifier");
  const timestamp = new Date(nowMs).toISOString();
  return {
    schema: INCIDENT_DRAFT_SCHEMA,
    id,
    createdAt: timestamp,
    updatedAt: timestamp,
    title: "",
    hypothesis: "",
    notes: "",
    evidence: [],
  };
}

/** Parse only the bounded version-one draft shape; malformed data is refused. */
export function parseIncidentDraft(value: unknown): IncidentDraft | null {
  const record = recordOf(value);
  if (record === null || record["schema"] !== INCIDENT_DRAFT_SCHEMA) return null;
  const id = record["id"];
  const createdAt = boundedText(record["createdAt"], 64);
  const updatedAt = boundedText(record["updatedAt"], 64);
  const title = boundedText(record["title"], INCIDENT_TITLE_LIMIT);
  const hypothesis = boundedText(record["hypothesis"], INCIDENT_HYPOTHESIS_LIMIT);
  const notes = boundedText(record["notes"], INCIDENT_NOTES_LIMIT);
  if (
    typeof id !== "string" || !ID_PATTERN.test(id) || createdAt === null || updatedAt === null ||
    title === null || hypothesis === null || notes === null || !Array.isArray(record["evidence"]) ||
    record["evidence"].length > INCIDENT_EVIDENCE_LIMIT
  ) return null;
  const evidence = record["evidence"].map(evidenceOf);
  if (evidence.some((item) => item === null)) return null;
  const concreteEvidence = evidence.filter((item): item is IncidentEvidence => item !== null);
  if (new Set(concreteEvidence.map((item) => item.key)).size !== concreteEvidence.length) return null;
  return {
    schema: INCIDENT_DRAFT_SCHEMA,
    id,
    createdAt,
    updatedAt,
    title,
    hypothesis,
    notes,
    evidence: concreteEvidence,
  };
}

/** Load a principal-scoped draft without letting storage failures break the cockpit. */
export function readIncidentDraft(storage: StorageLike, key: string): IncidentDraft | null {
  try {
    const raw = storage.getItem(key);
    return raw === null ? null : parseIncidentDraft(JSON.parse(raw) as unknown);
  } catch {
    return null;
  }
}

/** Persist a draft and report whether the browser accepted the write. */
export function writeIncidentDraft(
  storage: StorageLike,
  key: string,
  draft: IncidentDraft,
): boolean {
  try {
    storage.setItem(key, JSON.stringify(draft));
    return true;
  } catch {
    return false;
  }
}

/** Remove a principal-scoped local draft and report storage availability. */
export function clearIncidentDraft(storage: StorageLike, key: string): boolean {
  try {
    storage.removeItem(key);
    return true;
  } catch {
    return false;
  }
}

/** Capture one exact typed selection and its replay context. */
export function evidenceFromSelection(
  selection: CockpitSelection,
  replay: ReplayState,
  nowMs: number,
): IncidentEvidence {
  return {
    key: incidentEvidenceKey(selection),
    selection,
    label: selectionLabel(selection),
    addedAt: new Date(nowMs).toISOString(),
    replay,
  };
}

/** Add or remove evidence with deduplication and a hard local storage bound. */
export function withIncidentEvidence(
  draft: IncidentDraft,
  evidence: IncidentEvidence | null,
  removeKey: string | null,
  nowMs: number,
): IncidentDraft {
  let next = draft.evidence;
  if (removeKey !== null) next = next.filter((item) => item.key !== removeKey);
  if (
    evidence !== null && !next.some((item) => item.key === evidence.key) &&
    next.length < INCIDENT_EVIDENCE_LIMIT
  ) next = [...next, evidence];
  return { ...draft, evidence: next, updatedAt: new Date(nowMs).toISOString() };
}

/** Replace the editable text fields while enforcing their public limits. */
export function withIncidentText(
  draft: IncidentDraft,
  field: "title" | "hypothesis" | "notes",
  value: string,
  nowMs: number,
): IncidentDraft {
  const limit = field === "title"
    ? INCIDENT_TITLE_LIMIT
    : field === "hypothesis"
      ? INCIDENT_HYPOTHESIS_LIMIT
      : INCIDENT_NOTES_LIMIT;
  return { ...draft, [field]: value.slice(0, limit), updatedAt: new Date(nowMs).toISOString() };
}

/** Build the export without promoting a local draft to hub or audit authority. */
export function buildIncidentExport(
  draft: IncidentDraft,
  replay: ReplayState,
  hubVersion: string,
  configEpoch: string,
  nowMs: number,
): IncidentExport {
  return {
    schema: INCIDENT_DRAFT_SCHEMA,
    exported_at: new Date(nowMs).toISOString(),
    provenance: "local-operator-draft",
    authority: "not-a-hub-receipt-or-signed-audit-bundle",
    evidence_boundary: {
      association: "explicit-operator-selection-only",
      event_material: "sequence-references-only",
      replay: "captured-per-evidence-reference",
    },
    cockpit: {
      hub_version: hubVersion,
      config_epoch: configEpoch,
      current_replay: replay,
    },
    incident: draft,
  };
}

/** Filesystem-safe name for one local incident export. */
export function incidentExportFilename(draft: IncidentDraft, nowMs: number): string {
  const timestamp = new Date(nowMs).toISOString().replace(/[:.]/gu, "-").slice(0, 19);
  return `synapse-incident-${draft.id}-${timestamp}.json`;
}
