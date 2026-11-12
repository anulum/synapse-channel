# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the closed answer to a request to open an attempt of an operation
"""Decide whether an executor may open an attempt of an approved operation.

An executor that holds the live claim on a task asks the hub to open an
attempt for the exact intent digest it is about to carry out. The hub answers
from a closed set, in a fixed order. The first answer that applies wins:

1. ``not_claim_holder``: the requester does not own the live claim on the task,
   or another identity declared the operation;
2. ``stale_epoch``: the stated epoch is neither the claim's epoch nor its
   ownership epoch;
3. ``unknown``: the hub's verdict gate is off, the current approval came
   through a bypassed gate, or the hub's clock reads earlier than the
   declaration;
4. ``not_approved``: the operation is not declared on this task, carries no
   intent, its intent is not the one named, or the latest approval decision
   on the intent is not ``approved``;
5. ``approval_stale``: the verdict gate, evaluated now, would refuse that
   approval;
6. ``approval_expired``: the hub's clock is at or past the intent's deadline;
7. ``approver_unauthorised``: the approver is the intent's actor, or belongs
   to another project;
8. ``operation_closed``: an independent verification has verified the
   operation;
9. ``attempt_exists``: an attempt of the operation is open, or the current
   approval has already admitted one;
10. ``earlier_attempt_unreconciled``: an attempt has ended and no independent
    observation has been recorded since;
11. ``approved_fresh``: none of the above. The hub opens the attempt and
    consumes the approval in the same transaction.

This module is pure. It decodes the request, states the facts the hub
gathers, decides, and names the attempt. Gathering the facts and journalling
the answer belong to the hub.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from synapse_channel.core.aef_canonical import canonical_json
from synapse_channel.core.errors import SynapseError

NOT_CLAIM_HOLDER = "not_claim_holder"
STALE_EPOCH = "stale_epoch"
UNKNOWN = "unknown"
NOT_APPROVED = "not_approved"
APPROVAL_STALE = "approval_stale"
APPROVAL_EXPIRED = "approval_expired"
APPROVER_UNAUTHORISED = "approver_unauthorised"
OPERATION_CLOSED = "operation_closed"
ATTEMPT_EXISTS = "attempt_exists"
EARLIER_ATTEMPT_UNRECONCILED = "earlier_attempt_unreconciled"
APPROVED_FRESH = "approved_fresh"
ANSWERS = (
    NOT_CLAIM_HOLDER,
    STALE_EPOCH,
    UNKNOWN,
    NOT_APPROVED,
    APPROVAL_STALE,
    APPROVAL_EXPIRED,
    APPROVER_UNAUTHORISED,
    OPERATION_CLOSED,
    ATTEMPT_EXISTS,
    EARLIER_ATTEMPT_UNRECONCILED,
    APPROVED_FRESH,
)
"""Every answer, in the order the hub tries them."""

INVALID_REQUEST = "invalid_request"
"""The reply to a malformed request; it is not an answer and is not journalled."""

ATTEMPT_ID_SCHEMA = "synapse.attempt-id.v0"
"""The schema name inside the bytes an attempt id is derived from."""

_OPERATION_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}")
_HEX64 = re.compile(r"[0-9a-f]{64}")
_MAX_TASK = 256
_MAX_KEY = 256
_MAX_EPOCH = 2**53


class OperationAttemptError(SynapseError, ValueError):
    """Raised when a request to open an attempt is malformed."""

    code = "operation_attempt"


@dataclass(frozen=True)
class AttemptRequest:
    """One decoded ``operation_attempt`` frame.

    Attributes
    ----------
    task_id, operation_id : str
        The claimed task and the declared operation on it.
    epoch : int
        The claim epoch or ownership epoch the requester holds.
    intent_sha256 : str
        The digest of the intent the requester is about to carry out.
    idem_key : str
        The requester's key for this request; a repeat with the same key is
        answered with the stored answer.
    """

    task_id: str
    operation_id: str
    epoch: int
    intent_sha256: str
    idem_key: str


@dataclass(frozen=True)
class ApprovalFact:
    """The current approval of an intent, as the hub reads it at the request.

    Attributes
    ----------
    seq : int
        Journal sequence of the approval note; it identifies the approval.
    approver : str
        The note's author.
    bypassed : bool
        Whether the note went through the verdict gate while it was off.
    gate_reasons : tuple[str, ...]
        Why the verdict gate would refuse this approval now; empty when it
        passes.
    """

    seq: int
    approver: str
    bypassed: bool
    gate_reasons: tuple[str, ...]


@dataclass(frozen=True)
class OperationFact:
    """The declared operation, as the hub's ledger holds it.

    Attributes
    ----------
    task_id, declarer : str
        The task it was declared on and the identity that declared it.
    intent_sha256 : str or None
        The digest of its intent, or ``None`` for a declaration without one.
    deadline : int or None
        The intent's deadline in UNIX seconds, or ``None`` without an intent.
    declared_at : float
        The hub's wall-clock time of the declaration.
    """

    task_id: str
    declarer: str
    intent_sha256: str | None
    deadline: int | None
    declared_at: float


@dataclass(frozen=True)
class AttemptFacts:
    """Everything the decision reads, gathered by the hub under one lock.

    Attributes
    ----------
    claim_refusal : str or None
        ``not_claim_holder`` or ``stale_epoch`` when the requester's claim on
        the task fails, else ``None``.
    operation : OperationFact or None
        The declared operation, or ``None`` when the id is not declared.
    gate_enabled : bool
        Whether the hub's verdict gate is on.
    approval : ApprovalFact or None
        The current approval of the named digest, or ``None``.
    closed : bool
        Whether an independent verification has verified the operation.
    attempt_open : bool
        Whether an attempt of the operation is open.
    approval_consumed : bool
        Whether the current approval has already admitted an attempt.
    unreconciled : bool
        Whether an ended attempt has no independent observation since.
    now : float
        The hub's wall-clock reading used for every time comparison.
    """

    claim_refusal: str | None
    operation: OperationFact | None
    gate_enabled: bool
    approval: ApprovalFact | None
    closed: bool
    attempt_open: bool
    approval_consumed: bool
    unreconciled: bool
    now: float


@dataclass(frozen=True)
class Decision:
    """One answer and the sentence that explains it."""

    answer: str
    detail: str


def _text(value: object, label: str, limit: int) -> str:
    if not isinstance(value, str) or not value or len(value) > limit:
        raise OperationAttemptError(f"{label} must be text of 1 to {limit} characters")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise OperationAttemptError(f"{label} cannot contain control characters")
    return value


def _form(value: object, label: str, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise OperationAttemptError(f"{label} has the wrong form")
    return value


def decode_attempt_request(data: Mapping[str, Any]) -> AttemptRequest:
    """Decode and validate one ``operation_attempt`` frame.

    Parameters
    ----------
    data : Mapping[str, Any]
        The inbound frame.

    Returns
    -------
    AttemptRequest
        The validated request.

    Raises
    ------
    OperationAttemptError
        If a field is missing, of the wrong type or out of bounds. A request
        without ``idem_key`` is refused: without it a repeated request could
        not be told from a new one. Rejected values are not echoed.
    """
    epoch = data.get("epoch")
    if isinstance(epoch, bool) or not isinstance(epoch, int) or not 1 <= epoch <= _MAX_EPOCH:
        raise OperationAttemptError(f"epoch must be an integer from 1 to {_MAX_EPOCH}")
    return AttemptRequest(
        task_id=_text(data.get("task_id"), "task_id", _MAX_TASK),
        operation_id=_form(data.get("operation_id"), "operation_id", _OPERATION_ID),
        epoch=epoch,
        intent_sha256=_form(data.get("intent_sha256"), "intent_sha256", _HEX64),
        idem_key=_text(data.get("idem_key"), "idem_key", _MAX_KEY),
    )


def project_of_identity(identity: str) -> str:
    """Return the project namespace of ``project/agent``; the whole name when it has none."""
    return identity.split("/", 1)[0]


def _before_the_approval(
    request: AttemptRequest, facts: AttemptFacts, requester: str
) -> Decision | tuple[OperationFact, ApprovalFact]:
    """Apply answers 1 to 4; otherwise return the operation and its approval."""
    operation = facts.operation
    if facts.claim_refusal == NOT_CLAIM_HOLDER or (
        operation is not None and operation.declarer != requester
    ):
        return Decision(
            NOT_CLAIM_HOLDER,
            "the requester does not hold the task's live claim as the operation's declarer",
        )
    if facts.claim_refusal == STALE_EPOCH:
        return Decision(
            STALE_EPOCH, "the stated epoch is neither the claim's epoch nor its ownership epoch"
        )
    if not facts.gate_enabled:
        return Decision(UNKNOWN, "the hub's verdict gate is off")
    if operation is not None and facts.now < operation.declared_at:
        return Decision(UNKNOWN, "the hub's clock reads earlier than the declaration")
    if facts.approval is not None and facts.approval.bypassed:
        return Decision(UNKNOWN, "the approval came through a bypassed verdict gate")
    if operation is None or operation.task_id != request.task_id:
        return Decision(NOT_APPROVED, "the operation is not declared on this task")
    if operation.intent_sha256 is None:
        return Decision(NOT_APPROVED, "the operation was declared without an authorisation intent")
    if operation.intent_sha256 != request.intent_sha256:
        return Decision(NOT_APPROVED, "the named digest is not the operation's intent")
    if facts.approval is None:
        return Decision(NOT_APPROVED, "the intent has no current approval")
    return operation, facts.approval


def _the_approval(
    facts: AttemptFacts, operation: OperationFact, approval: ApprovalFact
) -> Decision | None:
    """Apply answers 5 to 7, which judge the approval itself."""
    if approval.gate_reasons:
        return Decision(APPROVAL_STALE, "approval gate: " + "; ".join(approval.gate_reasons))
    if operation.deadline is not None and facts.now >= operation.deadline:
        return Decision(APPROVAL_EXPIRED, "the hub's clock is at or past the intent's deadline")
    if approval.approver == operation.declarer:
        return Decision(APPROVER_UNAUTHORISED, "the approver is the intent's actor")
    if project_of_identity(approval.approver) != project_of_identity(operation.declarer):
        return Decision(APPROVER_UNAUTHORISED, "the approver is not of the actor's project")
    return None


def _the_attempts(facts: AttemptFacts) -> Decision:
    """Apply answers 8 to 11, which read the operation's attempts."""
    if facts.closed:
        return Decision(OPERATION_CLOSED, "an independent verification has verified the operation")
    if facts.attempt_open:
        return Decision(ATTEMPT_EXISTS, "an attempt of the operation is open")
    if facts.approval_consumed:
        return Decision(ATTEMPT_EXISTS, "this approval has already admitted an attempt")
    if facts.unreconciled:
        return Decision(
            EARLIER_ATTEMPT_UNRECONCILED,
            "an ended attempt has no independent observation since its end",
        )
    return Decision(APPROVED_FRESH, "attempt opened")


