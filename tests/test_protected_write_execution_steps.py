# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — parent-bound recovery admission
from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterator, Mapping
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal, cast

import pytest

from synapse_channel.core.persistence import EventStore, OperationCommitResult, StoredOperation
from synapse_channel.core.protected_write_execution_journal import record_protected_execution_start
from synapse_channel.core.protected_write_execution_steps import record_protected_execution_step
from synapse_channel.core.protected_write_preparation import PreparedProtectedWrite
from test_protected_write_preparation import _begun

pytestmark = pytest.mark.skipif(os.name != "posix", reason="real POSIX preparation fixtures")

Fixture = tuple[PreparedProtectedWrite, EventStore, OperationCommitResult]
Phase = Literal["started", "completed", "failed", "unknown"]


@pytest.mark.parametrize("field", ["writer_principal", "writer_incarnation"])
def test_step_refuses_durable_consumption_for_another_writer(
    execution: Fixture, field: str
) -> None:
    prepared, service, consumed = execution
    response = json.loads(json.dumps(consumed.operation.response))
    response["evidence"][field] = "different-writer"
    foreign = service.commit_operation(
        operation_key="foreign-writer-consumption",
        request_digest="c" * 64,
        response=response,
        events=(("protected_write_execution_start", response["evidence"]),),
        intent={"family": "protected-execution-evidence"},
    )
    before = service.count()
    with pytest.raises(ValueError, match="matching newly consumed"):
        _step((prepared, service, foreign), foreign.operation, 0, "started")
    assert service.count() == before


@pytest.fixture
def execution(tmp_path: Path) -> Iterator[Fixture]:
    authority, prepared, state = _begun(tmp_path)
    service = EventStore(tmp_path / "steps.db")
    try:
        result = record_protected_execution_start(
            prepared,
            state.protected_write_reservations["reservation"],
            service_journal=service,
            max_journal_operations=16,
        )
        yield prepared, service, result
    finally:
        service.close()
        authority.close()


def _step(
    fixture: Fixture, predecessor: StoredOperation, index: int, phase: Phase
) -> OperationCommitResult:
    prepared, service, execution = fixture
    # Controlled verifier fixture: these records are not physical postcondition evidence.
    reference = json.loads(prepared.admission.proposal_bytes)["content_reference"]
    return record_protected_execution_step(
        prepared,
        execution,
        predecessor,
        step_index=index,
        phase=phase,
        service_journal=service,
        max_journal_operations=16,
        evidence_reference=reference if phase == "completed" else None,
        # Unit-only completed-step callback; activation needs immutable evidence readback and real
        # live postcondition.
        verify_completed=lambda _p, _i, _r: True,
    )


def test_complete_declared_sequence_and_replay(execution: Fixture, tmp_path: Path) -> None:
    prepared, service, consumed = execution
    previous = consumed.operation
    operations = json.loads(prepared.admission.proposal_bytes)["auxiliary_operations"]
    for index, operation in enumerate(operations):
        started = _step(execution, previous, index, "started")
        assert started.outcome == "inserted"
        assert started.operation.response["operation_id"] == operation["operation_id"]
        assert _step(execution, previous, index, "started").outcome == "replayed"
        previous = _step(execution, started.operation, index, "completed").operation
    assert len(service.read_operations()) == 1 + 2 * len(operations)
    reopened = EventStore(tmp_path / "steps.db")
    try:
        assert reopened.read_operations() == service.read_operations()
    finally:
        reopened.close()


@pytest.mark.parametrize("phase", ["failed", "unknown"])
def test_failure_or_unknown_stops_chain_and_cannot_be_rewritten(
    execution: Fixture, phase: Phase
) -> None:
    started = _step(execution, execution[2].operation, 0, "started")
    result = _step(execution, started.operation, 0, phase)
    assert result.outcome == "inserted"
    with pytest.raises(ValueError, match="predecessor"):
        _step(execution, result.operation, 1, "started")
    assert _step(execution, started.operation, 0, "completed").outcome == "conflict"
    assert len(execution[1].read_operations()) == 3


