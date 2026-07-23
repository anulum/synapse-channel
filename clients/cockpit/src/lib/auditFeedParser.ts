// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — strict parsing for durable audit endpoint documents

/** One universal receipt projected by the durable store. */
export interface ReceiptRow {
  readonly seq: number;
  readonly ts: number;
  readonly receiptId: string;
  readonly kind: string;
  readonly subject: string;
  readonly actor: string;
  readonly status: string;
  readonly summary: string;
  readonly sourceEventKind: string;
}

/** One governed operator relay reconstructed from its audit event. */
export interface OperatorActionRow {
  readonly seq: number;
  readonly ts: number;
  readonly action: string;
  readonly direction: string;
  readonly status: string;
  readonly applied: boolean;
  readonly pending: boolean;
  readonly namespace: string;
  readonly taskId: string;
  readonly operator: string;
  readonly agent: string;
  readonly requester: string;
  readonly approver: string;
  readonly reason: string;
  readonly detail: string;
}

/** Parsed cursor page from one durable audit endpoint. */
export interface AuditPage<T extends { readonly seq: number }> {
  readonly rows: readonly T[];
  readonly nextCursor: number;
}

function objectRecord(raw: unknown): Record<string, unknown> | null {
  return typeof raw === "object" && raw !== null && !Array.isArray(raw)
    ? (raw as Record<string, unknown>)
    : null;
}

function sequence(value: unknown): number | null {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0 ? value : null;
}

function timestamp(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
}

function text(record: Record<string, unknown>, key: string): string | null {
  const value = record[key];
  return typeof value === "string" ? value : null;
}

function parseReceipt(raw: unknown): ReceiptRow | null {
  const record = objectRecord(raw);
  if (record === null) return null;
  const seq = sequence(record["seq"]);
  const ts = timestamp(record["ts"]);
  const receiptId = text(record, "receipt_id");
  const kind = text(record, "kind");
  const subject = text(record, "subject");
  const actor = text(record, "actor");
  const status = text(record, "status");
  const summary = text(record, "summary");
  const sourceEventKind = text(record, "source_event_kind");
  if (
    seq === null ||
    ts === null ||
    receiptId === null ||
    kind === null ||
    subject === null ||
    actor === null ||
    status === null ||
    summary === null ||
    sourceEventKind === null
  ) {
    return null;
  }
  return { seq, ts, receiptId, kind, subject, actor, status, summary, sourceEventKind };
}

function parseOperatorAction(raw: unknown): OperatorActionRow | null {
  const record = objectRecord(raw);
  if (record === null) return null;
  const seq = sequence(record["seq"]);
  const ts = timestamp(record["ts"]);
  const action = text(record, "action");
  const direction = text(record, "direction");
  const status = text(record, "status");
  const namespace = text(record, "namespace");
  const taskId = text(record, "task_id");
  const operator = text(record, "operator");
  const agent = text(record, "agent");
  const requester = text(record, "requester");
  const approver = text(record, "approver");
  const reason = text(record, "reason");
  const detail = text(record, "detail");
  if (
    seq === null ||
    ts === null ||
    action === null ||
    direction === null ||
    status === null ||
    namespace === null ||
    taskId === null ||
    operator === null ||
    agent === null ||
    requester === null ||
    approver === null ||
    reason === null ||
    detail === null ||
    typeof record["applied"] !== "boolean" ||
    typeof record["pending"] !== "boolean"
  ) {
    return null;
  }
  return {
    seq,
    ts,
    action,
    direction,
    status,
    applied: record["applied"],
    pending: record["pending"],
    namespace,
    taskId,
    operator,
    agent,
    requester,
    approver,
    reason,
    detail,
  };
}

function parsePage<T extends { readonly seq: number }>(
  raw: unknown,
  key: "receipts" | "actions",
  parseRow: (entry: unknown) => T | null,
): AuditPage<T> | null {
  const record = objectRecord(raw);
  if (record === null || record["present"] !== true || !Array.isArray(record[key])) return null;
  const nextCursor = sequence(record["next_cursor"]);
  if (nextCursor === null) return null;
  const rows: T[] = [];
  for (const entry of record[key]) {
    const row = parseRow(entry);
    if (row === null) return null;
    rows.push(row);
  }
  return { rows, nextCursor };
}

/** Parse one strict `/receipts.json` cursor page. */
export function parseReceiptsPage(raw: unknown): AuditPage<ReceiptRow> | null {
  return parsePage(raw, "receipts", parseReceipt);
}

/** Parse one strict `/operator-actions.json` cursor page. */
export function parseOperatorActionsPage(raw: unknown): AuditPage<OperatorActionRow> | null {
  return parsePage(raw, "actions", parseOperatorAction);
}
