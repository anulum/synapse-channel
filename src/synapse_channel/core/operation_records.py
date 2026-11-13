# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — operation records bound to a claimed task
"""Bind an operation, its outcome and its verification to one claimed task.

A worker that holds the live claim on a task first **declares** the operation
it is about to perform: an ``operation_id`` and the effect it will have
(a file, a git publication, a deployment or a migration). Later, an outcome
report (``synapse.execution-outcome.v1``) and one or more verification records
(``synapse.result-verification.v1``) are added for that operation. The hub
journals each accepted record as one event, so a reader of the journal alone
can tell whether the effect was verified, found absent, or is still unknown.

Rules (recovery console design, rev 2):

* A declaration needs the sender to own the live claim on the task, and the
  frame's epoch must be the claim's current ``epoch`` or its
  ``ownership_epoch``. The journal keeps the claim's current epoch as
  ``snapshot_epoch``. An operation id is declared once per hub.
* Outcome and verification records need a declared operation on the same task.
  Their sender is checked by the handler against the ``operation-evidence``
  permission. The verifier must match the declared effect.
* At most :data:`MAX_RECORDS_PER_OPERATION` outcome and verification rows per
  operation; the declaration does not count. Rows are append-only: a later row
  never replaces an earlier one.
* A seal record reports that its sender created an open attempt's ``sealed``
  marker at the system of record (:mod:`synapse_channel.core.operation_seals`).
  The handler checks it against ``operation-seal``; the hub keeps it only for an
  open attempt of a declared operation on the same task, and only when it names
  exactly that attempt's seal. It counts toward the record limit.
* A declaration may carry an authorisation intent
  (:mod:`synapse_channel.core.authorisation_intent`). Its row then keeps the
  composed intent and its digest; a declaration without one is unchanged.

Targets are identifiers, not secrets: a repository URL that carries
credentials is refused, not redacted.
"""

from __future__ import annotations

import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from synapse_channel.core.attempt_markers import commit_id_length, commit_object_format
from synapse_channel.core.authorisation_intent import (
    AuthorisationIntent,
    IntentStatement,
    parse_intent_statement,
)
from synapse_channel.core.errors import SynapseError
from synapse_channel.core.execution_outcome import ExecutionOutcome, outcome_from_json
from synapse_channel.core.journal import EventKind
from synapse_channel.core.operation_attempt_ledger import STATE_NONE, STATE_OPEN, AttemptLedger
from synapse_channel.core.operation_seals import (
    REFUSED_ATTEMPT_ENDED,
    REFUSED_ATTEMPT_UNKNOWN,
    SealRecord,
    decode_seal,
    seal_mismatch,
)
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.result_verification_record import (
    ResultVerification,
    verification_from_json,
)
from synapse_channel.core.state_models import TaskClaim

RECORD_DECLARE = "declare"
RECORD_OUTCOME = "outcome"
RECORD_VERIFICATION = "verification"
RECORD_SEAL = "seal"
RECORD_KINDS = frozenset({RECORD_DECLARE, RECORD_OUTCOME, RECORD_VERIFICATION, RECORD_SEAL})

EFFECT_FILE = "file"
EFFECT_GIT = "git_publication"
EFFECT_DEPLOYMENT = "deployment"
EFFECT_MIGRATION = "migration"
VERIFIER_FOR_EFFECT: Mapping[str, str] = {
    EFFECT_FILE: "file_change",
    EFFECT_GIT: "git_publication",
    EFFECT_DEPLOYMENT: "deployment",
    EFFECT_MIGRATION: "database_migration",
}
"""The verifier whose records may speak for each declared effect."""

MAX_RECORDS_PER_OPERATION = 32
"""Outcome, verification and seal rows kept per operation; the 33rd is refused."""

STATUS_ACCEPTED = "accepted"
STATUS_REFUSED = "refused"

REFUSED_INVALID = "invalid_request"
REFUSED_NO_JOURNAL = "no_journal"
REFUSED_NOT_HOLDER = "not_claim_holder"
REFUSED_STALE_EPOCH = "stale_epoch"
REFUSED_DUPLICATE = "duplicate_operation"
REFUSED_UNKNOWN = "unknown_operation"
REFUSED_TASK_MISMATCH = "task_mismatch"
REFUSED_OPERATION_MISMATCH = "operation_mismatch"
REFUSED_VERIFIER_MISMATCH = "verifier_mismatch"
REFUSED_RECORD_LIMIT = "record_limit"
REFUSED_NOT_AUTHORISED = "not_authorised"

_OPERATION_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}")
_HEX40 = re.compile(r"[0-9a-f]{40}")
_HEX64 = re.compile(r"[0-9a-f]{64}")
_BRANCH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,199}")
_MAX_TASK = 256
_MAX_PATH = 512
_MAX_NAME = 128
_MAX_BUDGET = 2**53


