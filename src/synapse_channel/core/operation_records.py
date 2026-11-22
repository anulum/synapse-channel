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
* At most :data:`MAX_RECORDS_PER_OPERATION` general rows per operation
  (executor outcomes, declarer self-verifications, non-reconciling
  verifications and ``unread`` stop read-backs); the declaration does not
  count. Stop-completion and independent reconciliation rows use separate
  bounded reserves sized to the operation's opened attempts and listed stop
  entries so a saturated executor cannot block a stop from reaching
  ``at rest``. Rows are append-only: a later row never replaces an earlier one.
* A seal record reports that its sender created an open attempt's ``sealed``
  marker at the system of record (:mod:`synapse_channel.core.operation_seals`).
  The handler checks it against ``operation-seal``; the hub keeps it only for an
  open attempt of a declared operation on the same task, and only when it names
  exactly that attempt's seal. It uses the per-attempt seal reserve
  (:data:`MAX_SEALS_PER_ATTEMPT` per opened attempt).
* A stop read-back record reports what a stop read back from the system of
  record for one attempt it listed
  (:mod:`synapse_channel.core.operation_stop_readbacks`). The handler checks
  it against ``operation-seal``; the hub keeps it only for an attempt the named
  stop listed, recomputes any marker it names, decides ``unsealable`` from the
  declared effect, answers a repeat of an already-kept result with that kept
  row without writing a second row, and refuses a different result once a
  closing read-back is kept. Newly journalled ``sealed``, ``applied`` and
  ``unsealable`` read-backs use the closing read-back reserve
  (:data:`MAX_CLOSING_READBACKS_PER_LISTED_ATTEMPT` per listed
  ``(stop_seq, attempt_id)`` pair), while ``unread`` counts toward the general
  limit; a recomputed ``sealed`` read-back marks the attempt sealed and a
  recomputed ``applied`` read-back ends it as ``applied`` (not sealed), while
  ``unsealable`` and ``unread`` end no attempt.
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
from synapse_channel.core.operation_attempt_ledger import (
    OBSERVED_EFFECTS,
    STATE_NONE,
    STATE_OPEN,
    AttemptLedger,
)
from synapse_channel.core.operation_seals import (
    REFUSED_ATTEMPT_ENDED,
    REFUSED_ATTEMPT_UNKNOWN,
    SealRecord,
    decode_seal,
    seal_mismatch,
)
from synapse_channel.core.operation_stop_readbacks import (
    CLOSING_RESULTS,
    REFUSED_READBACK_CLOSED,
    REFUSED_STOP_UNKNOWN,
    RESULT_UNSEALABLE,
    StopReadback,
    decode_stop_readback,
    readback_mismatch,
)
from synapse_channel.core.persistence import EventStore, StoredEvent
from synapse_channel.core.result_verification_record import (
    ResultVerification,
    verification_from_json,
)
from synapse_channel.core.state_models import TaskClaim

RECORD_DECLARE = "declare"
RECORD_OUTCOME = "outcome"
RECORD_VERIFICATION = "verification"
RECORD_SEAL = "seal"
RECORD_STOP_READBACK = "stop_readback"
RECORD_KINDS = frozenset(
    {RECORD_DECLARE, RECORD_OUTCOME, RECORD_VERIFICATION, RECORD_SEAL, RECORD_STOP_READBACK}
)

EFFECT_FILE = "file"
EFFECT_GIT = "git_publication"
EFFECT_DEPLOYMENT = "deployment"
EFFECT_MIGRATION = "migration"
EFFECT_COMPONENT_STATE = "component_state"
"""A component's own state, changed by a catalogue row and observed by a read row of it."""
VERIFIER_FOR_EFFECT: Mapping[str, str] = {
    EFFECT_FILE: "file_change",
    EFFECT_GIT: "git_publication",
    EFFECT_DEPLOYMENT: "deployment",
    EFFECT_MIGRATION: "database_migration",
    EFFECT_COMPONENT_STATE: "component_read",
}
"""The verifier whose records may speak for each declared effect."""

MAX_RECORDS_PER_OPERATION = 32
"""General rows kept per operation (outcomes, self- or non-reconciling verifications, unread);
the 33rd is refused."""

MAX_SEALS_PER_ATTEMPT = 1
"""Reserved seal rows per opened attempt; an attempt can be sealed at most once."""

