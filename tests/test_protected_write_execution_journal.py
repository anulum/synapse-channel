# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — execution acceptance and retained-chain regressions
"""Durable writer-step tests and their physical-acceptance limits.

The positive ``authorize_current=True`` and ``verify_isolation=True`` seams in
this module, ``test_protected_write_driver.py`` and
``test_protected_write_preparation.py`` exercise storage ordering only. An
activated service needs actor-consistent current-begin lookup and a separate
supervised writer process. The positive quiescence and recovery callbacks in
``test_protected_write_transition_journal.py``,
``test_protected_write_recovery.py`` and
``test_protected_write_recovery_journal.py`` do not prove descendant, descriptor,
file-closure or interrupted-writer fencing. The positive completion callback in
``test_protected_write_execution_steps.py`` likewise requires immutable evidence
readback and live postcondition proof.

Injected syscall/helper failures in ``test_protected_write_file_execution.py``,
``test_protected_write_storage_layout.py``,
``test_protected_write_inspection.py``, ``test_protected_write_descriptors.py``
and ``test_protected_write_driver.py`` are narrow fault checks. Activation needs
real process interruption, permission and descriptor races, storage exhaustion
and durable recovery under the disposable operator sandbox. The five uncovered
branches in ``protected_write_execution_observations.py`` require actual retained
evidence cases or a reviewed invariant; this suite does not claim 100% coverage.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from synapse_channel.core.persistence import EventStore, StoredOperation
from synapse_channel.core.protected_write_driver import execute_prepared_protected_write
from synapse_channel.core.protected_write_evidence_store import ProtectedExecutionEvidenceStore
from synapse_channel.core.protected_write_execution_journal import (
    record_protected_execution_start,
    verify_protected_execution_records,
)
from synapse_channel.core.protected_write_lifecycle import ProtectedWriteReservation
from synapse_channel.core.protected_write_preparation import (
    PreparedProtectedWrite,
    verify_prepared_begin,
)
from test_protected_write_admission import CONTEXT
from test_protected_write_preparation import _begun


@pytest.fixture
def completed_execution(
    tmp_path: Path,
) -> Iterator[
    tuple[
        PreparedProtectedWrite, ProtectedWriteReservation, EventStore, tuple[StoredOperation, ...]
    ]
]:
    authority, prepared, state = _begun(tmp_path)
    service = EventStore(tmp_path / "completed.db")
    evidence = ProtectedExecutionEvidenceStore(
        service, "text", prepared.policy.limits.operation_limits, 4096, 64
    )
    descriptor = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)

    def current() -> ProtectedWriteReservation:
        return verify_prepared_begin(
            prepared,
            state=state,
            store=authority,
            authenticated_writer=(CONTEXT.writer_principal, CONTEXT.writer_incarnation),
            # Unit-only positive policy callback; activation needs actor-consistent current begin
            # and trust lookup.
            authorize_current=lambda _p, _r: True,
        )

    try:
        begun = current()
        results = execute_prepared_protected_write(
            prepared,
            root_descriptors={"memory": descriptor, "parent": descriptor},
            owner_uid=os.getuid(),
            service_journal=service,
            max_journal_operations=64,
            max_locks=8,
            max_evidence_bytes=4096,
            verify_current=current,
            # Unit-only positive isolation callback; activation needs a supervised
            # separate-principal writer and real namespace checks.
            verify_isolation=lambda: True,
            store_evidence=evidence.write,
            read_evidence=evidence.read,
        )
        yield prepared, begun, service, tuple(r.operation for r in results)
    finally:
        os.close(descriptor)
        service.close()
        authority.close()


def test_verify_complete_execution_reads_without_writing(
    completed_execution: tuple[
        PreparedProtectedWrite, ProtectedWriteReservation, EventStore, tuple[StoredOperation, ...]
    ],
    tmp_path: Path,
) -> None:
    prepared, begun, service, expected = completed_execution
    before = service.read_all()
    assert verify_protected_execution_records(prepared, begun, service_journal=service) == expected
    assert service.read_all() == before
    reopened = EventStore(tmp_path / "completed.db")
    try:
        assert (
            verify_protected_execution_records(prepared, begun, service_journal=reopened)
            == expected
        )
    finally:
        reopened.close()


@pytest.mark.parametrize(
    "case",
    [
        "missing",
        "order",
        "begin",
        "start-event",
        "step-event",
        "unknown",
        "predecessor",
        "reference",
        "extra",
    ],
)
def test_verify_complete_execution_refuses_changed_retained_chain(
    completed_execution: tuple[
        PreparedProtectedWrite, ProtectedWriteReservation, EventStore, tuple[StoredOperation, ...]
    ],
    tmp_path: Path,
    case: str,
) -> None:
    prepared, begun, service, records = completed_execution
    start, result = records[0], records[1]
    with sqlite3.connect(tmp_path / "completed.db") as database:
        if case == "missing":
            database.execute(
                "DELETE FROM operations WHERE operation_key=?", (result.operation_key,)
            )
        elif case == "order":
            database.execute(
                "UPDATE operations SET first_event_seq=0 WHERE operation_key=?",
                (result.operation_key,),
            )
        elif case == "begin":
            value = json.loads(begun.result_bytes)
            value["timestamp"] += 1.0
            begun = replace(begun, result_bytes=json.dumps(value).encode())
        elif case in {"start-event", "step-event"}:
            sequence = start.first_event_seq if case == "start-event" else result.first_event_seq
            database.execute("UPDATE events SET kind='changed' WHERE seq=?", (sequence,))
        else:
            body = dict(result.response)
            if case == "unknown":
                body["phase"] = "unknown"
            elif case == "predecessor":
                body["predecessor_request_digest"] = "0" * 64
            elif case == "reference":
                body["evidence_reference"] = None
            else:
                body["extra"] = True
            raw = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
            digest = hashlib.sha256(raw.encode("ascii")).hexdigest()
            marker = service.latest_at_or_before(result.commit_seq)
            assert marker is not None
            payload = {
                **marker.payload,
                "request_digest": digest,
                "response_sha256": digest,
                "response": body,
            }
            database.execute(
                "UPDATE operations SET response_json=?, response_sha256=?, request_digest=? "
                "WHERE operation_key=?",
                (raw, digest, digest, result.operation_key),
            )
            database.execute(
                "UPDATE events SET payload=? WHERE seq=?", (raw, result.first_event_seq)
            )
            database.execute(
                "UPDATE events SET payload=? WHERE seq=?",
                (json.dumps(payload), result.commit_seq),
            )
    with pytest.raises(ValueError):
        verify_protected_execution_records(prepared, begun, service_journal=service)


@pytest.mark.parametrize("field", ["writer_principal", "writer_incarnation"])
def test_execution_consumption_refuses_changed_writer(tmp_path: Path, field: str) -> None:
    authority, prepared, state = _begun(tmp_path)
    service = EventStore(tmp_path / "changed-writer.db")
    begun = state.protected_write_reservations["reservation"]
    changed = json.loads(begun.result_bytes)
    changed["body"][field] = "different-writer"
    try:
        with pytest.raises(ValueError, match="exact prepared begin"):
            record_protected_execution_start(
                prepared,
                replace(begun, result_bytes=json.dumps(changed).encode()),
                service_journal=service,
                max_journal_operations=16,
            )
        assert service.count() == 0
    finally:
        service.close()
        authority.close()


pytestmark = pytest.mark.skipif(os.name != "posix", reason="real prepared descriptor fixtures")


def test_two_service_connections_consume_one_begin_once_and_reopen(tmp_path: Path) -> None:
    authority, prepared, state = _begun(tmp_path)
    begun = verify_prepared_begin(
        prepared,
        state=state,
        store=authority,
        authenticated_writer=(CONTEXT.writer_principal, CONTEXT.writer_incarnation),
        # Unit-only positive policy callback; activation needs actor-consistent current begin and
        # trust lookup.
        authorize_current=lambda _p, _r: True,
    )
    path = tmp_path / "execution.db"
    stores = (EventStore(path), EventStore(path))
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(
                pool.map(
                    lambda store: (
                        record_protected_execution_start(
                            prepared, begun, service_journal=store, max_journal_operations=1
                        ).outcome
                    ),
                    stores,
                )
            )
        assert sorted(outcomes) == ["inserted", "replayed"]
        assert stores[0].count() == 2
        assert authority.count() == 5
    finally:
        for store in stores:
            store.close()
        authority.close()
    reopened = EventStore(path)
    try:
        result = record_protected_execution_start(
            prepared, begun, service_journal=reopened, max_journal_operations=1
        )
        assert result.outcome == "replayed"
        assert result.operation.response["state"] == "execution_consumed"
        altered = json.loads(begun.result_bytes)
        altered["timestamp"] += 1.0
        changed = replace(begun, result_bytes=json.dumps(altered).encode())
        assert (
            record_protected_execution_start(
                prepared, changed, service_journal=reopened, max_journal_operations=1
            ).outcome
            == "conflict"
        )
    finally:
        reopened.close()


@pytest.mark.parametrize("budget", [None, True, 0, -1, 1.0, 2**53])
def test_execution_requires_positive_explicit_budget(tmp_path: Path, budget: object) -> None:
    authority, prepared, state = _begun(tmp_path)
    service = EventStore(tmp_path / "execution.db")
    try:
        with pytest.raises(ValueError, match="budget"):
            record_protected_execution_start(
                prepared,
                state.protected_write_reservations["reservation"],
                service_journal=service,
                max_journal_operations=cast(int, budget),
            )
        assert service.count() == 0
    finally:
        authority.close()
        service.close()


def test_start_refuses_an_admission_without_current_begin(tmp_path: Path) -> None:
    authority, prepared, state = _begun(tmp_path)
    service = EventStore(tmp_path / "execution.db")
    current = state.protected_write_reservations["reservation"]
    try:
        with pytest.raises(ValueError, match="exact prepared begin"):
            record_protected_execution_start(
                prepared,
                replace(current, transition_sequence=0),
                service_journal=service,
                max_journal_operations=1,
            )
        assert service.count() == 0
    finally:
        authority.close()
        service.close()


@pytest.mark.parametrize("budget", [True, 0, -1, 1.0, 2**53])
def test_operation_store_rejects_invalid_retention_cap(tmp_path: Path, budget: object) -> None:
    store = EventStore(tmp_path / "cap.db")
    try:
        with pytest.raises(ValueError, match="budget"):
            store.commit_operation(
                operation_key="key",
                request_digest="a" * 64,
                response={},
                events=(("test", {}),),
                intent={},
                max_retained_operations=cast(int, budget),
            )
    finally:
        store.close()


def test_retention_cap_is_atomic_across_connections_and_does_not_evict(tmp_path: Path) -> None:
    path = tmp_path / "cap.db"
    stores = (EventStore(path), EventStore(path))

    def insert(index: int) -> str:
        try:
            result = stores[index].commit_operation(
                operation_key=f"key-{index}",
                request_digest="a" * 64,
                response={"index": index},
                events=(("test", {"index": index}),),
                intent={},
                max_retained_operations=1,
            )
            return result.outcome
        except ValueError as error:
            assert "budget exhausted" in str(error)
            return "full"

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(insert, range(2)))
        assert sorted(results) == ["full", "inserted"]
        assert len(stores[0].read_operations()) == 1
        assert stores[0].count() == 2
    finally:
        for store in stores:
            store.close()
