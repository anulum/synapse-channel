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

from synapse_channel.core.persistence import EventStore, OperationCommitResult, StoredOperation
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
    previous = start
    completed = [start]
    operations = json.loads(prepared.admission.proposal_bytes)["auxiliary_operations"]
    for index, operation in enumerate(operations):
        for phase, slot in (("started", "start"), ("completed", "result")):
            record = _retained(
                service_journal, f"{key}/step/{index}/{slot}", after_sequence=previous.commit_seq
            )
            body = record.response
            reference = body.get("evidence_reference")
            expected_body = {
                "state": "execution_step",
                "execution_start_sha256": start.response_sha256,
                "step_index": index,
                "operation_id": operation["operation_id"],
                "phase": phase,
                "evidence_reference": reference,
                "predecessor_response_sha256": previous.response_sha256,
                "predecessor_request_digest": previous.request_digest,
            }
            encoded = _canonical(expected_body)
            if (
                _canonical(body) != encoded
                or record.request_digest != hashlib.sha256(encoded).hexdigest()
                or (phase == "started" and reference is not None)
                or (phase == "completed" and not isinstance(reference, dict))
            ):
                raise ValueError("execution step is not exact successful continuation")
            if phase == "completed":
                validate_protected_write_content_reference(
                    reference, limits=prepared.policy.limits.operation_limits
                )
            event = service_journal.latest_at_or_before(record.first_event_seq)
            if (
                event is None
                or event.seq != record.first_event_seq
                or event.kind != "protected_write_execution_step"
                or _canonical(event.payload) != encoded
            ):
                raise ValueError("execution step event differs from retained operation")
            previous = record
        completed.append(previous)
    return tuple(completed)