MAX_CLOSING_READBACKS_PER_LISTED_ATTEMPT = 1
"""Reserved terminal read-back rows per listed ``(stop_seq, attempt_id)`` pair."""

MAX_RECONCILIATION_BASE = 2
"""Base independent reconciliation reserve (covers closing ``verified`` and post-close
``not_verified`` even at zero attempts)."""

MAX_RECONCILIATION_PER_ATTEMPT = 2
"""Additional independent reconciliation reserve per opened attempt."""

RESERVED_READBACK_RESULTS = frozenset({*CLOSING_RESULTS, RESULT_UNSEALABLE})
"""Stop read-back results that use the closing read-back reserve instead of the general cap."""

BUCKET_GENERAL = "general operation record limit"
BUCKET_SEAL = "seal reserve"
BUCKET_CLOSING_READBACK = "closing read-back reserve"
BUCKET_RECONCILIATION = "reconciliation reserve"
"""Readable names of the four per-operation capacity buckets for ``record_limit`` details."""

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
_HEX32 = re.compile(r"[0-9a-f]{32}")
_HEX40 = re.compile(r"[0-9a-f]{40}")
_HEX64 = re.compile(r"[0-9a-f]{64}")
_EVIDENCE_LENGTHS = frozenset({40, 64})
_BASE_GENERATION = re.compile(r"0|[1-9][0-9]{0,17}")
_EMPTY_CHAIN_DIGEST = "0" * 64
_BRANCH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,199}")
_MAX_TASK = 256
_MAX_PATH = 512
_MAX_NAME = 128
_DOTTED = re.compile(r"[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+")
COMPONENT_IDENTITY = ("host", "vendor", "component", "component_version", "os", "transport")
"""The fields of a component's identity, as a catalogue row names them."""
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
    readback : StopReadback or None
        For a stop read-back record, what the stop read back for one attempt.
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
    readback: StopReadback | None = None


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


_DEPLOYMENT_LEGACY_KEYS = frozenset({"report", "revision"})
_DEPLOYMENT_CHAINED_KEYS = frozenset(
    {
        "chain",
        "start_record",
        "base_generation",
        "base_artefact_sha256",
        "artefact_sha256",
        "revision",
    }
)
_MIGRATION_LEGACY_KEYS = frozenset({"database", "migration", "schema_sha256"})
_MIGRATION_PRESTATE_KEYS = frozenset(
    {"database", "migration", "base_schema_sha256", "schema_sha256"}
)


def _deployment_target(value: Mapping[str, Any]) -> dict[str, str]:
    """Validate either the legacy report-based or the generation-chained deployment target."""
    keys = set(value)
    if keys == _DEPLOYMENT_LEGACY_KEYS:
        return {
            "report": _bounded_text(value.get("report"), "report", _MAX_PATH),
            "revision": _bounded_text(value.get("revision"), "revision", _MAX_NAME),
        }
    if keys == _DEPLOYMENT_CHAINED_KEYS:
        base_generation = _pattern(
            value.get("base_generation"), "base_generation", _BASE_GENERATION
        )
        base_artefact = _pattern(value.get("base_artefact_sha256"), "base_artefact_sha256", _HEX64)
        if base_generation == "0" and base_artefact != _EMPTY_CHAIN_DIGEST:
            raise OperationRecordError(
                "an empty chain (base_generation '0') needs base_artefact_sha256 of 64 zeros"
            )
        return {
            "chain": _bounded_text(value.get("chain"), "chain", _MAX_PATH),
            "start_record": _bounded_text(value.get("start_record"), "start_record", _MAX_PATH),
            "base_generation": base_generation,
            "base_artefact_sha256": base_artefact,
            "artefact_sha256": _pattern(value.get("artefact_sha256"), "artefact_sha256", _HEX64),
            "revision": _bounded_text(value.get("revision"), "revision", _MAX_NAME),
        }
    raise OperationRecordError(
        f"a deployment effect_target names exactly {sorted(_DEPLOYMENT_LEGACY_KEYS)} "
        f"or {sorted(_DEPLOYMENT_CHAINED_KEYS)}"
    )


