# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — parent-bound recovery admission
"""Durable success-only execution step ordering; no filesystem mutation."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import Literal

from synapse_channel.core.atomic_operations import OperationRecord
from synapse_channel.core.persistence import EventStore, OperationCommitResult, StoredOperation
from synapse_channel.core.protected_write_operations import (
    validate_protected_write_content_reference,
)
from synapse_channel.core.protected_write_preparation import PreparedProtectedWrite


def _canonical(value: Mapping[str, object]) -> str:
    return json.dumps(
        dict(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )


def record_protected_execution_step(
    prepared: PreparedProtectedWrite,
    execution: OperationCommitResult,
    predecessor: StoredOperation,
    *,
    step_index: int,
    phase: Literal["started", "completed", "failed", "unknown"],
    service_journal: EventStore,
    max_journal_operations: int,
    evidence_reference: Mapping[str, object] | None = None,
    verify_completed: Callable[[PreparedProtectedWrite, int, Mapping[str, object]], bool]
    | None = None,
) -> OperationCommitResult:
    """Record one attempted step or its exact verified outcome.

    Parameters
    ----------
    prepared:
        Complete immutable writer input.
    execution:
        This live invocation's newly inserted execution-consumption result.
        A replayed consumption never permits a new step after restart.
    predecessor:
        Exact prior completed operation from this same service journal.
    step_index:
        Zero-based index in the declared auxiliary sequence.
    phase:
        started before syscall; completed only after trusted observed success.
        failed/unknown are terminal outcomes, never predecessors for another step.
    service_journal:
        Separately enrolled execution-evidence journal, not authority ledger.
    max_journal_operations:
        Positive retained operation budget including start AND all step records.
    evidence_reference:
        Enrolled immutable result-evidence reference, absent for started records.
    verify_completed:
        Trusted local verifier of exact operation postconditions/evidence.
        Required for completed, must return exactly True, no external I/O.

    Returns
    -------
    OperationCommitResult
        Only a newly inserted started record lets this invocation attempt its
        syscall once. Replayed records are observations, never reexecution permits.

    Raises
    ------
    ValueError
        On reused consumption, wrong index/phase/predecessor, proof or budget.

    Notes
    -----
    A crash after started is unknown, not permission to retry. Failed/unknown
    results stop the chain and preserve files for independent recovery. The
    atomic predecessor condition prevents partial insertion or stale sequencing.
    No completion/quiescence receipt, cleanup or actual syscall is generated here.
    """
    if type(max_journal_operations) is not int or not 0 < max_journal_operations < 2**53:
        raise ValueError("invalid execution journal budget")
    operations = json.loads(prepared.admission.proposal_bytes)["auxiliary_operations"]
    if type(step_index) is not int or not 0 <= step_index < len(operations):
        raise ValueError("invalid declared execution step")
    if phase not in ("started", "completed", "failed", "unknown"):
        raise ValueError("invalid execution step phase")
    start = execution.operation
    start_body = start.response
    origin = json.loads(prepared.admission.request_bytes)
    admitted = json.loads(prepared.admission.result_bytes)["body"]
    evidence = start_body.get("evidence", {})
    if (
        execution.outcome != "inserted"
        or start_body.get("state") != "execution_consumed"
        or not isinstance(evidence, dict)
        or evidence.get("reservation_id") != prepared.admission.reservation_id
        or evidence.get("proposal_sha256") != prepared.content.proposal_sha256
        or evidence.get("authority_id") != origin["authority_id"]
        or evidence.get("authority_continuity") != origin["authority_continuity"]
        or evidence.get("writer_principal") != admitted["writer_principal"]
        or evidence.get("writer_incarnation") != admitted["writer_incarnation"]
    ):
        raise ValueError("step requires matching newly consumed execution")
    start_sha = hashlib.sha256(_canonical(start_body).encode("ascii")).hexdigest()
    predecessor_sha = hashlib.sha256(_canonical(predecessor.response).encode("ascii")).hexdigest()
    if start.response_sha256 != start_sha or predecessor.response_sha256 != predecessor_sha:
        raise ValueError("execution operation evidence integrity mismatch")
    if phase == "started" and step_index == 0:
        if (
            predecessor.operation_key != start.operation_key
            or predecessor.request_digest != start.request_digest
            or _canonical(predecessor.response) != _canonical(start_body)
        ):
            raise ValueError("first step requires exact execution consumption")
    else:
        previous = predecessor.response
        expected_index = step_index - 1 if phase == "started" else step_index
        expected_phase = "completed" if phase == "started" else "started"
        expected_slot = "result" if phase == "started" else "start"
        if (
            previous.get("state") != "execution_step"
            or previous.get("execution_start_sha256") != start_sha
            or type(previous.get("step_index")) is not int
            or previous.get("step_index") != expected_index
            or previous.get("phase") != expected_phase
            or previous.get("operation_id") != operations[expected_index]["operation_id"]
            or predecessor.operation_key
            != f"{start.operation_key}/step/{expected_index}/{expected_slot}"
            or predecessor.request_digest != predecessor_sha
        ):
            raise ValueError("execution step predecessor is not the required successful state")
    reference = None if evidence_reference is None else json.loads(_canonical(evidence_reference))
    if reference is not None:
        validate_protected_write_content_reference(
            reference, limits=prepared.policy.limits.operation_limits
        )
    if phase == "started" and reference is not None:
        raise ValueError("started step cannot claim result evidence")
    if phase == "completed" and (
        reference is None
        or verify_completed is None
        or verify_completed(prepared, step_index, MappingProxyType(reference)) is not True
    ):
        raise ValueError("completed step requires verified observed result evidence")
    body = {
        "state": "execution_step",
        "execution_start_sha256": start_sha,
        "step_index": step_index,
        "operation_id": operations[step_index]["operation_id"],
        "phase": phase,
        "evidence_reference": reference,
        "predecessor_response_sha256": predecessor_sha,
        "predecessor_request_digest": predecessor.request_digest,
    }
    slot = "start" if phase == "started" else "result"
    key = f"{start.operation_key}/step/{step_index}/{slot}"
    digest = hashlib.sha256(_canonical(body).encode("ascii")).hexdigest()
    return service_journal.commit_operation(
        operation_key=key,
        request_digest=digest,
        response=body,
        events=(("protected_write_execution_step", body),),
        intent={"family": "protected-execution-evidence"},
        max_retained_operations=max_journal_operations,
        required_predecessor=OperationRecord(
            predecessor.operation_key, predecessor.request_digest, predecessor.response
        ),
    )
