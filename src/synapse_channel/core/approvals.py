# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — human-in-the-loop approval gates from coordination events
"""Reconstruct human-in-the-loop approval state from the durable hub event log.

A held task or a policy-gated release sometimes needs an explicit human (or
operator) decision before work continues. This module models that decision as an
auditable workflow that rides the existing progress ledger: an ``approval``-kind
progress note carries a canonical ``subject``/``state`` body, where ``state`` is
``requested`` (awaiting a decision), ``approved``, or ``rejected``. The note
author is the actor; the optional trailing reason is free text.

Replaying those notes yields, per subject, the current decision state (the latest
event wins, so a fresh request after a rejection re-opens the gate), who
requested and decided it, and the full ordered history. The result is advisory
evidence and an audit trail, not a hard runtime gate: nothing here blocks a hub
mutation. An approved subject can be cited in a release receipt through the
existing ``synapse release --approval`` field. The canonical note format is
exposed (:func:`format_approval_note` / :func:`parse_approval_note`) so non-Python
clients can participate.

A request may also name whom it is addressed to and which batch it belongs to,
through four optional tokens before the reason: ``approver=<project>/<name>``,
``approver_role=<project>/<role>``, ``batch=<64 hex>`` and ``summary=<64 hex>``.
The summary token is the digest of the evidence summary the approver is shown;
the hub never stores or parses summary text. Readers that predate the tokens
ignore them. :func:`parse_approval_addressing` reads them leniently and
:func:`approval_addressing_problem` says what is malformed; the hub's
approval-load policy (:mod:`synapse_channel.core.approval_load`) refuses a
malformed or unaddressed request only where such a policy is declared.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import StoredEvent
from synapse_channel.core.persistence_reader import EventLogReader

APPROVAL_NOTE_KIND = "approval"
"""Progress-note ``kind`` marking a structured approval-workflow record."""

APPROVAL_NOTE_PREFIX = "approval"
"""Leading token of a canonical approval-note text body."""

STATE_REQUESTED = "requested"
"""Approval state for a subject awaiting a decision."""

STATE_APPROVED = "approved"
"""Approval state for an approved subject."""

STATE_REJECTED = "rejected"
"""Approval state for a rejected subject."""

APPROVAL_STATES = frozenset({STATE_REQUESTED, STATE_APPROVED, STATE_REJECTED})
"""The complete set of recognised approval states."""

AWAITING = "awaiting_approval"
"""Derived current state for a subject whose latest event is a request."""

_REASON_DELIMITER = " :: "
"""Separator between the structured head and the free-text reason in a note body."""

ADDRESSING_TOKENS = ("approver", "approver_role", "batch", "summary")
"""Optional request tokens naming the addressee, the batch and the evidence-summary digest."""

_DIGEST = re.compile(r"[0-9a-f]{64}")
"""A lower-case SHA-256 hex digest, the only accepted form of ``batch`` and ``summary``."""


@dataclass(frozen=True)
class ApprovalAddressing:
    """The optional addressing tokens of an approval note, read leniently.

    Attributes
    ----------
    approver : str
        The ``<project>/<name>`` identity the request is addressed to, or ``""``.
    approver_role : str
        The ``<project>/<role>`` role the request is addressed to, or ``""``.
    batch : str
        The 64-hex batch identifier the application built, or ``""``.
    summary : str
        The 64-hex digest of the evidence summary shown to the approver, or ``""``.
    """

    approver: str = ""
    approver_role: str = ""
    batch: str = ""
    summary: str = ""

    @property
    def is_addressed(self) -> bool:
        """Return whether the note names an approver identity or an approver role."""
        return bool(self.approver or self.approver_role)

    def present(self) -> dict[str, str]:
        """Return only the tokens the note carries, keyed by token name."""
        values = {
            "approver": self.approver,
            "approver_role": self.approver_role,
            "batch": self.batch,
            "summary": self.summary,
        }
        return {key: value for key, value in values.items() if value}


@dataclass(frozen=True)
class ApprovalEvent:
    """One approval request or decision parsed from a progress note.

    Attributes
    ----------
    subject : str
        Identifier of the gated thing (a task id or release/gate id).
    state : str
        One of :data:`APPROVAL_STATES`.
    actor : str
        The progress-note author who requested or decided.
    reason : str
        Optional free-text reason; empty when none was given.
    seq : int
        Durable event-log sequence the event was observed at.
    ts : float
        Event timestamp.
    addressing : ApprovalAddressing
        The request's optional addressing tokens; empty when it names none.
    """

    subject: str
    state: str
    actor: str
    reason: str
    seq: int
    ts: float
    addressing: ApprovalAddressing = ApprovalAddressing()


@dataclass(frozen=True)
class ApprovalStatus:
    """Replayed approval state for one subject.

    Attributes
    ----------
    subject : str
        Identifier of the gated thing.
    current_state : str
        :data:`AWAITING` while the latest event is a request, otherwise
        ``approved`` or ``rejected``.
    requested_by : str
        Actor of the most recent request, or ``""``.
    requested_at : float
        Timestamp of the most recent request, or ``0.0``.
    decided_by : str
        Actor of the most recent decision, or ``""`` when none applies.
    decided_at : float
        Timestamp of the most recent decision, or ``0.0``.
    decision_reason : str
        Reason attached to the most recent decision, or ``""``.
    history : tuple[ApprovalEvent, ...]
        Every event for this subject, in event-log order.
    """

    subject: str
    current_state: str
    requested_by: str
    requested_at: float
    decided_by: str
    decided_at: float
    decision_reason: str
    history: tuple[ApprovalEvent, ...]

    @property
    def is_pending(self) -> bool:
        """Return whether the subject is awaiting a decision."""
        return self.current_state == AWAITING


@dataclass(frozen=True)
class ApprovalReport:
    """Replayed approval workflow built from a durable event store."""

    generated_from_seq: int
    as_of: float
    statuses: tuple[ApprovalStatus, ...]

    @property
    def pending(self) -> tuple[ApprovalStatus, ...]:
        """Return only the subjects awaiting a decision."""
        return tuple(status for status in self.statuses if status.is_pending)

    @property
    def by_subject(self) -> dict[str, ApprovalStatus]:
        """Return statuses keyed by subject."""
        return {status.subject: status for status in self.statuses}


def format_approval_note(
    *,
    subject: str,
    state: str,
    reason: str = "",
    approver: str = "",
    approver_role: str = "",
    batch: str = "",
    summary: str = "",
) -> str:
    """Return the canonical text body for an approval progress note.

    Emit the result as a ``LEDGER_PROGRESS`` note with ``kind="approval"`` (see
    :data:`APPROVAL_NOTE_KIND`).

    Parameters
    ----------
    subject : str
        Identifier of the gated thing; must be non-empty and contain no whitespace.
    state : str
        One of :data:`APPROVAL_STATES`.
    reason : str, optional
        Free-text reason; collapsed to a single line and omitted when empty.
    approver : str, optional
        ``<project>/<name>`` identity a request is addressed to.
    approver_role : str, optional
        ``<project>/<role>`` role a request is addressed to.
    batch : str, optional
        64-hex batch identifier built by the requesting application.
    summary : str, optional
        64-hex digest of the evidence summary shown to the approver; never its text.

    Returns
    -------
    str
        Canonical approval-note text body.

    Raises
    ------
    ValueError
        If ``subject`` is empty or has whitespace, ``state`` is unknown, an
        addressing token is malformed, or a decision carries addressing tokens.
    """
    cleaned = subject.strip()
    if not cleaned or any(character.isspace() for character in cleaned):
        msg = "approval-note subject must be non-empty and contain no whitespace"
        raise ValueError(msg)
    if state not in APPROVAL_STATES:
        msg = f"approval-note state must be one of {sorted(APPROVAL_STATES)}"
        raise ValueError(msg)
    addressing = ApprovalAddressing(
        approver=approver, approver_role=approver_role, batch=batch, summary=summary
    )
    tokens = addressing.present()
    if tokens and state != STATE_REQUESTED:
        msg = "approval-note addressing tokens belong only to a request"
        raise ValueError(msg)
    problem = approval_addressing_problem(addressing)
    if problem is not None:
        raise ValueError(problem)
    head = f"{APPROVAL_NOTE_PREFIX} subject={cleaned} state={state}"
    head += "".join(f" {key}={tokens[key]}" for key in ADDRESSING_TOKENS if key in tokens)
    reason_line = " ".join(reason.split())
    if reason_line:
        return f"{head}{_REASON_DELIMITER}{reason_line}"
    return head


def parse_approval_note(text: str) -> dict[str, str] | None:
    """Parse a canonical approval-note body into its fields.

    Parameters
    ----------
    text : str
        Progress-note text body.

    Returns
    -------
    dict[str, str] or None
        Parsed fields (``subject``, ``state``, ``reason``), or ``None`` when the
        body is not a valid approval note.
    """
    head, _, reason = text.partition(_REASON_DELIMITER)
    tokens = head.split()
    if not tokens or tokens[0] != APPROVAL_NOTE_PREFIX:
        return None
    pairs: dict[str, str] = {}
    for token in tokens[1:]:
        key, separator, value = token.partition("=")
        if separator:
            pairs[key] = value
    subject = pairs.get("subject", "").strip()
    state = pairs.get("state", "").strip()
    if not subject or state not in APPROVAL_STATES:
        return None
    return {"subject": subject, "state": state, "reason": reason.strip()}


def parse_approval_addressing(text: str) -> ApprovalAddressing:
    """Return the addressing tokens an approval-note body carries, read leniently.

    Values are returned as written, malformed or not; an absent token reads as
    ``""``. A body that is not an approval note carries no addressing. When a
    token repeats, the last occurrence wins, as it does for every other token.

    Parameters
    ----------
    text : str
        Progress-note text body.

    Returns
    -------
    ApprovalAddressing
        The tokens found before the reason delimiter.
    """
    head, _, _ = text.partition(_REASON_DELIMITER)
    tokens = head.split()
    if not tokens or tokens[0] != APPROVAL_NOTE_PREFIX:
        return ApprovalAddressing()
    pairs: dict[str, str] = {}
    for token in tokens[1:]:
        key, separator, value = token.partition("=")
        if separator and key in ADDRESSING_TOKENS:
            pairs[key] = value
    return ApprovalAddressing(**pairs)


def approval_text_sha256(text: str) -> str:
    """Return the SHA-256 that ties an approval-load admission row to its request note.

    The note text is hashed as UTF-8 with lone surrogates passed through, so a
    hostile body that cannot be encoded strictly still gets one stable digest.
    """
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


def approval_addressing_problem(addressing: ApprovalAddressing) -> str | None:
    """Return why the addressing tokens are malformed, or ``None`` when they are well formed.

    ``approver`` and ``approver_role`` must each be ``<project>/<name>`` with a
    non-empty part on both sides of the first ``/``; ``batch`` and ``summary``
    must each be 64 lower-case hex characters. An absent token is well formed.

    Parameters
    ----------
    addressing : ApprovalAddressing
        Tokens as read by :func:`parse_approval_addressing`.

    Returns
    -------
    str or None
        A one-line reason naming the first malformed token, or ``None``.
    """
    for key, value in (
        ("approver", addressing.approver),
        ("approver_role", addressing.approver_role),
    ):
        project, separator, name = value.partition("/")
        if value and not (separator and project and name):
            return f"{key} must be '<project>/<name>', got {value!r}"
    for key, value in (("batch", addressing.batch), ("summary", addressing.summary)):
        if value and _DIGEST.fullmatch(value) is None:
            return f"{key} must be 64 lower-case hex characters"
    return None


def run_approval_report(
    db_path: str | Path,
    *,
    key_file: str | Path | None = None,
) -> ApprovalReport:
    """Build an approval report from a hub SQLite event store.

    Parameters
    ----------
    db_path : str or pathlib.Path
        Path to a hub event-store database.
    key_file : str or pathlib.Path or None, optional
        Owner-only SQLCipher key for an encrypted event store.

    Returns
    -------
    ApprovalReport
        Replayed approval workflow state.

    Raises
    ------
    ValueError
        If the event store does not exist.
    """
    path = Path(db_path)
    if not path.exists():
        msg = f"missing event store: {path}"
        raise ValueError(msg)
    store = EventLogReader(path, key_file=key_file)
    try:
        events = tuple(store.read_all())
    finally:
        store.close()
    return build_approval_report(events)


def build_approval_report(events: Sequence[StoredEvent]) -> ApprovalReport:
    """Build an approval report from loaded events.

    Parameters
    ----------
    events : collections.abc.Sequence[StoredEvent]
        Durable events read from a hub event store.

    Returns
    -------
    ApprovalReport
        Replayed approval workflow state, with subjects sorted by id.
    """
    history: dict[str, list[ApprovalEvent]] = {}
    for event in _approval_events(events):
        history.setdefault(event.subject, []).append(event)
    statuses = tuple(_status_for(subject, items) for subject, items in sorted(history.items()))
    return ApprovalReport(
        generated_from_seq=max((event.seq for event in events), default=0),
        as_of=max((event.ts for event in events), default=0.0),
        statuses=statuses,
    )


def approvals_to_json(report: ApprovalReport) -> dict[str, object]:
    """Return a stable JSON-compatible representation of an approval report."""
    return {
        "generated_from_seq": report.generated_from_seq,
        "as_of": report.as_of,
        "statuses": [_status_to_json(status) for status in report.statuses],
        "note": "advisory approval evidence and audit trail, not a runtime gate",
    }


def render_human(report: ApprovalReport) -> str:
    """Render an approval report as compact terminal text."""
    header = "Approval gates: advisory evidence, not a runtime gate"
    if not report.statuses:
        return f"{header}\n\nNo approval activity found."
    pending = report.pending
    lines = [
        header,
        f"generated_from_seq={report.generated_from_seq} as_of={report.as_of:.3f}",
        f"pending={len(pending)} of {len(report.statuses)} subjects",
        "",
        "Subjects",
    ]
    lines.extend(_render_status(status) for status in report.statuses)
    return "\n".join(lines)


def _approval_events(events: Sequence[StoredEvent]) -> tuple[ApprovalEvent, ...]:
    """Return approval events parsed from ``kind="approval"`` progress notes."""
    parsed: list[ApprovalEvent] = []
    for event in events:
        if event.kind != EventKind.LEDGER_PROGRESS:
            continue
        if str(event.payload.get("kind", "")) != APPROVAL_NOTE_KIND:
            continue
        text = str(event.payload.get("text", ""))
        fields = parse_approval_note(text)
        if fields is None:
            continue
        parsed.append(
            ApprovalEvent(
                subject=fields["subject"],
                state=fields["state"],
                actor=str(event.payload.get("author", "")),
                reason=fields["reason"],
                seq=event.seq,
                ts=event.ts,
                addressing=parse_approval_addressing(text),
            )
        )
    return tuple(parsed)


def _status_for(subject: str, items: Sequence[ApprovalEvent]) -> ApprovalStatus:
    """Replay one subject's events into a current status."""
    ordered = tuple(sorted(items, key=lambda item: item.seq))
    requested_by = ""
    requested_at = 0.0
    decided_by = ""
    decided_at = 0.0
    decision_reason = ""
    for item in ordered:
        if item.state == STATE_REQUESTED:
            requested_by = item.actor
            requested_at = item.ts
        else:
            decided_by = item.actor
            decided_at = item.ts
            decision_reason = item.reason
    latest = ordered[-1]
    current = AWAITING if latest.state == STATE_REQUESTED else latest.state
    if latest.state == STATE_REQUESTED:
        decided_by = ""
        decided_at = 0.0
        decision_reason = ""
    return ApprovalStatus(
        subject=subject,
        current_state=current,
        requested_by=requested_by,
        requested_at=requested_at,
        decided_by=decided_by,
        decided_at=decided_at,
        decision_reason=decision_reason,
        history=ordered,
    )