def _migration_target(value: Mapping[str, Any]) -> dict[str, str]:
    """Validate a migration target with or without its starting schema SHA-256."""
    keys = set(value)
    if keys not in (_MIGRATION_LEGACY_KEYS, _MIGRATION_PRESTATE_KEYS):
        raise OperationRecordError(
            f"a migration effect_target names exactly {sorted(_MIGRATION_LEGACY_KEYS)} "
            f"or {sorted(_MIGRATION_PRESTATE_KEYS)}"
        )
    fields = {
        "database": _bounded_text(value.get("database"), "database", _MAX_PATH),
        "migration": _bounded_text(value.get("migration"), "migration", _MAX_NAME),
        "schema_sha256": _pattern(value.get("schema_sha256"), "schema_sha256", _HEX64),
    }
    if "base_schema_sha256" in keys:
        fields["base_schema_sha256"] = _pattern(
            value.get("base_schema_sha256"), "base_schema_sha256", _HEX64
        )
    return fields


def _target(effect: str, value: object) -> dict[str, str]:
    """Validate one declared effect's identifiers; unknown fields are refused."""
    if not isinstance(value, Mapping):
        raise OperationRecordError("effect_target must be an object")
    if effect == EFFECT_DEPLOYMENT:
        return _deployment_target(value)
    if effect == EFFECT_MIGRATION:
        return _migration_target(value)
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
    else:
        fields = _component_target(value)
    if set(value) != set(fields):
        raise OperationRecordError(f"a {effect} effect_target names exactly {sorted(fields)}")
    return fields


def _component_target(value: Mapping[str, Any]) -> dict[str, str]:
    """Validate a component's identity, its observing read row and the expected digest."""
    fields = {name: _bounded_text(value.get(name), name, _MAX_NAME) for name in COMPONENT_IDENTITY}
    observed_by = _bounded_text(value.get("observed_by"), "observed_by", _MAX_NAME)
    fields["observed_by"] = _pattern(observed_by, "observed_by", _DOTTED)
    fields["expected_sha256"] = _pattern(value.get("expected_sha256"), "expected_sha256", _HEX64)
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
        if record == RECORD_STOP_READBACK:
            readback = decode_stop_readback(data.get("readback"))
            return OperationRecordRequest(record, task_id, operation_id, readback=readback)
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
    target: Mapping[str, str] = field(default_factory=dict)
    declarer: str | None = None
    records: int = 0
    seal_records: int = 0
    closing_readback_records: int = 0
    reconciliation_records: int = 0


def is_reconciliation_verification(
    *,
    sender: str,
    declarer: str | None,
    effect: str,
    result: str,
    closed: bool,
) -> bool:
    """Return whether a verification draws from the independent reconciliation reserve.

    Parameters
    ----------
    sender : str
        The authenticated sender of the verification record.
    declarer : str or None
        The identity that declared the operation, or ``None`` when unknown.
    effect : str
        The verification's ``effect`` field.
    result : str
        The verification's ``result`` field.
    closed : bool
        Whether the operation was already closed before this row.

    Returns
    -------
    bool
        ``True`` only when a sender other than the declarer records an observed
        effect (``observed`` or ``absent``), a closing ``verified`` result, or
        a ``not_verified`` contradiction after the operation is already closed.
    """
    if not sender or declarer is None or sender == declarer:
        return False
    return (
        effect in OBSERVED_EFFECTS or result == "verified" or (result == "not_verified" and closed)
    )


def readback_bucket(result: str) -> str:
    """Return the capacity bucket name for a stop read-back ``result``."""
    return BUCKET_CLOSING_READBACK if result in RESERVED_READBACK_RESULTS else BUCKET_GENERAL


_TARGET_KEYS = {
    EFFECT_FILE: ("path", "sha256"),
    EFFECT_GIT: ("repository", "branch", "revision", "object_format"),
    EFFECT_DEPLOYMENT: (
        "report",
        "chain",
        "start_record",
        "base_generation",
        "base_artefact_sha256",
        "artefact_sha256",
        "revision",
    ),
    EFFECT_MIGRATION: ("database", "migration", "base_schema_sha256", "schema_sha256"),
    EFFECT_COMPONENT_STATE: (*COMPONENT_IDENTITY, "observed_by", "expected_sha256"),
}
"""The declaration row's fields that name each effect's target."""


