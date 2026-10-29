# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real immutable recovery evidence tests
"""Exercise recovery evidence through real journals, descriptors and reopened storage."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

from fixtures.protected_write_recovery_snapshot_linux import RecoveryFixture, _create
from fixtures.protected_write_recovery_snapshot_linux import recovery_fixture as recovery_fixture
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protected_write_driver import execute_prepared_protected_write
from synapse_channel.core.protected_write_evidence_store import ProtectedExecutionEvidenceStore
from synapse_channel.core.protected_write_execution_journal import record_protected_execution_start
from synapse_channel.core.protected_write_execution_steps import record_protected_execution_step
from synapse_channel.core.protected_write_recovery_evidence import (
    collect_protected_recovery_evidence,
    read_protected_recovery_evidence,
)
from test_protected_write_preparation import _begun


def _collect(fixture: RecoveryFixture) -> dict[str, object]:
    prepared, begun, _service, evidence, root = fixture
    return dict(
        collect_protected_recovery_evidence(
            prepared,
            begun,
            evidence_store=evidence,
            root_descriptors={"memory": root, "parent": root},
            owner_uid=os.getuid(),
            max_total_evidence_bytes=16384,
            max_total_content_bytes=16384,
        )
    )


def test_recovery_packet_survives_reopen_without_live_reads(
    recovery_fixture: RecoveryFixture,
    tmp_path: Path,
) -> None:
    """Persisted evidence stays immutable when current paths later change."""
    prepared, begun, service, _evidence, _root = recovery_fixture
    record_protected_execution_start(
        prepared, begun, service_journal=service, max_journal_operations=64
    )
    reference = _collect(recovery_fixture)
    assert _collect(recovery_fixture) == reference
    (tmp_path / "records/note.md").write_bytes(b"later foreign state")
    with EventStore(tmp_path / "recovery-execution.db") as reopened:
        evidence = ProtectedExecutionEvidenceStore(
            reopened, "text", prepared.policy.limits.operation_limits, 4096, 64
        )
        frozen = read_protected_recovery_evidence(
            reference,
            prepared=prepared,
            begun=begun,
            evidence_store=evidence,
            max_bytes=4096,
            max_total_evidence_bytes=16384,
        )
        note = dict(frozen.observations)[("memory", "records/note.md")]
        assert note is not None and note.file_identity is None
        assert hashlib.sha256(frozen.content).hexdigest() == reference["sha256"]
        assert json.loads(frozen.content)["position"] == "before_frontier"
        with pytest.raises(ValueError, match="another parent or begin"):
            read_protected_recovery_evidence(
                reference,
                prepared=prepared,
                begun=replace(begun, transition_sequence=99),
                evidence_store=evidence,
                max_bytes=4096,
                max_total_evidence_bytes=16384,
            )
    before = service.read_all()
    with pytest.raises(ValueError):
        _collect(recovery_fixture)
    assert service.read_all() == before


def test_uncertain_started_create_persists_exact_after_state(
    recovery_fixture: RecoveryFixture,
) -> None:
    """A real uncompleted creation yields evidence, never a completion receipt."""
    prepared, begun, service, evidence, _root = recovery_fixture
    execution = record_protected_execution_start(
        prepared,
        begun,
        service_journal=service,
        max_journal_operations=64,
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
    _create(recovery_fixture)
    reference = _collect(recovery_fixture)
    frozen = read_protected_recovery_evidence(
        reference,
        prepared=prepared,
        begun=begun,
        evidence_store=evidence,
        max_bytes=4096,
        max_total_evidence_bytes=16384,
    )
    source = json.loads(frozen.content)
    assert source["position"] == "after_frontier" and source["frontier_phase"] == "started"
    note = dict(frozen.observations)[("memory", "records/note.md")]
    assert note is not None and note.file_identity is not None


@pytest.mark.parametrize(
    "case",
    [
        "schema",
        "canonical",
        "parent",
        "frontier",
        "count",
        "path",
        "deferred",
        "chain",
        "root",
        "state",
        "fields",
        "mode",
        "content",
        "identity",
    ],
)
def test_closed_recovery_packet_refuses_malformed_stored_bytes(
    recovery_fixture: RecoveryFixture,
    case: str,
) -> None:
    """Invalid bytes in the actual store cannot acquire a valid parent-bound shape."""
    prepared, begun, service, evidence, _root = recovery_fixture
    record_protected_execution_start(
        prepared, begun, service_journal=service, max_journal_operations=64
    )
    reference = _collect(recovery_fixture)
    source = json.loads(evidence.read(reference, 4096))
    target = source["observations"][0]
    if case == "schema":
        source["extra"] = 1
    elif case == "parent":
        source["reservation_id"] = "foreign"
    elif case == "frontier":
        source["frontier_step"] = True
    elif case == "count":
        source["observations"] = []
    elif case == "path":
        target["relative_path"] = "foreign"
    elif case == "deferred":
        target["state"] = None
    elif case == "chain":
        target["directories"] = []
    elif case == "root":
        target["directories"][0][1] += 1
    elif case == "state":
        target["state"] = {"kind": "foreign"}
    elif case == "fields":
        target["state"]["extra"] = 1
    elif case == "mode":
        target["state"] = {"kind": "directory", "mode": "9999"}
    elif case == "content":
        target["state"] = {
            "kind": "file",
            "mode": "0600",
            "size_bytes": prepared.policy.limits.operation_limits.max_content_bytes + 1,
            "sha256": "a" * 64,
        }
    elif case == "identity":
        target["state"] = {"kind": "absent"}
        target["identity"] = [1, 1]
    content = json.dumps(source, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    if case == "canonical":
        content += b" "
    changed = evidence.write(content)
    with pytest.raises(ValueError):
        read_protected_recovery_evidence(
            changed,
            prepared=prepared,
            begun=begun,
            evidence_store=evidence,
            max_bytes=4096,
            max_total_evidence_bytes=16384,
        )


def test_collection_refuses_other_begin_before_storage(recovery_fixture: RecoveryFixture) -> None:
    """An unrelated admission cannot be used to collect this execution's evidence."""
    prepared, begun, service, _evidence, _root = recovery_fixture
    altered = replace(begun, admission=replace(begun.admission, reservation_id="foreign"))
    with pytest.raises(ValueError, match="preparation/begin mismatch"):
        _collect((prepared, altered, *recovery_fixture[2:]))
    assert service.read_all() == []