def decide(request: AttemptRequest, facts: AttemptFacts, *, requester: str) -> Decision:
    """Return the one answer the hub gives to ``request`` in the state ``facts``.

    Parameters
    ----------
    request : AttemptRequest
        The decoded request.
    facts : AttemptFacts
        The state the hub read under its mutation lock.
    requester : str
        The authenticated sender.

    Returns
    -------
    Decision
        The first answer of :data:`ANSWERS` that applies, with its sentence.
    """
    early = _before_the_approval(request, facts, requester)
    if isinstance(early, Decision):
        return early
    operation, approval = early
    judged = _the_approval(facts, operation, approval)
    return judged if judged is not None else _the_attempts(facts)


def attempt_id(operation_id: str, approval_seq: int) -> str:
    """Return the hub's id of the attempt that ``approval_seq`` admits for ``operation_id``.

    One approval admits one attempt, so the pair is unique. The id is 32
    lower-case hexadecimal characters, valid in a Git ref name.

    Parameters
    ----------
    operation_id : str
        The declared operation.
    approval_seq : int
        The journal sequence of the consumed approval note.

    Returns
    -------
    str
        The first 128 bits of the SHA-256 of the canonical JSON of the pair.
    """
    document = {
        "approval_seq": approval_seq,
        "operation_id": operation_id,
        "schema": ATTEMPT_ID_SCHEMA,
    }
    return hashlib.sha256(canonical_json(document)).hexdigest()[:32]