def _target_of(effect: str, payload: Mapping[str, Any]) -> dict[str, str]:
    """Return the target fields a declaration row keeps for its effect."""
    return {
        key: value
        for key in _TARGET_KEYS.get(effect, ())
        if isinstance(value := payload.get(key), str)
    }


class OperationLedger:
    """Declared operations, their per-bucket record counts and their attempts, restored in order."""

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
            EventKind.OPERATION_STOP_READBACK,
        )
        closed_ops: set[str] = set()
        for event in store.iter_events(kinds=kinds):
            self._restore_event(event, closed_ops)

    def _restore_event(self, event: StoredEvent, closed_ops: set[str]) -> None:
        """Replay one journal row in sequence order, classifying its bucket at its position."""
        operation_id = event.payload.get("operation_id")
        if not isinstance(operation_id, str):
            return
        if event.kind == EventKind.OPERATION_DECLARED:
            task_id, effect = event.payload.get("task_id"), event.payload.get("effect")
            if isinstance(task_id, str) and isinstance(effect, str):
                target = _target_of(effect, event.payload)
                raw_sender = event.payload.get("sender")
                declarer = raw_sender if isinstance(raw_sender, str) else None
                self._entries.setdefault(
                    operation_id, _Entry(task_id, effect, target, declarer=declarer)
                )
            return
        entry = self._entries.get(operation_id)
        if entry is None:
            return
        self._count_restored_row(operation_id, entry, event, closed_ops)

    def _count_restored_row(
        self, operation_id: str, entry: _Entry, event: StoredEvent, closed_ops: set[str]
    ) -> None:
        """Increment the capacity bucket of one non-declaration row at its replay position."""
        if event.kind == EventKind.OPERATION_SEALED:
            entry.seal_records += 1
        elif event.kind == EventKind.OPERATION_STOP_READBACK:
            if event.payload.get("result") in RESERVED_READBACK_RESULTS:
                entry.closing_readback_records += 1
            else:
                entry.records += 1
        elif event.kind == EventKind.OPERATION_VERIFIED:
            sender = str(event.payload.get("sender") or "")
            effect = str(event.payload.get("effect") or "")
            result = str(event.payload.get("result") or "")
            if is_reconciliation_verification(
                sender=sender,
                declarer=entry.declarer,
                effect=effect,
                result=result,
                closed=operation_id in closed_ops,
            ):
                entry.reconciliation_records += 1
            else:
                entry.records += 1
            if (
                sender
                and entry.declarer is not None
                and sender != entry.declarer
                and result == "verified"
            ):
                closed_ops.add(operation_id)
        else:
            entry.records += 1

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

    def declarer_of(self, operation_id: str) -> str | None:
        """Return the declarer of an operation, or ``None`` when not recorded."""
        entry = self._entries.get(operation_id)
        if entry is not None and entry.declarer is not None:
            return entry.declarer
        fact = self._attempts.operation(operation_id)
        return None if fact is None else fact.declarer

    def target_of(self, operation_id: str) -> Mapping[str, str]:
        """Return the declared target of an operation; empty when it is not declared."""
        entry = self._entries.get(operation_id)
        return {} if entry is None else dict(entry.target)

    def records_of(self, operation_id: str) -> int:
        """Return the number of general rows kept for ``operation_id``."""
        entry = self._entries.get(operation_id)
        return 0 if entry is None else entry.records

    def seal_records_of(self, operation_id: str) -> int:
        """Return the number of seal rows kept for ``operation_id``."""
        entry = self._entries.get(operation_id)
        return 0 if entry is None else entry.seal_records

    def closing_readback_records_of(self, operation_id: str) -> int:
        """Return the number of closing or unsealable read-back rows kept for ``operation_id``."""
        entry = self._entries.get(operation_id)
        return 0 if entry is None else entry.closing_readback_records

    def reconciliation_records_of(self, operation_id: str) -> int:
        """Return the number of independent reconciliation rows kept for ``operation_id``."""
        entry = self._entries.get(operation_id)
        return 0 if entry is None else entry.reconciliation_records

    def seal_limit_of(self, operation_id: str) -> int:
        """Return the reserved seal row cap for ``operation_id``."""
        return MAX_SEALS_PER_ATTEMPT * self._attempts.attempt_count(operation_id)

    def closing_readback_limit_of(self, operation_id: str) -> int:
        """Return the reserved closing/unsealable read-back cap for ``operation_id``."""
        return MAX_CLOSING_READBACKS_PER_LISTED_ATTEMPT * self._attempts.listed_pair_count(
            operation_id
        )

    def reconciliation_limit_of(self, operation_id: str) -> int:
        """Return the reserved independent reconciliation cap for ``operation_id``."""
        return MAX_RECONCILIATION_BASE + (
            MAX_RECONCILIATION_PER_ATTEMPT * self._attempts.attempt_count(operation_id)
        )

    def declare(
        self,
        operation_id: str,
        task_id: str,
        effect: str,
        target: Mapping[str, str] | None = None,
        *,
        declarer: str | None = None,
    ) -> None:
        """Remember one journalled declaration, its target and optionally its declarer."""
        self._entries[operation_id] = _Entry(task_id, effect, dict(target or {}), declarer=declarer)

    def add_record(self, operation_id: str) -> None:
        """Count one general outcome, verification or unread read-back row."""
        self._entries[operation_id].records += 1

    def add_seal_record(self, operation_id: str) -> None:
        """Count one journalled seal row in the seal reserve."""
        self._entries[operation_id].seal_records += 1

    def add_closing_readback_record(self, operation_id: str) -> None:
        """Count one journalled closing or unsealable read-back row in its reserve."""
        self._entries[operation_id].closing_readback_records += 1

    def add_reconciliation_record(self, operation_id: str) -> None:
        """Count one journalled independent reconciliation verification in its reserve."""
        self._entries[operation_id].reconciliation_records += 1


