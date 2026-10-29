# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — retained execution acceptance and completion records
"""Record one-time acceptance and verify exact retained execution chains."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from synapse_channel.core.persistence import EventStore, OperationCommitResult, StoredOperation
from synapse_channel.core.persistence_operation_keys import read_operation_keys
from synapse_channel.core.protected_write_journal import protected_operation_response
from synapse_channel.core.protected_write_lifecycle import ProtectedWriteReservation
from synapse_channel.core.protected_write_operations import (
    validate_protected_write_content_reference,
)
from synapse_channel.core.protected_write_preparation import PreparedProtectedWrite


def record_protected_execution_start(
    prepared: PreparedProtectedWrite,
    begun: ProtectedWriteReservation,
    *,
    service_journal: EventStore,
    max_journal_operations: int,
) -> OperationCommitResult:
    """Consume an exact verified begin once without minting a grant or executing it.

    Parameters
    ----------
    prepared:
        Complete mandatory preparation result.
    begun:
        Exact reservation just returned by verify_prepared_begin under current trust.
    service_journal:
        Separately operator-enrolled execution-evidence store, never the Core grant
        journal. Its database/sidecar paths and ownership must be enrolled before use.
    max_journal_operations:
        Positive total retained operation cap, including execution and step records;
        no deletion or eviction on overflow.

    Returns
    -------
    OperationCommitResult
        Only inserted lets this invocation proceed to the declared executor.
        Replayed means already consumed: never execute again, even after a crash.
        Conflict means reservation identity was reused with different begin evidence.

    Raises
    ------
    ValueError
        On inconsistent preparation/begin or exhausted/invalid history budget.

    Notes
    -----
    This records execution evidence, not authority. A crash after this record but
    before any syscall leaves an uncertain attempt requiring reconciliation, not
    permission to retry. No automatic recovery, cleanup, lease renewal or writer
    syscall is performed. EventStore's existing FULL atomic transaction is reused.
    """
    if type(max_journal_operations) is not int or not 0 < max_journal_operations < 2**53:
        raise ValueError("invalid retained execution budget")
    key, digest, evidence = _execution_identity(prepared, begun)
    return service_journal.commit_operation(
        operation_key=key,
        request_digest=digest,
        response={"state": "execution_consumed", "evidence": evidence},
        events=(("protected_write_execution_start", evidence),),
        intent={"family": "protected-execution-evidence"},
        max_retained_operations=max_journal_operations,
    )


def _execution_identity(
    prepared: PreparedProtectedWrite, begun: ProtectedWriteReservation
) -> tuple[str, str, dict[str, object]]:
    source = json.loads(begun.request_bytes)
    body = json.loads(begun.result_bytes)["body"]
    admitted = json.loads(prepared.admission.result_bytes)["body"]
    if (
        begun.admission != prepared.admission
        or source["type"] != "protected_write_begin"
        or body["operation_phase"] != "executing"
        or body["revocation_phase"] != "open"
        or body["begin_sequence"] != begun.transition_sequence
        or prepared.content.proposal_sha256 != source["proposal_sha256"]
        or body["writer_principal"] != admitted["writer_principal"]
        or body["writer_incarnation"] != admitted["writer_incarnation"]
    ):
        raise ValueError("execution start requires exact prepared begin")
    identity = {
        "domain": "synapse-protected-write.execution.v1",
        "authority_id": source["authority_id"],
        "authority_continuity": source["authority_continuity"],
        "reservation_id": begun.admission.reservation_id,
    }
    key = (
        "protected-execution:"
        + hashlib.sha256(
            json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
                "ascii"
            )
        ).hexdigest()
    )
    evidence = {
        **identity,
        "proposal_sha256": prepared.content.proposal_sha256,
        "writer_principal": body["writer_principal"],
        "writer_incarnation": body["writer_incarnation"],
        "begin_sequence": begun.transition_sequence,
        "begin_request_sha256": hashlib.sha256(begun.request_bytes).hexdigest(),
        "begin_result_sha256": hashlib.sha256(begun.result_bytes).hexdigest(),
    }
    digest = hashlib.sha256(
        json.dumps(evidence, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
            "ascii"
        )
    ).hexdigest()
    return key, digest, evidence


def _canonical(value: Mapping[str, object]) -> bytes:
    return json.dumps(
        dict(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")


def _retained(store: EventStore, key: str, *, after_sequence: int) -> StoredOperation:
    operation = store.get_operation(key)
    if operation is None or operation.first_event_seq <= after_sequence:
        raise ValueError("execution record is missing or out of order")
    protected_operation_response(
        store,
        operation_key=key,
        request_digest=operation.request_digest,
        mutation_sequence=operation.first_event_seq,
    )
    return operation


@dataclass(frozen=True)
class ProtectedExecutionFrontier:
    """Verified durable prefix and uncertain next step, never a retry permit.

    Attributes
    ----------
    phase : str
        Exact missing, started, failed, unknown or complete frontier category.
    step_index : int
        First uncompleted zero-based step, or operation count when complete.
    start : StoredOperation
        Verified one-time consumption of the exact admitted begin.
    completed : tuple of StoredOperation
        Ordered completed step results before the frontier.
    terminal : StoredOperation or None
        Retained started/failed/unknown record, absent at missing/complete states.
    """

    phase: Literal["before_step", "started", "failed", "unknown", "complete"]
    step_index: int
    start: StoredOperation
    completed: tuple[StoredOperation, ...]
    terminal: StoredOperation | None


def _step_record(
    store: EventStore,
    key: str,
    *,
    after_sequence: int,
    start: StoredOperation,
    previous: StoredOperation,
    index: int,
    operation_id: str,
    phase: Literal["started", "completed", "failed", "unknown"],
    prepared: PreparedProtectedWrite,
) -> StoredOperation:
    """Validate one exact step and its committed mutation event."""
    record = _retained(store, key, after_sequence=after_sequence)
    reference = record.response.get("evidence_reference")
    body = {
        "state": "execution_step",
        "execution_start_sha256": start.response_sha256,
        "step_index": index,
        "operation_id": operation_id,
        "phase": phase,
        "evidence_reference": reference,
        "predecessor_response_sha256": previous.response_sha256,
        "predecessor_request_digest": previous.request_digest,
    }
    encoded = _canonical(body)
    if (
        _canonical(record.response) != encoded
        or record.request_digest != hashlib.sha256(encoded).hexdigest()
        or (phase != "completed" and reference is not None)
        or (phase == "completed" and not isinstance(reference, dict))
    ):
        raise ValueError("execution step is not exact successful continuation")
    if phase == "completed":
        validate_protected_write_content_reference(
            reference, limits=prepared.policy.limits.operation_limits
        )
    event = store.latest_at_or_before(record.first_event_seq)
    if (
        event is None
        or event.seq != record.first_event_seq
        or event.kind != "protected_write_execution_step"
        or _canonical(event.payload) != encoded
    ):
        raise ValueError("execution step event differs from retained operation")
    return record


def _reject_later_steps(store: EventStore, key: str, index: int, count: int) -> None:
    """Refuse records past the first missing or terminal step."""
    for later in range(index, count + 1):
        for slot in ("start", "result"):
            if store.get_operation(f"{key}/step/{later}/{slot}") is not None:
                raise ValueError("execution contains a step beyond its durable frontier")


def _require_execution_namespace(store: EventStore, key: str, count: int) -> None:
    """Refuse undeclared children, indices, slots and aliases in this execution namespace."""
    prefix = key + "/"
    keys = read_operation_keys(
        store, prefix, max_entries=2 * count + 1, max_key_bytes=4 * (len(prefix) + 32)
    )
    allowed = {
        f"{prefix}step/{index}/{slot}" for index in range(count) for slot in ("start", "result")
    }
    if not set(keys) <= allowed:
        raise ValueError("execution contains a step beyond its durable frontier")


def _verified_execution_start(
    prepared: PreparedProtectedWrite,
    begun: ProtectedWriteReservation,
    service_journal: EventStore,
) -> tuple[str, StoredOperation]:
    """Bind the committed consumption event to its exact immutable begin evidence."""
    key, digest, evidence = _execution_identity(prepared, begun)
    start = _retained(service_journal, key, after_sequence=0)
    expected = {"state": "execution_consumed", "evidence": evidence}
    if start.request_digest != digest or _canonical(start.response) != _canonical(expected):
        raise ValueError("retained execution differs from exact begin")
    event = service_journal.latest_at_or_before(start.first_event_seq)
    if (
        event is None
        or event.seq != start.first_event_seq
        or event.kind != "protected_write_execution_start"
        or _canonical(event.payload) != _canonical(evidence)
    ):
        raise ValueError("execution start event differs from retained operation")
    return key, start


def inspect_protected_execution_frontier(
    prepared: PreparedProtectedWrite,
    begun: ProtectedWriteReservation,
    *,
    service_journal: EventStore,
) -> ProtectedExecutionFrontier:
    """Read a complete or interrupted exact execution chain without mutation.

    Parameters
    ----------
    prepared:
        Trusted immutable preparation for the exact admitted proposal.
    begun:
        Retained authority begin whose request/result digests bind consumption.
    service_journal:
        Enrolled existing execution journal, read outside the authority lock.

    Returns
    -------
    ProtectedExecutionFrontier
        Exact completed prefix and first missing or terminal step.

    Raises
    ------
    ValueError
        On missing consumption, damaged atomic records, identity/sequence/event
        mismatch, unsupported phase or records past the first interruption.

    Notes
    -----
    A missing start/step or terminal failed/unknown step is still uncertain until
    a separate supervisor proves the old writer exited and an operator compares
    actual bytes with the bounded parent plan. This function grants no retry.
    """
    key, start = _verified_execution_start(prepared, begun, service_journal)
    operations = json.loads(prepared.admission.proposal_bytes)["auxiliary_operations"]
    _require_execution_namespace(service_journal, key, len(operations))
    completed: list[StoredOperation] = []
    previous = start
    for index, operation in enumerate(operations):
        start_key = f"{key}/step/{index}/start"
        if service_journal.get_operation(start_key) is None:
            _reject_later_steps(service_journal, key, index, len(operations))
            return ProtectedExecutionFrontier("before_step", index, start, tuple(completed), None)
        started = _step_record(
            service_journal,
            start_key,
            after_sequence=previous.commit_seq,
            start=start,
            previous=previous,
            index=index,
            operation_id=operation["operation_id"],
            phase="started",
            prepared=prepared,
        )
        result_key = f"{key}/step/{index}/result"
        result = service_journal.get_operation(result_key)
        if result is None:
            _reject_later_steps(service_journal, key, index + 1, len(operations))
            return ProtectedExecutionFrontier("started", index, start, tuple(completed), started)
        phase = result.response.get("phase")
        if type(phase) is not str or phase not in {"completed", "failed", "unknown"}:
            raise ValueError("execution result phase is unsupported")
        result_phase: Literal["completed", "failed", "unknown"]
        if phase == "completed":
            result_phase = "completed"
        elif phase == "failed":
            result_phase = "failed"
        else:
            result_phase = "unknown"
        verified = _step_record(
            service_journal,
            result_key,
            after_sequence=started.commit_seq,
            start=start,
            previous=started,
            index=index,
            operation_id=operation["operation_id"],
            phase=result_phase,
            prepared=prepared,
        )
        if result_phase != "completed":
            _reject_later_steps(service_journal, key, index + 1, len(operations))
            return ProtectedExecutionFrontier(
                result_phase, index, start, tuple(completed), verified
            )
        completed.append(verified)
        previous = verified
    _reject_later_steps(service_journal, key, len(operations), len(operations))
    return ProtectedExecutionFrontier("complete", len(operations), start, tuple(completed), None)


def verify_protected_execution_records(
    prepared: PreparedProtectedWrite,
    begun: ProtectedWriteReservation,
    *,
    service_journal: EventStore,
) -> tuple[StoredOperation, ...]:
    """Read an exact complete execution chain from the retained service journal.

    Parameters
    ----------
    prepared:
        Independently verified immutable preparation, never client metadata.
    begun:
        Exact trusted begin whose request/result digests bind the consumption.
    service_journal:
        Enrolled existing service journal; this operation performs no writes.

    Returns
    -------
    tuple[StoredOperation, ...]
        Consumption followed by every ordered completed result. These are
        journal records, not a settlement, active-memory receipt or capability.

    Raises
    ------
    ValueError
        On absent/partial/unknown execution, changed identity, broken predecessor,
        incomplete atomic commit or mutation-event mismatch.

    Notes
    -----
    The caller must separately verify referenced observation bytes, actual
    postconditions, namespace custody and writer quiescence. A complete journal
    is necessary but insufficient for committed settlement. An authenticated
    request, this tuple or a past begin does not grant permission to reexecute.
    Budgets come from the already parsed enrolled proposal, not a journal scan.
    Concurrent unrelated journal operations may interleave without invalidating
    the exact execution chain. Journal ownership is an external prerequisite.
    """
    frontier = inspect_protected_execution_frontier(
        prepared, begun, service_journal=service_journal
    )
    if frontier.phase != "complete":
        raise ValueError("execution chain is incomplete or uncertain")
    return frontier.start, *frontier.completed
