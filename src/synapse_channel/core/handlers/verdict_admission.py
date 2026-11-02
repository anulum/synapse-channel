# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — progress-note admission for verdict records and gated approvals
"""Refuse unauthorised verdict records and stale approvals before they are appended.

``handle_ledger_progress`` calls :func:`progress_refusal` before it appends a
note and :func:`announce_stale_verdicts` after a correction was appended. A
refusal is journalled as a ``verdict_record_refused`` row (author, kind, task and
reason, never the body) and reported privately to the sender. A correction that
turns verdicts stale is journalled as one ``verdict_stale`` row per verdict, and
the verdict's author and reviewer each get a directed notice when online.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from synapse_channel.core.approvals import (
    APPROVAL_NOTE_KIND,
    STATE_APPROVED,
    parse_approval_note,
)
from synapse_channel.core.journal import (
    record_verdict_gate_bypass,
    record_verdict_record_refused,
    record_verdict_stale,
)
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.verdict_hub import (
    admission_refusal,
    approval_refusal,
    newly_stale,
    recorded_notes,
    subject_digest,
)
from synapse_channel.core.verdict_records import CORRECTION_NOTE_KIND, VERDICT_RECORD_KINDS
from synapse_channel.core.verdict_staleness import RecordedNote

if TYPE_CHECKING:
    from typing import Protocol

    from synapse_channel.core.handler_context import HandlerContext
    from synapse_channel.core.verdict_hub import VerdictHubPolicy

    class VerdictAdmissionContext(HandlerContext, Protocol):
        """Capabilities the verdict admission helpers consume."""

        @property
        def verdict_policy(self) -> VerdictHubPolicy:
            """Return this hub's verdict and correction policy."""
            ...


NO_JOURNAL_REFUSAL = "evidence, verdict and correction records need a durable journal"
"""Private reply when a hub without a journal receives a verdict-family note."""


def progress_refusal(
    hub: VerdictAdmissionContext, *, sender: str, task_id: str, kind: str, text: str
) -> tuple[str | None, list[RecordedNote]]:
    """Return why the note must be refused (or ``None``) and the notes read for it.

    Verdict-family notes are validated and authorised; an ``approved`` approval
    of a SHA-256 subject must pass the verdict gate. Other notes pass untouched.
    """
    journal = hub.journal
    if kind in VERDICT_RECORD_KINDS:
        if journal is None:
            return NO_JOURNAL_REFUSAL, []
        notes = recorded_notes(journal)
        reason = admission_refusal(notes, hub.verdict_policy, author=sender, kind=kind, text=text)
    elif kind == APPROVAL_NOTE_KIND and journal is not None:
        fields = parse_approval_note(text)
        if fields is None or fields["state"] != STATE_APPROVED:
            return None, []
        notes = recorded_notes(journal)
        reason = _approval_gate(hub, journal, notes, sender=sender, subject=fields["subject"])
    else:
        return None, []
    if reason is not None and journal is not None:
        record_verdict_record_refused(
            journal,
            {"author": sender, "kind": kind, "task_id": task_id, "reason": reason},
        )
    return reason, notes


def _approval_gate(
    hub: VerdictAdmissionContext,
    journal: EventStore,
    notes: list[RecordedNote],
    *,
    sender: str,
    subject: str,
) -> str | None:
    """Gate one approval; with the gate switched off, journal what it would have said."""
    policy = hub.verdict_policy
    if policy.gate_enabled:
        decision = approval_refusal(notes, policy, subject, approver=sender)
        return None if decision is None else "approval gate: " + "; ".join(decision.reasons)
    if subject_digest(subject) is not None:
        would = approval_refusal(
            notes, replace(policy, gate_enabled=True), subject, approver=sender
        )
        record_verdict_gate_bypass(
            journal,
            {
                "author": sender,
                "subject": subject,
                "gate_would_refuse": would is not None,
                "reasons": list(would.reasons) if would is not None else [],
            },
        )
    return None


async def announce_stale_verdicts(
    hub: VerdictAdmissionContext,
    notes: list[RecordedNote],
    *,
    sender: str,
    kind: str,
    text: str,
) -> int:
    """Journal and announce every verdict the appended correction turned stale.

    Returns
    -------
    int
        Number of verdicts turned stale.
    """
    journal = hub.journal
    if kind != CORRECTION_NOTE_KIND or journal is None:
        return 0
    stale = newly_stale(notes, hub.verdict_policy, author=sender, kind=kind, text=text)
    for state in stale:
        record = {
            "verdict_seq": state.seq,
            "namespace": state.record.namespace,
            "subject_sha256": state.record.subject_sha256,
            "author": state.record.author,
            "reviewer": state.reviewer,
            "corrections": list(state.unacknowledged_corrections),
            "corrected_by": sender,
        }
        record_verdict_stale(journal, record)
        notice = (
            f"Verdict at seq {state.seq} on {state.record.namespace}:"
            f"{state.record.subject_sha256} is stale: "
            f"an input it read was corrected by {sender}."
        )
        for seat in sorted({state.record.author, state.reviewer}):
            await hub.send_to_agent(seat, hub.system(notice, target=seat, verdict_stale=record))
    return len(stale)