def evidence_bucket(
    ledger: OperationLedger, request: OperationRecordRequest, *, sender: str = ""
) -> str:
    """Return the capacity bucket name an outcome or verification draws from."""
    verification = request.verification
    if verification is not None and is_reconciliation_verification(
        sender=sender,
        declarer=ledger.declarer_of(request.operation_id),
        effect=verification.effect,
        result=verification.result,
        closed=ledger.attempts.view(request.operation_id, None).closed,
    ):
        return BUCKET_RECONCILIATION
    return BUCKET_GENERAL


def evidence_refusal(
    ledger: OperationLedger, request: OperationRecordRequest, *, sender: str = ""
) -> str | None:
    """Return why an outcome or verification record cannot be kept, or ``None``."""
    task = ledger.task_of(request.operation_id)
    if task is None:
        return REFUSED_UNKNOWN
    if task != request.task_id:
        return REFUSED_TASK_MISMATCH
    bucket = evidence_bucket(ledger, request, sender=sender)
    if bucket == BUCKET_RECONCILIATION:
        if ledger.reconciliation_records_of(request.operation_id) >= (
            ledger.reconciliation_limit_of(request.operation_id)
        ):
            return REFUSED_RECORD_LIMIT
    elif ledger.records_of(request.operation_id) >= MAX_RECORDS_PER_OPERATION:
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

    The operation must be declared on the same task, with the named attempt open,
    below the per-attempt seal reserve, and the record must name exactly that
    attempt's seal (:func:`synapse_channel.core.operation_seals.seal_mismatch`).
    """
    task = ledger.task_of(request.operation_id)
    if task is None:
        return REFUSED_UNKNOWN
    if task != request.task_id:
        return REFUSED_TASK_MISMATCH
    seal = request.seal
    assert seal is not None, "decode_operation_record gives every seal record its body"
    state = ledger.attempts.attempt_state(request.operation_id, seal.attempt_id)
    if state == STATE_NONE:
        return REFUSED_ATTEMPT_UNKNOWN
    if state != STATE_OPEN:
        return REFUSED_ATTEMPT_ENDED
    if ledger.seal_records_of(request.operation_id) >= ledger.seal_limit_of(request.operation_id):
        return REFUSED_RECORD_LIMIT
    if seal.stop_seq is not None and not ledger.attempts.stop_lists(
        seal.stop_seq, request.operation_id, seal.attempt_id
    ):
        return REFUSED_STOP_UNKNOWN
    fact = ledger.attempts.operation(request.operation_id)
    digest = None if fact is None else fact.intent_sha256
    return seal_mismatch(seal, operation_id=request.operation_id, intent_sha256=digest)


def declared_target(request: OperationRecordRequest) -> dict[str, str]:
    """Return a declaration's target as its row keeps it: a Git target adds its object format."""
    target = dict(request.target)
    if request.effect == EFFECT_GIT:
        revision = target["revision"]
        target["object_format"] = commit_object_format(commit_id_length(revision, revision))
    return target