@pytest.mark.parametrize("index", [-1, True, 3, 0.0])
def test_invalid_index(execution: Fixture, index: object) -> None:
    with pytest.raises(ValueError, match="declared execution step"):
        _step(execution, execution[2].operation, cast(int, index), "started")


@pytest.mark.parametrize("budget", [0, True, 1.0, 2**53])
def test_invalid_budget(execution: Fixture, budget: object) -> None:
    prepared, service, consumed = execution
    with pytest.raises(ValueError, match="budget"):
        record_protected_execution_step(
            prepared,
            consumed,
            consumed.operation,
            step_index=0,
            phase="started",
            service_journal=service,
            max_journal_operations=cast(int, budget),
        )


def test_wrong_phase_and_skipped_step(execution: Fixture) -> None:
    with pytest.raises(ValueError, match="phase"):
        _step(execution, execution[2].operation, 0, cast(Phase, "retry"))
    with pytest.raises(ValueError, match="predecessor"):
        _step(execution, execution[2].operation, 1, "started")
    with pytest.raises(ValueError, match="predecessor"):
        _step(execution, execution[2].operation, 0, "completed")


@pytest.mark.parametrize("alteration", ["replay", "state", "evidence", "reservation", "digest"])
def test_unmatched_or_corrupt_consumption(execution: Fixture, alteration: str) -> None:
    prepared, service, consumed = execution
    if alteration == "replay":
        consumed = consumed._replace(outcome="replayed")
    else:
        response = json.loads(json.dumps(consumed.operation.response))
        if alteration == "state":
            response["state"] = "other"
        elif alteration == "evidence":
            response["evidence"] = None
        elif alteration == "reservation":
            response["evidence"]["reservation_id"] = "other"
        else:
            consumed = consumed._replace(
                operation=consumed.operation._replace(response_sha256="a" * 64)
            )
        consumed = consumed._replace(operation=consumed.operation._replace(response=response))
    with pytest.raises(ValueError, match="consumed execution|integrity"):
        record_protected_execution_step(
            prepared,
            consumed,
            consumed.operation,
            step_index=0,
            phase="started",
            service_journal=service,
            max_journal_operations=16,
        )


def test_first_step_requires_exact_start_and_intact_predecessor(execution: Fixture) -> None:
    original = execution[2].operation
    with pytest.raises(ValueError, match="exact execution"):
        _step(execution, original._replace(operation_key="foreign"), 0, "started")
    with pytest.raises(ValueError, match="integrity"):
        _step(execution, original._replace(response_sha256="b" * 64), 0, "started")


@pytest.mark.parametrize(
    "mode", ["missing_reference", "missing_verifier", "false", "truthy", "started_reference"]
)
def test_result_requires_exact_verified_evidence(execution: Fixture, mode: str) -> None:
    prepared, service, consumed = execution
    started = _step(execution, consumed.operation, 0, "started")
    reference = json.loads(prepared.admission.proposal_bytes)["content_reference"]
    with pytest.raises(ValueError, match="evidence"):
        record_protected_execution_step(
            prepared,
            consumed,
            consumed.operation if mode == "started_reference" else started.operation,
            step_index=0,
            phase="started" if mode == "started_reference" else "completed",
            service_journal=service,
            max_journal_operations=16,
            evidence_reference=None if mode == "missing_reference" else reference,
            verify_completed=None
            if mode == "missing_verifier"
            else lambda _p, _i, _r: cast(bool, 1 if mode == "truthy" else False),
        )
    assert len(service.read_operations()) == 2


