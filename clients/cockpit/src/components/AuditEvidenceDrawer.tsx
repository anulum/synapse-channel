// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — exact durable-event audit evidence drawer

import type { JSX } from "react";
import { useEffect, useRef } from "react";
import type { AuditEvidence } from "../lib/auditEvidence";

interface AuditEvidenceDrawerProps {
  readonly evidence: AuditEvidence;
  readonly onClose: () => void;
  readonly onOpenEvent: (seq: number) => void;
}

function evidenceBoundary(evidence: AuditEvidence): string {
  if (evidence.kind === "paired-projection") {
    return "Exact association: the governed action and operator-relay receipt project the same durable event sequence.";
  }
  if (evidence.kind === "action-only") {
    return "Partial evidence: no operator-relay receipt is retained at this exact sequence. No subject or timestamp match is inferred.";
  }
  return "Receipt evidence only: no governed operator action is retained at this exact sequence. No subject or timestamp match is inferred.";
}

/** Show every retained audit projection for one exact event sequence. */
export function AuditEvidenceDrawer({ evidence, onClose, onOpenEvent }: AuditEvidenceDrawerProps): JSX.Element {
  const closeRef = useRef<HTMLButtonElement | null>(null);
  useEffect(() => {
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    closeRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent): void => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      previous?.focus();
    };
  }, [onClose]);

  return (
    <div className="audit-drawer__veil" role="presentation" onMouseDown={(event) => {
      if (event.target === event.currentTarget) onClose();
    }}>
      <section
        className="audit-drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby="audit-evidence-title"
        aria-describedby="audit-evidence-boundary"
      >
        <header className="audit-drawer__head">
          <div>
            <span className="audit-drawer__eyebrow">durable evidence</span>
            <h2 id="audit-evidence-title">event #{evidence.seq}</h2>
          </div>
          <button ref={closeRef} type="button" className="panel__clear" onClick={onClose} aria-label="Close audit evidence">
            close
          </button>
        </header>
        <div className="audit-drawer__body">
          <p id="audit-evidence-boundary" className={`audit-drawer__boundary audit-drawer__boundary--${evidence.kind}`}>
            {evidenceBoundary(evidence)}
          </p>
          <section aria-label="Governed action projections">
            <h3>Governed actions · {evidence.actions.length}</h3>
            {evidence.actions.length === 0 ? (
              <p className="panel__placeholder">No governed action projection retained at this sequence.</p>
            ) : (
              <dl className="audit-drawer__facts">
                {evidence.actions.map((action) => (
                  <div key={`${action.seq}:${action.direction}:${action.status}`} className="audit-drawer__fact">
                    <dt>{action.action || "action"} · {action.status || "unknown"}</dt>
                    <dd>{action.detail || action.reason || "no outcome detail"}</dd>
                    <dd>{action.taskId || action.namespace || "no subject"} · {action.operator || action.requester || action.agent || action.approver || "no actor"}</dd>
                    <dd>{action.applied ? "applied" : action.pending ? "pending" : "not applied"} · direction {action.direction || "unknown"}</dd>
                  </div>
                ))}
              </dl>
            )}
          </section>
          <section aria-label="Receipt projections">
            <h3>Receipts · {evidence.receipts.length}</h3>
            {evidence.receipts.length === 0 ? (
              <p className="panel__placeholder">No receipt projection retained at this sequence.</p>
            ) : (
              <dl className="audit-drawer__facts">
                {evidence.receipts.map((receipt) => (
                  <div key={receipt.receiptId} className="audit-drawer__fact">
                    <dt>{receipt.kind} · {receipt.status || "unknown"}</dt>
                    <dd>{receipt.summary || "receipt recorded"}</dd>
                    <dd>{receipt.subject || "no subject"} · {receipt.actor || "no actor"}</dd>
                    <dd className="audit-drawer__receipt-id">{receipt.receiptId}</dd>
                  </div>
                ))}
              </dl>
            )}
          </section>
        </div>
        <footer className="audit-drawer__actions">
          <button type="button" className="replay__latest" onClick={() => onOpenEvent(evidence.seq)}>
            open exact event #{evidence.seq}
          </button>
        </footer>
      </section>
    </div>
  );
}