MARKED_EFFECTS = frozenset({EFFECT_GIT})
"""The declared effects whose attempts have a marker at a system of record."""


def kept_stop_readback(
    ledger: OperationLedger, request: OperationRecordRequest
) -> tuple[str, int] | None:
    """Return ``(result, seq)`` when ``request`` repeats the kept read-back, else ``None``."""
    readback = request.readback
    assert readback is not None, "decode_operation_record gives every read-back record its body"
    kept = ledger.attempts.kept_readback(readback.stop_seq, readback.attempt_id)
    if kept is not None and kept[0] == readback.result:
        return kept
    return None


def stop_readback_refusal(ledger: OperationLedger, request: OperationRecordRequest) -> str | None:
    """Return why a stop read-back record cannot be kept, or ``None``.

    The operation must be declared on the same task; the named stop must have
    listed the attempt; once a closing read-back is kept, a different result is
    refused ``readback_closed`` while the same result returns the kept row; the
    record must agree with what the hub recomputes
    (:func:`synapse_channel.core.operation_stop_readbacks.readback_mismatch`);
    and a newly journalled row must stay below its capacity bucket (the closing
    read-back reserve for ``sealed``, ``applied`` and ``unsealable``, or the
    general record limit for ``unread``).
    """
    task = ledger.task_of(request.operation_id)
    if task is None:
        return REFUSED_UNKNOWN
    if task != request.task_id:
        return REFUSED_TASK_MISMATCH
    readback = request.readback
    assert readback is not None, "decode_operation_record gives every read-back record its body"
    attempts = ledger.attempts
    if not attempts.stop_lists(readback.stop_seq, request.operation_id, readback.attempt_id):
        return REFUSED_STOP_UNKNOWN
    kept = attempts.kept_readback(readback.stop_seq, readback.attempt_id)
    if kept is not None and kept[0] in CLOSING_RESULTS and readback.result != kept[0]:
        return REFUSED_READBACK_CLOSED
    fact = attempts.operation(request.operation_id)
    state = attempts.attempt_state(request.operation_id, readback.attempt_id)
    mismatch = readback_mismatch(
        readback,
        operation_id=request.operation_id,
        intent_sha256=None if fact is None else fact.intent_sha256,
        marked=ledger.effect_of(request.operation_id) in MARKED_EFFECTS,
        attempt_open=state == STATE_OPEN,
    )
    if mismatch is not None:
        return mismatch
    if kept_stop_readback(ledger, request) is None:
        if readback.result in RESERVED_READBACK_RESULTS:
            if ledger.closing_readback_records_of(request.operation_id) >= (
                ledger.closing_readback_limit_of(request.operation_id)
            ):
                return REFUSED_RECORD_LIMIT
        elif ledger.records_of(request.operation_id) >= MAX_RECORDS_PER_OPERATION:
            return REFUSED_RECORD_LIMIT
    return None


def verification_digests(evidence: Mapping[str, str]) -> dict[str, str]:
    """Keep 40- and 64-hex evidence, and a 32-hex ``attempt_id``.

    Free text is not journalled. A 32-hex value is kept only when its key is
    ``attempt_id``. Every other length, and that length under another key, is
    dropped.
    """
    kept: dict[str, str] = {}
    for key, value in evidence.items():
        if key == "attempt_id" and _HEX32.fullmatch(value):
            kept[key] = value
        elif len(value) in _EVIDENCE_LENGTHS and (
            _HEX40.fullmatch(value) is not None or _HEX64.fullmatch(value) is not None
        ):
            kept[key] = value
    return kept


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
        payload = {**base, "effect": request.effect, **declared_target(request)}
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
    if request.readback is not None:
        payload = {**base, **request.readback.to_payload()}
        return store.append(EventKind.OPERATION_STOP_READBACK, payload, durable=True)
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
        "evidence": verification_digests(verification.evidence),
    }
    return store.append(EventKind.OPERATION_VERIFIED, payload, durable=True)
