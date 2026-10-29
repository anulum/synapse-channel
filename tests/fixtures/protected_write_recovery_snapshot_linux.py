# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real retained recovery snapshots
"""Verify actual known bytes without claiming separate-UID writer quiescence."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator, Mapping
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from synapse_channel.core.atomic_operations import canonical_request_digest
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protected_write_admission import bind_protected_write_admission
from synapse_channel.core.protected_write_descriptors import hold_protected_path
from synapse_channel.core.protected_write_driver import execute_prepared_protected_write
from synapse_channel.core.protected_write_evidence_store import ProtectedExecutionEvidenceStore
from synapse_channel.core.protected_write_execution_journal import record_protected_execution_start
from synapse_channel.core.protected_write_execution_observations import (
    verify_protected_execution_observations,
)
from synapse_channel.core.protected_write_execution_steps import record_protected_execution_step
from synapse_channel.core.protected_write_file_execution import create_retained_protected_entry
from synapse_channel.core.protected_write_lifecycle import ProtectedWriteReservation
from synapse_channel.core.protected_write_preparation import PreparedProtectedWrite
from synapse_channel.core.protected_write_proposal import parse_protected_write_proposal
from synapse_channel.core.protected_write_recovery_snapshot import (
    ProtectedRecoverySnapshot,
    inspect_protected_recovery_snapshot,
)
from test_protected_write_admission import CONTEXT, _claim
from test_protected_write_content import CONTENT
from test_protected_write_inspection import _inspect
from test_protected_write_preparation import _begun, _inputs

RecoveryFixture = tuple[
    PreparedProtectedWrite,
    ProtectedWriteReservation,
    EventStore,
    ProtectedExecutionEvidenceStore,
    int,
]


@pytest.fixture
def recovery_fixture(tmp_path: Path) -> Iterator[RecoveryFixture]:
    """Retain real prepared authority, evidence store and filesystem roots."""
    authority, prepared, state = _begun(tmp_path)
    service = EventStore(tmp_path / "recovery-execution.db")
    evidence = ProtectedExecutionEvidenceStore(
        service, "text", prepared.policy.limits.operation_limits, 4096, 64
    )
    root = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        yield prepared, state.protected_write_reservations["reservation"], service, evidence, root
    finally:
        os.close(root)
        service.close()
        authority.close()


def _snapshot(fixture: RecoveryFixture, *, budget: int = 16384) -> ProtectedRecoverySnapshot:
    """Capture through the public recovery inspector with retained real roots."""
    prepared, begun, service, evidence, root = fixture
    return inspect_protected_recovery_snapshot(
        prepared,
        begun,
        service_journal=service,
        read_evidence=evidence.read,
        max_total_evidence_bytes=16384,
        root_descriptors={"memory": root, "parent": root},
        owner_uid=os.getuid(),
        max_total_content_bytes=budget,
    )


def _create(fixture: RecoveryFixture) -> None:
    """Exercise the actual retained creation backend for the admitted path."""
    prepared, _begun_value, _service, _evidence, root = fixture
    expected = dict(prepared.observations)[("memory", "records/note.md")]
    assert expected is not None
    with hold_protected_path(
        root, expected, owner_uid=os.getuid(), max_bytes=4096, require_openat2=True
    ) as target:
        create_retained_protected_entry(
            target, kind="file", mode=0o600, content=CONTENT, max_bytes=4096
        )


def test_unstarted_frontier_refuses_an_unjournaled_physical_effect(
    recovery_fixture: RecoveryFixture,
) -> None:
    """A missing step record cannot justify bytes that appeared after consumption."""
    prepared, begun, service, _evidence, _root = recovery_fixture
    record_protected_execution_start(
        prepared, begun, service_journal=service, max_journal_operations=64
    )
    before = service.read_all()
    snapshot = _snapshot(recovery_fixture)
    assert snapshot.position == "before_frontier"
    assert snapshot.prefix.frontier.phase == "before_step"
    assert service.read_all() == before
    _create(recovery_fixture)
    with pytest.raises(ValueError):
        _snapshot(recovery_fixture)


def test_started_frontier_accepts_exact_before_or_after_and_refuses_partial_bytes(
    recovery_fixture: RecoveryFixture, tmp_path: Path
) -> None:
    """An uncertain creation resolves only to its exact declared byte states."""
    prepared, begun, service, _evidence, _root = recovery_fixture
    execution = record_protected_execution_start(
        prepared, begun, service_journal=service, max_journal_operations=64
    )
    record_protected_execution_step(
        prepared,
        execution,
        execution.operation,
        step_index=0,
        phase="started",
        service_journal=service,
        max_journal_operations=64,
    )
    assert _snapshot(recovery_fixture).position == "before_frontier"
    _create(recovery_fixture)
    after = _snapshot(recovery_fixture)
    assert after.position == "after_frontier"
    observed = dict(after.observations)[("memory", "records/note.md")]
    assert observed is not None and observed.size_bytes == len(CONTENT)
    with pytest.raises(ValueError, match="aggregate budget"):
        _snapshot(recovery_fixture, budget=1)
    (tmp_path / "records/note.md").write_bytes(CONTENT[:1])
    with pytest.raises(ValueError, match="declared effect"):
        _snapshot(recovery_fixture)


def test_driver_interruption_retains_verified_prefix_and_detects_inode_substitution(
    recovery_fixture: RecoveryFixture, tmp_path: Path
) -> None:
    """Real driver output before the next step binds current bytes and inodes."""
    prepared, begun, service, evidence, root = recovery_fixture
    calls = 0

    def current() -> ProtectedWriteReservation:
        nonlocal calls
        calls += 1
        if calls == 4:
            raise ValueError("injected authority disconnect before next step")
        return begun

    with pytest.raises(ValueError, match="authority disconnect"):
        execute_prepared_protected_write(
            prepared,
            root_descriptors={"memory": root, "parent": root},
            owner_uid=os.getuid(),
            service_journal=service,
            max_journal_operations=64,
            max_locks=8,
            max_evidence_bytes=4096,
            verify_current=current,
            # Local driver mechanics only; this is not physical isolation proof.
            verify_isolation=lambda: True,
            store_evidence=evidence.write,
            read_evidence=evidence.read,
        )
    snapshot = _snapshot(recovery_fixture)
    assert snapshot.position == "before_frontier"
    assert snapshot.prefix.frontier.step_index == 1
    assert len(snapshot.prefix.observations) == 1
    with pytest.raises(ValueError, match="incomplete or uncertain"):
        verify_protected_execution_observations(
            prepared,
            begun,
            service_journal=service,
            read_evidence=evidence.read,
            max_total_evidence_bytes=16384,
        )
    target = tmp_path / "records/note.md"
    replacement = tmp_path / "records/replacement"
    replacement.write_bytes(CONTENT)
    replacement.chmod(0o600)
    replacement.replace(target)
    with pytest.raises(ValueError, match="observation changed"):
        _snapshot(recovery_fixture)


def test_complete_driver_snapshot_requires_exact_final_files(
    recovery_fixture: RecoveryFixture, tmp_path: Path
) -> None:
    """The complete path uses the same immutable-prefix and live-inode checks."""
    prepared, begun, service, evidence, root = recovery_fixture
    execute_prepared_protected_write(
        prepared,
        root_descriptors={"memory": root, "parent": root},
        owner_uid=os.getuid(),
        service_journal=service,
        max_journal_operations=64,
        max_locks=8,
        max_evidence_bytes=4096,
        verify_current=lambda: begun,
        # Local driver mechanics only; this is not physical isolation proof.
        verify_isolation=lambda: True,
        store_evidence=evidence.write,
        read_evidence=evidence.read,
    )
    assert _snapshot(recovery_fixture).position == "complete"
    (tmp_path / "records/note.md").write_bytes(b"foreign")
    with pytest.raises(ValueError):
        _snapshot(recovery_fixture)


def test_planned_directory_frontier_proves_child_absence_before_and_after_mkdir(
    tmp_path: Path,
) -> None:
    """A child is absent only under the exact checked planned parent."""
    authority, prepared, state = _begun(tmp_path, planned=True)
    begun = state.protected_write_reservations["reservation"]
    memory = os.open(tmp_path / "container", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    parent = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    with EventStore(tmp_path / "recovery-execution.db") as service:
        evidence = ProtectedExecutionEvidenceStore(
            service, "text", prepared.policy.limits.operation_limits, 4096, 64
        )

        def capture() -> ProtectedRecoverySnapshot:
            return inspect_protected_recovery_snapshot(
                prepared,
                begun,
                service_journal=service,
                read_evidence=evidence.read,
                max_total_evidence_bytes=16384,
                root_descriptors={"memory": memory, "parent": parent},
                owner_uid=os.getuid(),
                max_total_content_bytes=16384,
            )

        try:
            execution = record_protected_execution_start(
                prepared, begun, service_journal=service, max_journal_operations=64
            )
            before = capture()
            assert dict(before.observations)[("memory", "records/note.md")] is None
            record_protected_execution_step(
                prepared,
                execution,
                execution.operation,
                step_index=0,
                phase="started",
                service_journal=service,
                max_journal_operations=64,
            )
            expected = dict(prepared.observations)[("memory", "records")]
            assert expected is not None
            with hold_protected_path(
                memory, expected, owner_uid=os.getuid(), max_bytes=4096, require_openat2=True
            ) as target:
                create_retained_protected_entry(
                    target, kind="directory", mode=0o700, content=b"", max_bytes=4096
                )
            after = capture()
            assert after.position == "after_frontier"
            child = dict(after.observations)[("memory", "records/note.md")]
            assert child is not None and child.file_identity is None
            (tmp_path / "container/records/note.md").write_bytes(b"foreign")
            with pytest.raises(ValueError):
                capture()
        finally:
            os.close(memory)
            os.close(parent)
            authority.close()


def test_snapshot_refuses_invalid_enrollment_and_budget(
    recovery_fixture: RecoveryFixture,
) -> None:
    """Bad root scope, identity and read budget fail before journal inspection."""
    prepared, begun, service, evidence, root = recovery_fixture
    for roots, owner, budget in (
        ({}, os.getuid(), 1024),
        ({"memory": root, "parent": root}, -1, 1024),
        ({"memory": root, "parent": root}, os.getuid(), 0),
    ):
        with pytest.raises(ValueError, match="invalid recovery"):
            inspect_protected_recovery_snapshot(
                prepared,
                begun,
                service_journal=service,
                read_evidence=evidence.read,
                max_total_evidence_bytes=16384,
                root_descriptors=roots,
                owner_uid=owner,
                max_total_content_bytes=budget,
            )


@pytest.mark.parametrize("case", ["aliases", "missing_parent", "absent_alias", "file_parent"])
def test_snapshot_refuses_corrupted_preparation_topology(
    recovery_fixture: RecoveryFixture, tmp_path: Path, case: str
) -> None:
    """Corrupted trusted-input metadata cannot create a recovery proof."""
    prepared, begun, service, evidence, root = recovery_fixture
    record_protected_execution_start(
        prepared, begun, service_journal=service, max_journal_operations=64
    )
    observations = dict(prepared.observations)
    directory = observations[("memory", "records")]
    assert directory is not None
    if case == "aliases":
        observations[("parent", "records")] = replace(directory, mode=0o600)
        message = "aliases disagree on bytes"
    elif case == "missing_parent":
        observations[("memory", "missing/child")] = None
        message = "unmodeled parent"
    elif case == "absent_alias":
        observations = {
            ("memory", "records/note.md"): None,
            ("parent", "records"): replace(
                directory, file_identity=None, mode=None, is_directory=False
            ),
        }
        message = "checked absence ancestor"
    else:
        (tmp_path / "records/note.md").write_bytes(CONTENT)
        note = _inspect(tmp_path, "records/note.md")
        observations = {
            ("memory", "records/note.md/child"): None,
            ("memory", "records/note.md"): note,
        }
        message = "not a modeled directory"
    changed = replace(prepared, observations=tuple(observations.items()))
    with pytest.raises(ValueError, match=message):
        _snapshot((changed, begun, service, evidence, root))


def test_real_unlink_then_evidence_failure_has_known_absent_after_state(tmp_path: Path) -> None:
    """An interrupted unlink matches absent bytes without inventing completion."""
    admission, proposal, policy, observations = _inputs(tmp_path)
    create = deepcopy(proposal["auxiliary_operations"][0])
    create["operation_id"] = "create-scratch"
    create["paths"][0]["relative_path"] = "records/scratch"
    remove = {
        "operation_id": "unlink-scratch",
        "opcode": "unlink",
        "content_reference": None,
        "paths": [
            {
                "root_id": "memory",
                "relative_path": "records/scratch",
                "before": create["paths"][0]["after"],
                "after": {"kind": "absent"},
            }
        ],
    }
    proposal["auxiliary_operations"][2:2] = [create, remove]
    permissions = dict(policy.auxiliary_opcodes)
    permissions[("memory", "records/scratch")] = frozenset({"create", "unlink"})
    parents = dict(policy.enrolled_parents)
    parents[("memory", "records/scratch")] = ("memory", "records")
    policy = replace(policy, auxiliary_opcodes=permissions, enrolled_parents=parents)
    observations[("memory", "records/scratch")] = _inspect(tmp_path, "records/scratch")
    request = json.loads(admission.request_bytes)
    response = json.loads(admission.result_bytes)
    request["body"]["proposal"] = proposal
    request["proposal_sha256"] = parse_protected_write_proposal(
        json.dumps(proposal), limits=policy.limits
    ).proposal_sha256
    response["proposal_sha256"] = request["proposal_sha256"]
    response["body"]["request_digest"] = canonical_request_digest(request)
    admission = bind_protected_write_admission(
        json.dumps(request),
        json.dumps(response),
        context=CONTEXT,
        claims={"task-1": _claim()},
        limits=policy.limits,
        reason_codes=frozenset(),
    )
    authority, prepared, state = _begun(
        tmp_path, prepared_inputs=(admission, proposal, policy, observations)
    )
    begun = state.protected_write_reservations["reservation"]
    root = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    with EventStore(tmp_path / "recovery-execution.db") as service:
        evidence = ProtectedExecutionEvidenceStore(
            service, "text", prepared.policy.limits.operation_limits, 4096, 64
        )
        writes = 0

        def fail_after_unlink(content: bytes) -> Mapping[str, object]:
            nonlocal writes
            writes += 1
            if writes == 4:
                raise OSError("injected evidence storage failure after unlink")
            return evidence.write(content)

        try:
            with pytest.raises(OSError, match="storage failure after unlink"):
                execute_prepared_protected_write(
                    prepared,
                    root_descriptors={"memory": root, "parent": root},
                    owner_uid=os.getuid(),
                    service_journal=service,
                    max_journal_operations=64,
                    max_locks=8,
                    max_evidence_bytes=4096,
                    verify_current=lambda: begun,
                    # Local driver mechanics only; this is not physical isolation proof.
                    verify_isolation=lambda: True,
                    store_evidence=fail_after_unlink,
                    read_evidence=evidence.read,
                )
            snapshot = _snapshot((prepared, begun, service, evidence, root))
            assert snapshot.position == "after_frontier"
            assert snapshot.prefix.frontier.phase == "unknown"
            scratch = dict(snapshot.observations)[("memory", "records/scratch")]
            assert scratch is not None and scratch.file_identity is None
            assert not (tmp_path / "records/scratch").exists()
        finally:
            os.close(root)
            authority.close()
