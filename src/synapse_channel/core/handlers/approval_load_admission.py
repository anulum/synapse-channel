# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — admission of approval requests under the declared approval-load policy
"""Refuse an approval request that is unaddressed, malformed or over its rate, before appending.

``handle_ledger_progress`` calls :func:`approval_load_admission` after the
verdict admission and before it appends the note. With no approval-load policy
declared, nothing here applies. Under a policy, only ``state=requested`` notes
are examined; ``approved`` and ``rejected`` notes are never refused here, and a
request already admitted is never revisited.

Under a policy a request is refused when:

- an addressing token is malformed (``approval_request_malformed``);
- it names no addressee while its sender's project has a rule
  (``approval_request_unaddressed``), so a limit cannot be bypassed by leaving
  the addressee out;
- a limit it counts against is full (``approval_rate_exceeded``, naming that
  limit).

Each refusal is journalled as a ``verdict_record_refused`` row of kind
``approval`` (author, kind, task and reason, never the body), so an auditor
counts it. An admitted request keeps its reserved place. The caller journals its
:meth:`ApprovalLoadAdmission.row` in the same transaction as the note, and
gives the place back when the note is not appended.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from synapse_channel.core.approvals import (
    APPROVAL_NOTE_KIND,
    STATE_REQUESTED,
    ApprovalAddressing,
    approval_addressing_problem,
    approval_text_sha256,
    parse_approval_addressing,
    parse_approval_note,
)
from synapse_channel.core.journal import record_verdict_record_refused

if TYPE_CHECKING:
    from typing import Protocol

    from synapse_channel.core.approval_load import ApprovalLoad, ApprovalLoadReservation
    from synapse_channel.core.handler_context import HandlerContext
    from synapse_channel.core.role_grants import RoleGrants

    class ApprovalLoadContext(HandlerContext, Protocol):
        """Capabilities the approval-load admission consumes."""

        @property
        def approval_load(self) -> ApprovalLoad | None:
            """Return the hub's approval-load state, or ``None`` with no policy declared."""
            ...

        @property
        def role_grants(self) -> RoleGrants | None:
            """Return the hub's role grants, which tell which roles an approver holds."""
            ...


NO_JOURNAL_REFUSAL = "approval-load policy needs a durable journal"
"""Reply to a request on a hub that declares a policy but keeps no journal."""


@dataclass(frozen=True)
class ApprovalLoadAdmission:
    """An admitted request's reserved place, settled once the append is known.

    Attributes
    ----------
    load : ApprovalLoad
        The hub's approval-load state that holds the reservation.
    reservation : ApprovalLoadReservation
        The places taken in each window.
    author : str
        The request's sender.
    subject : str
        The request's subject.
    text_sha256 : str
        SHA-256 of the request's text, which ties the admission row to its note.
    addressing : ApprovalAddressing
        The request's addressing tokens.
    """

    load: ApprovalLoad
    reservation: ApprovalLoadReservation
    author: str
    subject: str
    text_sha256: str
    addressing: ApprovalAddressing

    def row(self) -> dict[str, object]:
        """Return the ``approval_load_admitted`` row to journal with the request note."""
        return self.load.admission_row(
            self.reservation,
            author=self.author,
            subject=self.subject,
            text_sha256=self.text_sha256,
            addressing=self.addressing,
        )

    def not_appended(self) -> None:
        """Give the reserved places back; the request was not appended."""
        self.load.release(self.reservation)


def approval_load_admission(
    hub: ApprovalLoadContext, *, sender: str, task_id: str, kind: str, text: str
) -> tuple[str | None, ApprovalLoadAdmission | None]:
    """Return why the request must be refused, or its reserved admission.

    Returns
    -------
    tuple[str | None, ApprovalLoadAdmission | None]
        ``(reason, None)`` for a refusal, ``(None, admission)`` for a request
        counted against at least one limit, and ``(None, None)`` when no limit
        applies to the note.
    """
    if kind != APPROVAL_NOTE_KIND:
        return None, None
    load = hub.approval_load
    if load is None:
        return None, None
    fields = parse_approval_note(text)
    if fields is None or fields["state"] != STATE_REQUESTED:
        return None, None
    journal = hub.journal
    if journal is None:
        return NO_JOURNAL_REFUSAL, None
    addressing = parse_approval_addressing(text)
    problem = approval_addressing_problem(addressing)
    project = sender.partition("/")[0]
    admission: ApprovalLoadAdmission | None = None
    if problem is not None:
        reason: str | None = f"approval_request_malformed: {problem}"
    elif not addressing.is_addressed:
        if project not in load.policy.projects:
            return None, None
        reason = (
            f"approval_request_unaddressed: a request from project {project} "
            "must name approver= or approver_role="
        )
    else:
        load.start(journal)
        reservation, reason = load.reserve(load.matched_rules(addressing, hub.role_grants))
        if reservation is not None:
            admission = ApprovalLoadAdmission(
                load=load,
                reservation=reservation,
                author=sender,
                subject=fields["subject"],
                text_sha256=approval_text_sha256(text),
                addressing=addressing,
            )
    if reason is not None:
        record_verdict_record_refused(
            journal, {"author": sender, "kind": kind, "task_id": task_id, "reason": reason}
        )
    return reason, admission
