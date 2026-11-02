# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — offline report of verdict states, stale notices and gated approvals
"""Summarise verdicts, corrections and approvals from a durable event log.

The report is what ``synapse verdict status`` prints. It reads only the log:
every verdict with its derived state, the gate decision for every reviewed
subject, the durable ``verdict_stale``, ``verdict_record_refused`` and
``verdict_gate_bypass`` rows, and every approval already granted for a subject
whose gate now fails (``granted_then_stale``). A granted approval is reported,
never revoked.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from synapse_channel.core.approvals import STATE_APPROVED, build_approval_report
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import StoredEvent
from synapse_channel.core.verdict_hub import (
    VerdictHubPolicy,
    approver_namespace,
    subject_digest,
)
from synapse_channel.core.verdict_records import VERDICT_RECORD_KINDS
from synapse_channel.core.verdict_staleness import (
    STATE_STALE,
    RecordedNote,
    fold_verdicts,
    gate_decision,
)

VERDICT_REPORT_EVENT_KINDS = frozenset(
    {
        EventKind.LEDGER_PROGRESS,
        EventKind.VERDICT_STALE,
        EventKind.VERDICT_RECORD_REFUSED,
        EventKind.VERDICT_GATE_BYPASS,
    }
)
"""Event kinds the report reads."""


def _notes(events: Iterable[StoredEvent]) -> list[RecordedNote]:
    return [
        RecordedNote(
            seq=event.seq,
            author=str(event.payload.get("author") or ""),
            kind=str(event.payload.get("kind") or ""),
            text=str(event.payload.get("text") or ""),
        )
        for event in events
        if event.kind == EventKind.LEDGER_PROGRESS
        and event.payload.get("kind") in VERDICT_RECORD_KINDS
    ]


def _rows(events: Sequence[StoredEvent], kind: str) -> list[dict[str, Any]]:
    return [{"seq": event.seq, **dict(event.payload)} for event in events if event.kind == kind]


def build_verdict_report(
    events: Sequence[StoredEvent], policy: VerdictHubPolicy, *, subject: str | None = None
) -> dict[str, Any]:
    """Return the JSON-ready verdict report, optionally for one subject digest.

    Subjects are keyed ``NAMESPACE:SHA256``; ``subject`` filters by digest in
    every namespace.
    """
    fold = fold_verdicts(_notes(events), policy.correction_policy())
    keys = sorted({(s.record.namespace, s.record.subject_sha256) for s in fold.verdicts})
    if subject is not None:
        keys = [key for key in keys if key[1] == subject]
    gates = {
        key: gate_decision(
            fold, key[0], key[1], allow_unrecorded_inputs=policy.allow_unrecorded_inputs
        )
        for key in keys
    }
    approvals = build_approval_report(events)
    granted_then_stale = [
        {"subject": status.subject, "reasons": list(gates[key].reasons)}
        for status in approvals.statuses
        if status.current_state == STATE_APPROVED
        and (digest := subject_digest(status.subject)) is not None
        and (key := (approver_namespace(status.decided_by), digest)) in gates
        and not gates[key].passed
    ]
    verdicts = [
        {
            "seq": state.seq,
            "namespace": state.record.namespace,
            "subject_sha256": state.record.subject_sha256,
            "reviewer": state.reviewer,
            "author": state.record.author,
            "outcome": state.record.outcome,
            "reviewer_sequence": state.record.reviewer_sequence,
            "state": state.state,
            "unacknowledged_corrections": list(state.unacknowledged_corrections),
        }
        for state in fold.verdicts
        if (state.record.namespace, state.record.subject_sha256) in gates
    ]
    return {
        "subjects": {
            f"{key[0]}:{key[1]}": {"passed": gate.passed, "reasons": list(gate.reasons)}
            for key, gate in gates.items()
        },
        "verdicts": verdicts,
        "stale_verdicts": sum(1 for item in verdicts if item["state"] == STATE_STALE),
        "verdict_stale_events": _rows(events, EventKind.VERDICT_STALE),
        "refused_records": _rows(events, EventKind.VERDICT_RECORD_REFUSED),
        "gate_bypasses": _rows(events, EventKind.VERDICT_GATE_BYPASS),
        "granted_then_stale": granted_then_stale,
        "gate_enabled": policy.gate_enabled,
    }


def render_verdict_report(report: dict[str, Any]) -> str:
    """Render the report as short human-readable lines."""
    lines = [f"verdict gate: {'on' if report['gate_enabled'] else 'off'}"]
    for digest, gate in report["subjects"].items():
        verdict = "passes" if gate["passed"] else "refused: " + "; ".join(gate["reasons"])
        lines.append(f"subject {digest}: {verdict}")
    for item in report["verdicts"]:
        lines.append(
            f"  verdict seq={item['seq']} reviewer={item['reviewer']} "
            f"outcome={item['outcome']} state={item['state']}"
        )
    for item in report["granted_then_stale"]:
        lines.append(f"granted then stale: {item['subject']} ({'; '.join(item['reasons'])})")
    lines.append(
        f"stale notices: {len(report['verdict_stale_events'])}; "
        f"refused records: {len(report['refused_records'])}; "
        f"gate bypasses: {len(report['gate_bypasses'])}"
    )
    return "\n".join(lines)