def test_complete_execution_packet_retains_verified_prefix_hashes(
    recovery_fixture: RecoveryFixture,
) -> None:
    """Actual complete backend execution is frozen without claiming OS isolation."""
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
        # Diagnostic local backend mechanics, not a deployed isolated-writer admission.
        verify_isolation=lambda: True,
        store_evidence=evidence.write,
        read_evidence=evidence.read,
    )
    ref = _collect(recovery_fixture)
    frozen = read_protected_recovery_evidence(
        ref,
        prepared=prepared,
        begun=begun,
        evidence_store=evidence,
        max_bytes=4096,
        max_total_evidence_bytes=16384,
    )
    source = json.loads(frozen.content)
    assert source["position"] == "complete"
    assert len(source["completed_prefix_sha256"]) == source["frontier_step"]
    assert dict(frozen.observations)[("memory", "records/note.md")] is not None
    with pytest.raises(ValueError, match="aggregate execution evidence exceeds budget"):
        read_protected_recovery_evidence(
            ref,
            prepared=prepared,
            begun=begun,
            evidence_store=evidence,
            max_bytes=4096,
            max_total_evidence_bytes=1,
        )
    source["completed_prefix_sha256"][0] = "a" * 64
    changed = evidence.write(
        json.dumps(source, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    )
    with pytest.raises(ValueError, match="retained execution frontier"):
        read_protected_recovery_evidence(
            changed,
            prepared=prepared,
            begun=begun,
            evidence_store=evidence,
            max_bytes=4096,
            max_total_evidence_bytes=16384,
        )


def test_packet_retains_planned_child_absence_under_checked_parent(tmp_path: Path) -> None:
    """Real missing planned parent and its child survive immutable storage."""
    authority, prepared, state = _begun(tmp_path, planned=True)
    begun = state.protected_write_reservations["reservation"]
    memory = os.open(tmp_path / "container", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    parent = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        with EventStore(tmp_path / "recovery-execution.db") as service:
            evidence = ProtectedExecutionEvidenceStore(
                service, "text", prepared.policy.limits.operation_limits, 4096, 64
            )
            record_protected_execution_start(
                prepared, begun, service_journal=service, max_journal_operations=64
            )
            ref = collect_protected_recovery_evidence(
                prepared,
                begun,
                evidence_store=evidence,
                root_descriptors={"memory": memory, "parent": parent},
                owner_uid=os.getuid(),
                max_total_evidence_bytes=16384,
                max_total_content_bytes=16384,
            )
            frozen = read_protected_recovery_evidence(
                ref,
                prepared=prepared,
                begun=begun,
                evidence_store=evidence,
                max_bytes=4096,
                max_total_evidence_bytes=16384,
            )
            assert dict(frozen.observations)[("memory", "records/note.md")] is None
            directory = tmp_path / "container/records"
            directory.mkdir(mode=0o700)
            actual = directory.stat()
            altered = json.loads(frozen.content)
            for path in altered["observations"]:
                if path["root_id"] == "memory" and path["relative_path"] == "records":
                    path["state"] = {"kind": "directory", "mode": "0700"}
                    path["identity"] = [actual.st_dev, actual.st_ino]
            raw = json.dumps(
                altered, sort_keys=True, separators=(",", ":"), ensure_ascii=True
            ).encode()
            bad_ref = evidence.write(raw)
            with pytest.raises(ValueError, match="checked absent ancestor"):
                read_protected_recovery_evidence(
                    bad_ref,
                    prepared=prepared,
                    begun=begun,
                    evidence_store=evidence,
                    max_bytes=4096,
                    max_total_evidence_bytes=16384,
                )
    finally:
        os.close(memory)
        os.close(parent)
        authority.close()


def test_frozen_packet_refuses_later_durable_execution_progress(
    recovery_fixture: RecoveryFixture,
) -> None:
    """A genuine later started record invalidates an older before-step packet."""
    prepared, begun, service, evidence, _root = recovery_fixture
    execution = record_protected_execution_start(
        prepared,
        begun,
        service_journal=service,
        max_journal_operations=64,
    )
    old_ref = _collect(recovery_fixture)
    original = evidence.read(old_ref, 4096)
    record_protected_execution_step(
        prepared,
        execution,
        execution.operation,
        step_index=0,
        phase="started",
        service_journal=service,
        max_journal_operations=64,
    )
    with pytest.raises(ValueError, match="retained execution frontier"):
        read_protected_recovery_evidence(
            old_ref,
            prepared=prepared,
            begun=begun,
            evidence_store=evidence,
            max_bytes=4096,
            max_total_evidence_bytes=16384,
        )
    assert evidence.read(old_ref, 4096) == original
    new_ref = _collect(recovery_fixture)
    assert new_ref != old_ref
    current = read_protected_recovery_evidence(
        new_ref,
        prepared=prepared,
        begun=begun,
        evidence_store=evidence,
        max_bytes=4096,
        max_total_evidence_bytes=16384,
    )
    assert json.loads(current.content)["frontier_phase"] == "started"


def test_packet_cannot_substitute_another_execution_start_hash(
    recovery_fixture: RecoveryFixture,
) -> None:
    """A well-shaped hash in real stored bytes cannot stand in for retained history."""
    prepared, begun, service, evidence, _root = recovery_fixture
    record_protected_execution_start(
        prepared, begun, service_journal=service, max_journal_operations=64
    )
    reference = _collect(recovery_fixture)
    source = json.loads(evidence.read(reference, 4096))
    source["execution_start_sha256"] = "a" * 64
    altered = evidence.write(
        json.dumps(source, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    )
    with pytest.raises(ValueError, match="retained execution frontier"):
        read_protected_recovery_evidence(
            altered,
            prepared=prepared,
            begun=begun,
            evidence_store=evidence,
            max_bytes=4096,
            max_total_evidence_bytes=16384,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("frontier_phase", None),
        ("frontier_phase", "unsupported"),
        ("position", None),
        ("position", "unsupported"),
        ("frontier_step", -1),
        ("frontier_step", 999),
        ("frontier_phase", "complete"),
        ("position", "complete"),
        ("position", "after_frontier"),
        ("completed_prefix_sha256", None),
        ("completed_prefix_sha256", ["a" * 64]),
        ("execution_start_sha256", None),
        ("execution_start_sha256", "z" * 64),
    ],
)
def test_packet_refuses_inconsistent_progress_metadata(
    recovery_fixture: RecoveryFixture, field: str, value: object
) -> None:
    """Stored bytes cannot claim impossible progress or an unbound retained prefix."""
    prepared, begun, service, evidence, _root = recovery_fixture
    record_protected_execution_start(
        prepared, begun, service_journal=service, max_journal_operations=64
    )
    reference = _collect(recovery_fixture)
    source = json.loads(evidence.read(reference, 4096))
    source[field] = value
    changed = evidence.write(
        json.dumps(source, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    )
    before = service.read_all()
    with pytest.raises(ValueError):
        read_protected_recovery_evidence(
            changed,
            prepared=prepared,
            begun=begun,
            evidence_store=evidence,
            max_bytes=4096,
            max_total_evidence_bytes=16384,
        )
    assert service.read_all() == before


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("directories", None),
        ("directories", [[True, 1]]),
        ("state", {"kind": 1}),
        ("state", {"kind": "file", "mode": "0600", "size_bytes": True, "sha256": "a" * 64}),
        ("state", {"kind": "file", "mode": "0600", "size_bytes": 0, "sha256": False}),
        ("state", {"kind": "file", "mode": 0o600, "size_bytes": 0, "sha256": "a" * 64}),
        ("identity", [True, 1]),
    ],
)
def test_packet_refuses_unbound_observation_shape(
    recovery_fixture: RecoveryFixture, field: str, value: object
) -> None:
    """Malformed persisted identities/content cannot become retained observations."""
    prepared, begun, service, evidence, _root = recovery_fixture
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
    _create(recovery_fixture)
    reference = _collect(recovery_fixture)
    source = json.loads(evidence.read(reference, 4096))
    source["observations"][0][field] = value
    changed = evidence.write(
        json.dumps(source, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    )
    before = service.read_all()
    with pytest.raises(ValueError):
        read_protected_recovery_evidence(
            changed,
            prepared=prepared,
            begun=begun,
            evidence_store=evidence,
            max_bytes=4096,
            max_total_evidence_bytes=16384,
        )
    assert service.read_all() == before