class OperationRecordError(SynapseError, ValueError):
    """Raised when an operation record request is malformed."""

    code = "operation_record"


@dataclass(frozen=True)
class OperationRecordRequest:
    """One decoded ``operation_record`` frame.

    Attributes
    ----------
    record : str
        ``declare``, ``outcome``, ``verification`` or ``seal``.
    task_id, operation_id : str
        The claimed task and the operation on it.
    epoch : int or None
        For a declaration, the claim epoch or ownership epoch the sender holds.
    effect : str or None
        For a declaration, the declared effect.
    target : Mapping[str, str]
        For a declaration, the effect's identifiers (the frame's ``effect_target``;
        ``target`` is the envelope's recipient).
    budget_remaining : int or None
        For a declaration, an optional remaining budget.
    outcome : ExecutionOutcome or None
        For an outcome record, the decoded report.
    verification : ResultVerification or None
        For a verification record, the decoded record.
    intent : IntentStatement or None
        For a declaration, the validated statement of its authorisation intent.
    seal : SealRecord or None
        For a seal record, what the sealer read back.
    """

    record: str
    task_id: str
    operation_id: str
    epoch: int | None = None
    effect: str | None = None
    target: Mapping[str, str] = field(default_factory=dict)
    budget_remaining: int | None = None
    outcome: ExecutionOutcome | None = None
    verification: ResultVerification | None = None
    intent: IntentStatement | None = None
    seal: SealRecord | None = None


def _bounded_text(value: object, label: str, limit: int) -> str:
    if not isinstance(value, str) or not value or len(value) > limit:
        raise OperationRecordError(f"{label} must be text of 1 to {limit} characters")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise OperationRecordError(f"{label} cannot contain control characters")
    return value