def _status_to_json(status: ApprovalStatus) -> dict[str, object]:
    """Convert an approval status into JSON-compatible fields."""
    return {
        "subject": status.subject,
        "current_state": status.current_state,
        "requested_by": status.requested_by,
        "requested_at": status.requested_at,
        "decided_by": status.decided_by,
        "decided_at": status.decided_at,
        "decision_reason": status.decision_reason,
        "history": [_event_to_json(event) for event in status.history],
    }


def _event_to_json(event: ApprovalEvent) -> dict[str, object]:
    """Convert an approval event into JSON-compatible fields.

    Addressing tokens appear only when the note carries them, so the output for
    a note without them is unchanged.
    """
    return {
        "subject": event.subject,
        "state": event.state,
        "actor": event.actor,
        "reason": event.reason,
        "seq": event.seq,
        "ts": event.ts,
        **event.addressing.present(),
    }


def _render_status(status: ApprovalStatus) -> str:
    """Render one approval status row, naming a pending request's addressee and batch."""
    if status.current_state == AWAITING:
        tail = f"requested by {status.requested_by or '-'}"
        request = status.history[-1].addressing.present()
        tail += "".join(f" {key}={request[key]}" for key in ADDRESSING_TOKENS if key in request)
    else:
        tail = f"{status.current_state} by {status.decided_by or '-'}"
        if status.decision_reason:
            tail += f" ({status.decision_reason})"
    return f"- {status.subject}: {status.current_state} — {tail}"
