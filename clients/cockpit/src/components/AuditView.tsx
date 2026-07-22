// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — compact durable receipt and operator-action audit view

import type { JSX } from "react";
import type {
  OperatorActionRow,
  OperatorActionsState,
  ReceiptRow,
  ReceiptsState,
} from "../lib/auditFeeds";
import { auditEvidenceAt } from "../lib/auditEvidence";
import type { CockpitSelection } from "../lib/workspace";
import { AuditEvidenceDrawer } from "./AuditEvidenceDrawer";

const ROWS_SHOWN = 40;

function timeOf(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

function stateLabel(state: ReceiptsState | OperatorActionsState): string {
  if (state.status === "error" && state.data !== null) return "stale";
  if (state.status === "absent" && state.data !== null) return "stale · endpoint absent";
  return state.status;
}

function unavailable(
  label: string,
  state: ReceiptsState | OperatorActionsState,
): string | null {
  if (state.data !== null) return null;
  if (state.status === "absent") return `${label} feed absent — start the dashboard with --feeds-db.`;
  if (state.status === "error") return `${label} feed failed: ${state.error ?? "unknown error"}.`;
  return `Waiting for the ${label.toLowerCase()} feed.`;
}

function staleLine(
  label: string,
  state: ReceiptsState | OperatorActionsState,
): string | null {
  if (state.data === null || state.status === "live" || state.status === "connecting") return null;
  if (state.status === "absent") {
    return `${label} feed is absent; showing the last store-attested rows.`;
  }
  return `${label} feed is stale (${state.error ?? "unknown error"}); showing the last store-attested rows.`;
}

function receiptClass(row: ReceiptRow): string {
  return ["undelivered", "expired", "refused", "denied", "rejected"].includes(row.status)
    ? "evidence-row evidence-row--warn"
    : "evidence-row";
}

function actionClass(row: OperatorActionRow): string {
  return row.applied || row.pending
    ? "evidence-row"
    : "evidence-row evidence-row--warn";
}

function Receipt({ row, onSelect }: { readonly row: ReceiptRow; readonly onSelect?: (seq: number | null) => void }): JSX.Element {
  return (
    <li className={receiptClass(row)} title={row.sourceEventKind}>
      <button type="button" className="evidence-row__button" onClick={() => onSelect?.(row.seq)}>
        <span className="evidence-row__glyph" aria-hidden="true">
          ◇
        </span>
        <span className="evidence-row__body">
          <span className="evidence-row__meta">
            <span className="evidence-row__seq">seq {row.seq}</span>
            <span className="evidence-row__time">{timeOf(row.ts)}</span>
            <span className="evidence-row__kind">{row.kind}</span>
            <span className="evidence-row__kind">{row.status}</span>
          </span>
          <span className="evidence-row__detail">{row.summary || "receipt recorded"}</span>
          <span className="evidence-row__who">
            {row.subject || "no subject"} · {row.actor || "no actor"}
          </span>
        </span>
      </button>
    </li>
  );
}

function OperatorAction({ row, onSelect }: { readonly row: OperatorActionRow; readonly onSelect?: (seq: number | null) => void }): JSX.Element {
  const subject = row.taskId || row.namespace || "no subject";
  const actor = row.operator || row.requester || row.agent || row.approver || "no actor";
  const detail = row.detail || row.reason || row.direction || "operator relay recorded";
  return (
    <li className={actionClass(row)}>
      <button type="button" className="evidence-row__button" onClick={() => onSelect?.(row.seq)}>
        <span className="evidence-row__glyph" aria-hidden="true">
          ◆
        </span>
        <span className="evidence-row__body">
          <span className="evidence-row__meta">
            <span className="evidence-row__seq">seq {row.seq}</span>
            <span className="evidence-row__time">{timeOf(row.ts)}</span>
            <span className="evidence-row__kind">{row.action || "action"}</span>
            <span className="evidence-row__kind">{row.status}</span>
          </span>
          <span className="evidence-row__detail">{detail}</span>
          <span className="evidence-row__who">
            {subject} · {actor}
          </span>
        </span>
      </button>
    </li>
  );
}

interface AuditViewProps {
  readonly receipts: ReceiptsState;
  readonly operatorActions: OperatorActionsState;
  readonly selection?: CockpitSelection | null;
  readonly onSelectEvent?: (seq: number | null) => void;
  readonly onOpenEvent?: (seq: number) => void;
}

/** Render store-attested receipt and governed operator-relay history. */
export function AuditView({
  receipts,
  operatorActions,
  selection = null,
  onSelectEvent,
  onOpenEvent,
}: AuditViewProps): JSX.Element {
  const receiptRows = receipts.data ?? [];
  const actionRows = operatorActions.data ?? [];
  const shownReceipts = receiptRows.slice(0, ROWS_SHOWN);
  const shownActions = actionRows.slice(0, ROWS_SHOWN);
  const receiptUnavailable = unavailable("Receipts", receipts);
  const actionUnavailable = unavailable("Operator actions", operatorActions);
  const receiptStale = staleLine("Receipts", receipts);
  const actionStale = staleLine("Operator actions", operatorActions);
  const selectedEvidence = selection?.kind === "event"
    ? auditEvidenceAt(receiptRows, actionRows, selection.seq)
    : null;

  return (
    <section className="panel" aria-label="Receipt and operator audit">
      <div className="panel__head">
        <span>Audit</span>
        <span className="panel__count">{receiptRows.length + actionRows.length}</span>
        <span className="panel__sub">
          durable store · receipts {stateLabel(receipts)} · actions {stateLabel(operatorActions)}
        </span>
      </div>
      <div className="panel__body" tabIndex={0}>
        <section aria-label="Universal receipts">
          <h3>Universal receipts</h3>
          {receiptUnavailable !== null ? (
            <p className="panel__placeholder">{receiptUnavailable}</p>
          ) : (
            <>
              {receiptStale !== null && <p className="panel__placeholder">{receiptStale}</p>}
              {receiptRows.length === 0 ? (
                <p className="panel__placeholder">No universal receipts recorded.</p>
              ) : (
                <ul className="evidence">
                  {shownReceipts.map((row) => (
                    <Receipt key={row.seq} row={row} {...(onSelectEvent !== undefined ? { onSelect: onSelectEvent } : {})} />
                  ))}
                  {receiptRows.length > shownReceipts.length && (
                    <li className="evidence-row evidence-row--more">
                      {`+${receiptRows.length - shownReceipts.length} more retained receipts`}
                    </li>
                  )}
                </ul>
              )}
            </>
          )}
        </section>
        <section aria-label="Governed operator actions">
          <h3>Governed operator actions</h3>
          {actionUnavailable !== null ? (
            <p className="panel__placeholder">{actionUnavailable}</p>
          ) : (
            <>
              {actionStale !== null && <p className="panel__placeholder">{actionStale}</p>}
              {actionRows.length === 0 ? (
                <p className="panel__placeholder">No governed operator relay actions recorded.</p>
              ) : (
                <ul className="evidence">
                  {shownActions.map((row) => (
                    <OperatorAction key={row.seq} row={row} {...(onSelectEvent !== undefined ? { onSelect: onSelectEvent } : {})} />
                  ))}
                  {actionRows.length > shownActions.length && (
                    <li className="evidence-row evidence-row--more">
                      {`+${actionRows.length - shownActions.length} more retained actions`}
                    </li>
                  )}
                </ul>
              )}
            </>
          )}
        </section>
      </div>
      {selectedEvidence !== null && onSelectEvent !== undefined && onOpenEvent !== undefined && (
        <AuditEvidenceDrawer
          evidence={selectedEvidence}
          onClose={() => onSelectEvent(null)}
          onOpenEvent={onOpenEvent}
        />
      )}
    </section>
  );
}