def _pattern(value: object, label: str, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise OperationRecordError(f"{label} has the wrong form")
    return value


def _commit_name(value: object) -> str:
    """Return one 40- or 64-hex commit name, using the shared object-id check."""
    if not isinstance(value, str):
        raise OperationRecordError("revision has the wrong form")
    try:
        commit_id_length(value, value)
    except ValueError:
        raise OperationRecordError("revision has the wrong form") from None
    return value


def _repository(value: object) -> str:
    """Return a repository name or URL that carries no credential."""
    text = _bounded_text(value, "repository", _MAX_PATH)
    if "://" in text:
        parts = urlsplit(text)
        if parts.query or parts.fragment:
            raise OperationRecordError("a repository URL cannot carry a query or fragment")
        if parts.username is not None or parts.password is not None:
            if parts.password is not None or parts.scheme in {"http", "https"}:
                raise OperationRecordError("a repository URL cannot carry a credential")
    return text


def _target(effect: str, value: object) -> dict[str, str]:
    """Validate one declared effect's identifiers; unknown fields are refused."""
    if not isinstance(value, Mapping):
        raise OperationRecordError("effect_target must be an object")
    if effect == EFFECT_FILE:
        fields = {
            "path": _bounded_text(value.get("path"), "path", _MAX_PATH),
            "sha256": _pattern(value.get("sha256"), "sha256", _HEX64),
        }
    elif effect == EFFECT_GIT:
        fields = {
            "repository": _repository(value.get("repository")),
            "branch": _pattern(value.get("branch"), "branch", _BRANCH),
            "revision": _commit_name(value.get("revision")),
        }
    elif effect == EFFECT_DEPLOYMENT:
        fields = {
            "report": _bounded_text(value.get("report"), "report", _MAX_PATH),
            "revision": _bounded_text(value.get("revision"), "revision", _MAX_NAME),
        }
    else:
        fields = {
            "database": _bounded_text(value.get("database"), "database", _MAX_PATH),
            "migration": _bounded_text(value.get("migration"), "migration", _MAX_NAME),
            "schema_sha256": _pattern(value.get("schema_sha256"), "schema_sha256", _HEX64),
        }
    if set(value) != set(fields):
        raise OperationRecordError(f"a {effect} effect_target names exactly {sorted(fields)}")
    return fields


def _whole(value: object, label: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise OperationRecordError(f"{label} must be an integer from {low} to {high}")
    return value


def decode_operation_record(data: Mapping[str, Any]) -> OperationRecordRequest:
    """Decode and validate one ``operation_record`` frame.

    Parameters
    ----------
    data : Mapping[str, Any]
        The inbound frame.

    Returns
    -------
    OperationRecordRequest
        The validated request.

    Raises
    ------
    OperationRecordError
        If any field is missing, of the wrong type, or out of bounds. Rejected
        values are not echoed.
    AuthorisationIntentError
        If a declaration carries an intent statement that is malformed.
    """
    record = data.get("record")
    if not isinstance(record, str) or record not in RECORD_KINDS:
        raise OperationRecordError(f"record must be one of {sorted(RECORD_KINDS)}")
    task_id = _bounded_text(data.get("task_id"), "task_id", _MAX_TASK)
    operation_id = _pattern(data.get("operation_id"), "operation_id", _OPERATION_ID)
    if record == RECORD_DECLARE:
        effect = data.get("effect")
        if not isinstance(effect, str) or effect not in VERIFIER_FOR_EFFECT:
            raise OperationRecordError(f"effect must be one of {sorted(VERIFIER_FOR_EFFECT)}")
        budget = data.get("budget_remaining")
        statement = data.get("intent")
        return OperationRecordRequest(
            record,
            task_id,
            operation_id,
            epoch=_whole(data.get("epoch"), "epoch", 1, _MAX_BUDGET),
            effect=effect,
            target=_target(effect, data.get("effect_target")),
            budget_remaining=None
            if budget is None
            else _whole(budget, "budget_remaining", 0, _MAX_BUDGET),
            intent=None if statement is None else parse_intent_statement(statement, effect),
        )
    try:
        if record == RECORD_SEAL:
            seal = decode_seal(data.get("seal"))
            return OperationRecordRequest(record, task_id, operation_id, seal=seal)
        if record == RECORD_OUTCOME:
            outcome = outcome_from_json(data.get("report"))
            return OperationRecordRequest(record, task_id, operation_id, outcome=outcome)
        verification = verification_from_json(data.get("verification"))
    except ValueError as exc:
        raise OperationRecordError(str(exc)) from None
    return OperationRecordRequest(record, task_id, operation_id, verification=verification)


def declaration_refusal(claim: TaskClaim | None, sender: str, epoch: int, now: float) -> str | None:
    """Return why ``sender`` may not declare on this claim, or ``None``.

    The sender must own the live claim, and ``epoch`` must be the claim's
    current epoch or its ownership epoch (the anchor a renewal keeps).
    """
    if claim is None or claim.owner != sender or claim.lease_expires_at <= now:
        return REFUSED_NOT_HOLDER
    if epoch not in (claim.epoch, claim.ownership_epoch):
        return REFUSED_STALE_EPOCH
    return None


@dataclass
class _Entry:
    task_id: str
    effect: str
    records: int = 0


class OperationLedger:
    """Declared operations, their record counts and their attempts, restored from the journal."""

    def __init__(self, store: EventStore | None) -> None:
        self._entries: dict[str, _Entry] = {}
        self._attempts = AttemptLedger(store)
        if store is None:
            return
        kinds = (
            EventKind.OPERATION_DECLARED,
            EventKind.OPERATION_OUTCOME,
            EventKind.OPERATION_VERIFIED,
            EventKind.OPERATION_SEALED,
        )
        for event in store.iter_events(kinds=kinds):
            operation_id = event.payload.get("operation_id")
            if not isinstance(operation_id, str):
                continue
            if event.kind == EventKind.OPERATION_DECLARED:
                task_id, effect = event.payload.get("task_id"), event.payload.get("effect")
                if isinstance(task_id, str) and isinstance(effect, str):
                    self._entries.setdefault(operation_id, _Entry(task_id, effect))
            elif operation_id in self._entries:
                self._entries[operation_id].records += 1

    @property
    def attempts(self) -> AttemptLedger:
        """Return the attempts of the declared operations."""
        return self._attempts

    def task_of(self, operation_id: str) -> str | None:
        """Return the task an operation was declared on, or ``None``."""
        entry = self._entries.get(operation_id)
        return None if entry is None else entry.task_id

    def effect_of(self, operation_id: str) -> str | None:
        """Return the declared effect of an operation, or ``None``."""
        entry = self._entries.get(operation_id)
        return None if entry is None else entry.effect

    def records_of(self, operation_id: str) -> int:
        """Return the number of outcome, verification and seal rows kept for it."""
        entry = self._entries.get(operation_id)
        return 0 if entry is None else entry.records

    def declare(self, operation_id: str, task_id: str, effect: str) -> None:
        """Remember one journalled declaration."""
        self._entries[operation_id] = _Entry(task_id, effect)

    def add_record(self, operation_id: str) -> None:
        """Count one journalled outcome, verification or seal row."""
        self._entries[operation_id].records += 1


def evidence_refusal(ledger: OperationLedger, request: OperationRecordRequest) -> str | None:
    """Return why an outcome or verification record cannot be kept, or ``None``."""
    task = ledger.task_of(request.operation_id)
    if task is None:
        return REFUSED_UNKNOWN
    if task != request.task_id:
        return REFUSED_TASK_MISMATCH
    if ledger.records_of(request.operation_id) >= MAX_RECORDS_PER_OPERATION:
        return REFUSED_RECORD_LIMIT
    body: ExecutionOutcome | ResultVerification | None = request.outcome or request.verification
    if body is None or body.operation_id != request.operation_id:
        return REFUSED_OPERATION_MISMATCH
    effect = ledger.effect_of(request.operation_id)
    if request.verification is not None and (
        effect is None or request.verification.verifier != VERIFIER_FOR_EFFECT[effect]
    ):
        return REFUSED_VERIFIER_MISMATCH
    return None


def seal_refusal(ledger: OperationLedger, request: OperationRecordRequest) -> str | None:
    """Return why a seal record cannot be kept, or ``None``.

    The operation must be declared on the same task, below the record limit,
    with the named attempt open, and the record must name exactly that
    attempt's seal (:func:`synapse_channel.core.operation_seals.seal_mismatch`).
    """
    task = ledger.task_of(request.operation_id)
    if task is None:
        return REFUSED_UNKNOWN
    if task != request.task_id:
        return REFUSED_TASK_MISMATCH
    if ledger.records_of(request.operation_id) >= MAX_RECORDS_PER_OPERATION:
        return REFUSED_RECORD_LIMIT
    seal = request.seal
    assert seal is not None, "decode_operation_record gives every seal record its body"
    state = ledger.attempts.attempt_state(request.operation_id, seal.attempt_id)
    if state == STATE_NONE:
        return REFUSED_ATTEMPT_UNKNOWN
    if state != STATE_OPEN:
        return REFUSED_ATTEMPT_ENDED
    fact = ledger.attempts.operation(request.operation_id)
    digest = None if fact is None else fact.intent_sha256
    return seal_mismatch(seal, operation_id=request.operation_id, intent_sha256=digest)


def _digests(evidence: Mapping[str, str]) -> dict[str, str]:
    """Keep only digest-shaped evidence values; free text is not journalled."""
    return {
        key: value
        for key, value in evidence.items()
        if _HEX40.fullmatch(value) or _HEX64.fullmatch(value)
    }


def record_operation(
    store: EventStore,
    request: OperationRecordRequest,
    *,
    sender: str,
    snapshot_epoch: int | None = None,
    now: float | None = None,
    intent: AuthorisationIntent | None = None,
) -> int:
    """Durably journal one accepted record and return its sequence.

    Parameters
    ----------
    store : EventStore
        The hub journal.
    request : OperationRecordRequest
        An accepted request.
    sender : str
        The authenticated sender.
    snapshot_epoch : int or None, optional
        For a declaration, the claim's current epoch at accept time.
    now : float or None, optional
        Record time; defaults to the wall clock.
    intent : AuthorisationIntent or None, optional
        For a declaration, the composed intent; the row keeps it and its digest.

    Returns
    -------
    int
        The journal sequence of the new event.

    Raises
    ------
    OperationRecordError
        If an intent is given for a record that is not a declaration, or an
        evidence record carries neither an outcome nor a verification.
    """
    if intent is not None and request.record != RECORD_DECLARE:
        raise OperationRecordError("only a declaration carries an authorisation intent")
    at = time.time() if now is None else now
    base: dict[str, Any] = {
        "task_id": request.task_id,
        "operation_id": request.operation_id,
        "sender": sender,
        "recorded_at": at,
    }
    if request.record == RECORD_DECLARE:
        payload = {**base, "effect": request.effect, **request.target}
        if request.effect == EFFECT_GIT:
            payload["object_format"] = commit_object_format(
                commit_id_length(payload["revision"], payload["revision"])
            )
        payload["snapshot_epoch"] = snapshot_epoch
        if request.budget_remaining is not None:
            payload["budget_remaining"] = request.budget_remaining
        if intent is not None:
            payload["intent"] = intent.document
            payload["intent_sha256"] = intent.sha256
        return store.append(EventKind.OPERATION_DECLARED, payload, durable=True)
    if request.seal is not None:
        payload = {**base, **request.seal.to_payload()}
        return store.append(EventKind.OPERATION_SEALED, payload, durable=True)
    if request.outcome is not None:
        outcome = request.outcome
        payload = {
            **base,
            "boundary": outcome.boundary,
            "state": outcome.state.value,
            "observation": outcome.observation,
            "external_effect": outcome.external_effect.value,
        }
        return store.append(EventKind.OPERATION_OUTCOME, payload, durable=True)
    verification = request.verification
    if verification is None:
        raise OperationRecordError("an evidence record carries an outcome or a verification")
    payload = {
        **base,
        "verifier": verification.verifier,
        "communication": verification.communication,
        "effect": verification.effect,
        "result": verification.result,
        "observation": verification.observation,
        "evidence": _digests(verification.evidence),
    }
    return store.append(EventKind.OPERATION_VERIFIED, payload, durable=True)