def test_completed_verifier_cannot_mutate_record_reference(execution: Fixture) -> None:
    prepared, service, consumed = execution
    started = _step(execution, consumed.operation, 0, "started")
    reference = json.loads(prepared.admission.proposal_bytes)["content_reference"]

    def verify(_prepared: PreparedProtectedWrite, _index: int, proof: Mapping[str, object]) -> bool:
        with pytest.raises(TypeError):
            cast(dict[str, object], proof)["handle"] = "changed"
        return True

    result = record_protected_execution_step(
        prepared,
        consumed,
        started.operation,
        step_index=0,
        phase="completed",
        service_journal=service,
        max_journal_operations=16,
        evidence_reference=reference,
        verify_completed=verify,
    )
    assert result.operation.response["evidence_reference"] == reference


def test_forged_success_cannot_satisfy_atomic_predecessor(execution: Fixture) -> None:
    started = _step(execution, execution[2].operation, 0, "started")
    failed = _step(execution, started.operation, 0, "failed").operation
    forged = dict(failed.response, phase="completed")
    digest = hashlib.sha256(
        json.dumps(forged, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    ).hexdigest()
    with pytest.raises(ValueError, match="predecessor"):
        _step(
            execution,
            failed._replace(response=forged, response_sha256=digest, request_digest=digest),
            1,
            "started",
        )
    assert len(execution[1].read_operations()) == 3


def test_step_budget_does_not_evict_consumption(execution: Fixture) -> None:
    prepared, service, consumed = execution
    with pytest.raises(ValueError, match="budget exhausted"):
        record_protected_execution_step(
            prepared,
            consumed,
            consumed.operation,
            step_index=0,
            phase="started",
            service_journal=service,
            max_journal_operations=1,
        )
    remaining = service.read_operations()
    assert len(remaining) == 1
    assert remaining[0].key == consumed.operation.operation_key
    assert remaining[0].request_digest == consumed.operation.request_digest
    assert remaining[0].response == consumed.operation.response


def test_two_connections_only_one_new_step_attempt(execution: Fixture, tmp_path: Path) -> None:
    prepared, service, consumed = execution
    second = EventStore(tmp_path / "steps.db")
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(
                pool.map(
                    lambda journal: (
                        _step(
                            (prepared, journal, consumed), consumed.operation, 0, "started"
                        ).outcome
                    ),
                    (service, second),
                )
            )
        assert sorted(outcomes) == ["inserted", "replayed"]
        assert len(service.read_operations()) == 2
    finally:
        second.close()


def test_reopened_consumption_never_authorizes_new_steps(tmp_path: Path) -> None:
    authority, prepared, state = _begun(tmp_path)
    path = tmp_path / "restart.db"
    service = EventStore(path)
    begun = state.protected_write_reservations["reservation"]
    try:
        consumed = record_protected_execution_start(
            prepared,
            begun,
            service_journal=service,
            max_journal_operations=16,
        )
        assert consumed.outcome == "inserted"
        _step((prepared, service, consumed), consumed.operation, 0, "started")
    finally:
        service.close()
        authority.close()
    reopened = EventStore(path)
    try:
        replayed = record_protected_execution_start(
            prepared,
            begun,
            service_journal=reopened,
            max_journal_operations=16,
        )
        assert replayed.outcome == "replayed"
        with pytest.raises(ValueError, match="newly consumed"):
            _step((prepared, reopened, replayed), replayed.operation, 0, "started")
        assert len(reopened.read_operations()) == 2
    finally:
        reopened.close()


@pytest.mark.parametrize("field", ["index_type", "key", "digest"])
def test_predecessor_requires_exact_typed_step_identity(execution: Fixture, field: str) -> None:
    started = _step(execution, execution[2].operation, 0, "started").operation
    if field == "index_type":
        response = dict(started.response, step_index=False)
        digest = hashlib.sha256(
            json.dumps(response, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
                "ascii"
            )
        ).hexdigest()
        started = started._replace(response=response, response_sha256=digest, request_digest=digest)
    elif field == "key":
        started = started._replace(operation_key="foreign-step")
    else:
        started = started._replace(request_digest="a" * 64)
    with pytest.raises(ValueError, match="predecessor"):
        _step(execution, started, 0, "completed")
    assert len(execution[1].read_operations()) == 2
