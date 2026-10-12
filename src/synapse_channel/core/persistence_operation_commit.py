# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — keyed mutation transaction orchestration
"""Prepare immutable transaction inputs and commit one keyed mutation atomically.

Replay/conflict lookup precedes predecessor and capacity checks. All event,
compatibility, operation and outbox inserts share one write reservation. The
observer's post-commit exception cannot roll back an already durable outcome.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from synapse_channel.core.atomic_operations import OperationRecord
from synapse_channel.core.persistence_connection import StoreConnection
from synapse_channel.core.persistence_operations import decode_operation
from synapse_channel.core.persistence_receipt_codec import encode_object

if TYPE_CHECKING:
    from synapse_channel.core.persistence import OperationCommitResult, StoredOperation

ResponseFinalizer = Callable[[dict[str, Any], tuple[int, ...]], Mapping[str, Any]]


@dataclass(frozen=True)
class ResponseBuilder:
    """Validated mutually exclusive response sequence builders."""

    field: str | None
    finalize: ResponseFinalizer | None


@dataclass(frozen=True)
class PreparedOperation:
    """Private finite JSON inputs prepared before taking the writer lock."""

    key: str
    digest: str
    stamp: float
    rows: list[tuple[float, str, str]]
    response: dict[str, Any]
    response_json: str
    intent_json: str
    predecessor: tuple[str, str, str, str] | None
    builder: ResponseBuilder
    capacity: int | None
    hook: Callable[[str], None]


def _predecessor(record: OperationRecord | None) -> tuple[str, str, str, str] | None:
    """Validate and freeze the exact predecessor before a write reservation."""
    if record is None:
        return None
    digest = record.request_digest
    if (
        not record.key
        or digest is None
        or len(digest) != 64
        or any(char not in "0123456789abcdef" for char in digest)
    ):
        raise ValueError("invalid required predecessor identity")
    response = encode_object(record.response)
    return record.key, digest, response, hashlib.sha256(response.encode("ascii")).hexdigest()


def prepare_operation(
    *,
    operation_key: str,
    request_digest: str,
    response: Mapping[str, Any],
    events: Iterable[tuple[str, Mapping[str, Any]]],
    intent: Mapping[str, Any],
    response_event_seq_field: str | None = None,
    finalize_response: ResponseFinalizer | None = None,
    max_retained_operations: int | None = None,
    required_predecessor: OperationRecord | None = None,
    stage_hook: Callable[[str], None] | None = None,
) -> PreparedOperation:
    """Validate identities and builders and freeze JSON in the original order."""
    if max_retained_operations is not None and (
        type(max_retained_operations) is not int or not 0 < max_retained_operations < 2**53
    ):
        raise ValueError("invalid retained operation budget")
    predecessor = _predecessor(required_predecessor)
    if finalize_response is not None and response_event_seq_field is not None:
        raise ValueError("choose one atomic response sequence builder")
    if not operation_key:
        raise ValueError("atomic operation key must be non-empty")
    if len(request_digest) != 64 or any(c not in "0123456789abcdef" for c in request_digest):
        raise ValueError("atomic operation request digest must be lowercase SHA-256")
    stamp = time.time()
    rows = [(stamp, str(kind), encode_object(payload)) for kind, payload in events]
    if not rows:
        raise ValueError("atomic operation requires at least one mutation event")
    value = dict(response)
    response_json = encode_object(value)
    intent_json = encode_object(intent)
    return PreparedOperation(
        operation_key,
        request_digest,
        stamp,
        rows,
        value,
        response_json,
        intent_json,
        predecessor,
        ResponseBuilder(response_event_seq_field, finalize_response),
        max_retained_operations,
        stage_hook or (lambda _stage: None),
    )


def _existing(
    storage: StoreConnection, operation: PreparedOperation
) -> OperationCommitResult | None:
    """Return historical replay/conflict before renewed insertion conditions."""
    from synapse_channel.core.persistence import OperationCommitResult

    row = storage.connection.execute(
        (
            "SELECT operation_key, request_digest, response_json, response_sha256, "
            "first_event_seq, commit_seq, committed_at FROM operations WHERE "
            "operation_key = "
            "?"
        ),
        (operation.key,),
    ).fetchone()
    if row is None:
        return None
    storage.connection.rollback()
    stored = decode_operation(row)
    outcome: Literal["replayed", "conflict"] = (
        "replayed" if stored.request_digest == operation.digest else "conflict"
    )
    return OperationCommitResult(outcome, stored)


def _authorize_insertion(storage: StoreConnection, operation: PreparedOperation) -> None:
    """Check the frozen predecessor and non-evicting capacity inside the reservation."""
    if operation.predecessor is not None:
        key, digest, response, response_hash = operation.predecessor
        row = storage.connection.execute(
            (
                "SELECT request_digest, response_json, response_sha256 FROM operations "
                "WHERE operation_key = "
                "?"
            ),
            (key,),
        ).fetchone()
        if row is None or row[0] != digest or row[1] != response or row[2] != response_hash:
            raise ValueError("required predecessor operation mismatch")
    if operation.capacity is not None:
        count = storage.connection.execute("SELECT COUNT(*) FROM operations").fetchone()[0]
        if count >= operation.capacity:
            raise ValueError("retained operation budget exhausted")


def _insert_mutations(storage: StoreConnection, operation: PreparedOperation) -> tuple[int, ...]:
    """Insert every mutation and enrolled AEF intent before finalizing the response."""
    sequences = []
    for row in operation.rows:
        sequence = storage.insert_event(*row)
        sequences.append(sequence)
        if row[1] in storage.aef_kinds:
            storage.connection.execute(
                "INSERT INTO aef_outbox (legacy_seq, receipt_id) VALUES (?, NULL)", (sequence,)
            )
        operation.hook("after_legacy_event_insert")
    return tuple(sequences)


def _response(
    operation: PreparedOperation, sequences: tuple[int, ...]
) -> tuple[dict[str, Any], str, str]:
    """Apply the trusted builder to private JSON and hash the exact final bytes."""
    value, encoded = operation.response, operation.response_json
    if operation.builder.field is not None:
        value[operation.builder.field] = sequences[0]
        encoded = encode_object(value)
    elif operation.builder.finalize is not None:
        value = dict(operation.builder.finalize(json.loads(encoded), sequences))
        encoded = encode_object(value)
    return value, encoded, hashlib.sha256(encoded.encode("ascii")).hexdigest()


def _insert_completion(
    storage: StoreConnection, operation: PreparedOperation, sequences: tuple[int, ...]
) -> StoredOperation:
    """Insert compatibility replay, exact operation and projection intent together."""
    from synapse_channel.core.persistence import StoredOperation

    value, encoded, response_hash = _response(operation, sequences)
    compatibility = {
        "key": operation.key,
        "request_digest": operation.digest,
        "response": value,
        "response_sha256": response_hash,
        "first_event_seq": sequences[0],
        "commit_seq": sequences[-1] + 1,
    }
    commit_seq = storage.insert_event(operation.stamp, "idempotency", encode_object(compatibility))
    storage.connection.execute(
        (
            "INSERT INTO operations (operation_key, request_digest, response_json, "
            "response_sha256, first_event_seq, commit_seq, committed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)"
        ),
        (
            operation.key,
            operation.digest,
            encoded,
            response_hash,
            sequences[0],
            commit_seq,
            operation.stamp,
        ),
    )
    operation.hook("after_operation_insert")
    storage.connection.execute(
        (
            "INSERT INTO operation_outbox (operation_key, intent_json, receipt_id) "
            "VALUES (?, ?, NULL)"
        ),
        (operation.key, operation.intent_json),
    )
    operation.hook("after_operation_outbox_insert")
    return StoredOperation(
        operation.key,
        operation.digest,
        value,
        response_hash,
        sequences[0],
        commit_seq,
        operation.stamp,
    )


def commit_prepared(
    storage: StoreConnection, operation: PreparedOperation
) -> OperationCommitResult:
    """Own the one atomic boundary, preserving precommit and postcommit truth."""
    from synapse_channel.core.persistence import OperationCommitResult

    with storage.lock:
        storage.connection.execute("PRAGMA synchronous=FULL")
        committed = False
        try:
            storage.connection.execute("BEGIN IMMEDIATE")
            existing = _existing(storage, operation)
            if existing is not None:
                return existing
            _authorize_insertion(storage, operation)
            sequences = _insert_mutations(storage, operation)
            stored = _insert_completion(storage, operation, sequences)
            operation.hook("before_commit")
            storage.connection.commit()
            committed = True
            operation.hook("after_commit")
            return OperationCommitResult("inserted", stored)
        except BaseException:
            if not committed:
                storage.connection.rollback()
            raise
        finally:
            storage.restore_normal(committed)
