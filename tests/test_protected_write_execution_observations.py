# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — execution observation integrity regressions
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from synapse_channel.core.persistence import EventStore, StoredOperation
from synapse_channel.core.protected_write_evidence_store import ProtectedExecutionEvidenceStore
from synapse_channel.core.protected_write_execution_observations import (
    verify_protected_execution_final_state,
    verify_protected_execution_observations,
)
from synapse_channel.core.protected_write_lifecycle import ProtectedWriteReservation
from synapse_channel.core.protected_write_preparation import PreparedProtectedWrite
from test_protected_write_execution_journal import completed_execution as completed_execution

Execution = tuple[
    PreparedProtectedWrite, ProtectedWriteReservation, EventStore, tuple[StoredOperation, ...]
]


def test_successful_child_exit_does_not_fence_an_inherited_writable_descriptor(
    completed_execution: Execution, tmp_path: Path
) -> None:
    """Actual child exit leaves another holder able to invalidate final-state proof."""
    prepared, begun, service, _ = completed_execution
    evidence = evidence_store(completed_execution)
    root = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    writer = os.open(tmp_path / "records/note.md", os.O_RDWR)
    try:
        child = subprocess.run(
            [sys.executable, "-I", "-c", "import os,sys; os.fsync(int(sys.argv[1]))", str(writer)],
            pass_fds=(writer,),
            capture_output=True,
            timeout=10,
            check=True,
        )
        assert child.returncode == 0
        before = verify_protected_execution_final_state(
            prepared,
            begun,
            service_journal=service,
            read_evidence=evidence.read,
            max_total_evidence_bytes=65536,
            root_descriptors={"memory": root, "parent": root},
            owner_uid=os.getuid(),
            max_total_content_bytes=65536,
        )
        assert before
        original_size = os.fstat(writer).st_size
        assert os.pwrite(writer, b"X", 0) == 1
        os.fsync(writer)
        assert os.fstat(writer).st_size == original_size
        with pytest.raises(ValueError):
            verify_protected_execution_final_state(
                prepared,
                begun,
                service_journal=service,
                read_evidence=evidence.read,
                max_total_evidence_bytes=65536,
                root_descriptors={"memory": root, "parent": root},
                owner_uid=os.getuid(),
                max_total_content_bytes=65536,
            )
    finally:
        os.close(writer)
        os.close(root)


@pytest.mark.parametrize(
    "case",
    [
        "unchanged",
        "bytes",
        "mode",
        "same-bytes-replacement",
        "missing",
        "symlink",
        "hardlink",
        "owner",
        "root",
        "extra-root",
        "budget",
        "boolean-budget",
    ],
)
def test_final_state_checks_real_files_and_preserves_borrowed_descriptors(
    completed_execution: Execution, tmp_path: Path, case: str
) -> None:
    prepared, begun, service, _ = completed_execution
    evidence = evidence_store(completed_execution)
    descriptor = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    roots = {"memory": descriptor, "parent": descriptor}
    owner = os.getuid()
    budget = 65536
    target = tmp_path / "records/note.md"
    original = target.read_bytes()
    if case == "bytes":
        target.write_bytes(b"changed fixture bytes")
    elif case == "mode":
        target.chmod(0o640)
    elif case in {"same-bytes-replacement", "missing", "symlink", "hardlink"}:
        retained = target.with_suffix(".prior")
        target.rename(retained)
        if case == "same-bytes-replacement":
            target.write_bytes(original)
            target.chmod(0o600)
        elif case == "symlink":
            target.symlink_to(retained)
        elif case == "hardlink":
            os.link(retained, target)
    elif case == "owner":
        owner += 1
    elif case == "root":
        roots["memory"] = -1
    elif case == "extra-root":
        roots["extra"] = descriptor
    elif case == "budget":
        budget = 1
    elif case == "boolean-budget":
        budget = True
    try:
        if case == "unchanged":
            result = verify_protected_execution_final_state(
                prepared,
                begun,
                service_journal=service,
                read_evidence=evidence.read,
                max_total_evidence_bytes=65536,
                root_descriptors=roots,
                owner_uid=owner,
                max_total_content_bytes=budget,
            )
            assert {key for key, _ in result} == {
                ("memory", "records/note.md"),
                ("memory", "records"),
            }
            assert target.read_bytes() == original
        else:
            with pytest.raises((ValueError, OSError)):
                verify_protected_execution_final_state(
                    prepared,
                    begun,
                    service_journal=service,
                    read_evidence=evidence.read,
                    max_total_evidence_bytes=65536,
                    root_descriptors=roots,
                    owner_uid=owner,
                    max_total_content_bytes=budget,
                )
        assert os.fstat(descriptor).st_ino == tmp_path.stat().st_ino
    finally:
        os.close(descriptor)


def evidence_store(fixture: Execution) -> ProtectedExecutionEvidenceStore:
    prepared, _, service, _ = fixture
    return ProtectedExecutionEvidenceStore(
        service, "text", prepared.policy.limits.operation_limits, 4096, 128
    )


def test_observations_bind_actual_driver_bytes_without_writes(
    completed_execution: Execution,
) -> None:
    prepared, begun, service, records = completed_execution
    evidence = evidence_store(completed_execution)
    expected = tuple(evidence.read(r.response["evidence_reference"], 4096) for r in records[1:])
    before = service.read_all()
    assert (
        verify_protected_execution_observations(
            prepared,
            begun,
            service_journal=service,
            read_evidence=evidence.read,
            max_total_evidence_bytes=sum(map(len, expected)),
        )
        == expected
    )
    assert service.read_all() == before


@pytest.mark.parametrize("case", ["contradictory-alias", "missing-parent", "wrong-initial"])
def test_observations_refuse_inconsistent_preparation_topology(
    completed_execution: Execution, case: str
) -> None:
    prepared, begun, service, _ = completed_execution
    if case == "missing-parent":
        prepared = replace(prepared, observations=())
    elif case == "contradictory-alias":
        key, observed = next(
            (key, observed)
            for key, observed in prepared.observations
            if observed is not None and observed.file_identity is not None
        )
        assert observed is not None
        prepared = replace(
            prepared,
            observations=(*prepared.observations, (key, replace(observed, file_identity=(0, 1)))),
        )
    else:
        prepared = replace(
            prepared,
            observations=tuple(
                (key, replace(observed, file_identity=(0, 1)))
                if observed is not None and observed.file_identity is None
                else (key, observed)
                for key, observed in prepared.observations
            ),
        )
    with pytest.raises(ValueError):
        verify_protected_execution_observations(
            prepared,
            begun,
            service_journal=service,
            read_evidence=evidence_store(completed_execution).read,
            max_total_evidence_bytes=65536,
        )


@pytest.mark.parametrize("budget", [True, 0, -1, 1.0, 2**53, 1])
def test_observations_enforce_exact_aggregate_budget(
    completed_execution: Execution, budget: object
) -> None:
    prepared, begun, service, _ = completed_execution
    with pytest.raises(ValueError, match="budget"):
        verify_protected_execution_observations(
            prepared,
            begun,
            service_journal=service,
            read_evidence=evidence_store(completed_execution).read,
            max_total_evidence_bytes=cast(int, budget),
        )


@pytest.mark.parametrize("case", ["missing-verifier", "false-verifier", "wrong-bytes", "mutable"])
def test_observations_reject_unverified_storage_bytes(
    completed_execution: Execution, case: str
) -> None:
    prepared, begun, service, _ = completed_execution
    evidence = evidence_store(completed_execution)
    reader: Callable[[Mapping[str, object], int], bytes] = evidence.read
    if case == "missing-verifier":
        prepared = replace(prepared, policy=replace(prepared.policy, domain_verifiers={}))
    elif case == "false-verifier":
        prepared = replace(
            prepared,
            policy=replace(prepared.policy, domain_verifiers={"text": lambda _b, _d: False}),
        )
    elif case == "wrong-bytes":

        def reader(_ref: Mapping[str, object], _size: int) -> bytes:
            return b"changed bytes"
    else:

        def reader(_ref: Mapping[str, object], _size: int) -> bytes:
            return cast(bytes, bytearray(b"mutable"))

    with pytest.raises(ValueError):
        verify_protected_execution_observations(
            prepared,
            begun,
            service_journal=service,
            read_evidence=reader,
            max_total_evidence_bytes=65536,
        )


@pytest.mark.parametrize(
    "case",
    [
        "extra",
        "whitespace",
        "domain",
        "execution",
        "operation",
        "index",
        "boolean-index",
        "paths",
        "path-count",
        "path-fields",
        "path",
        "root-id",
        "state",
        "directories",
        "directory-count",
        "enrolled-root",
        "identity",
        "identity-bool",
        "identity-negative",
        "changed-identity",
    ],
)
def test_digest_valid_but_wrong_observation_is_not_accepted(
    completed_execution: Execution, tmp_path: Path, case: str
) -> None:
    prepared, begun, service, records = completed_execution
    evidence = evidence_store(completed_execution)
    record = records[-1]
    original = evidence.read(record.response["evidence_reference"], 4096)
    document = json.loads(original)
    observed = document["after"][0]
    if case == "extra":
        document["extra"] = True
    elif case == "domain":
        document["domain"] = "other-domain"
    elif case == "execution":
        document["execution_start_sha256"] = "0" * 64
    elif case == "operation":
        document["operation_id"] = "other-operation"
    elif case == "index":
        document["step_index"] += 1
    elif case == "boolean-index":
        document["step_index"] = True
    elif case == "paths":
        document["after"] = {}
    elif case == "path-count":
        document["after"] = []
    elif case == "path-fields":
        observed["extra"] = True
    elif case == "path":
        observed["relative_path"] = "other/path"
    elif case == "root-id":
        observed["root_id"] = "other-root"
    elif case == "state":
        observed["state"] = {"kind": "absent"}
    elif case == "directories":
        observed["directories"] = {}
    elif case == "directory-count":
        observed["directories"] = []
    elif case == "enrolled-root":
        observed["directories"][0] = [0, 0]
    elif case == "identity":
        observed["identity"] = None
    elif case == "identity-bool":
        observed["identity"] = [True, 1]
    elif case == "identity-negative":
        observed["identity"] = [-1, 1]
    elif case == "changed-identity":
        observed["identity"][1] += 1
    encoded = json.dumps(
        document, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()
    if case == "whitespace":
        encoded += b"\n"
    # Deliberate local database fault: rehash record and commit marker consistently.
    # A fresh valid ContentRef must not hide semantically incorrect observation bytes.
    reference = evidence.write(encoded)
    body = {**record.response, "evidence_reference": dict(reference)}
    raw = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    digest = hashlib.sha256(raw.encode()).hexdigest()
    marker = service.latest_at_or_before(record.commit_seq)
    assert marker is not None
    payload = {
        **marker.payload,
        "request_digest": digest,
        "response_sha256": digest,
        "response": body,
    }
    with sqlite3.connect(tmp_path / "completed.db") as database:
        database.execute(
            "UPDATE operations SET response_json=?, response_sha256=?, request_digest=? "
            "WHERE operation_key=?",
            (raw, digest, digest, record.operation_key),
        )
        database.execute("UPDATE events SET payload=? WHERE seq=?", (raw, record.first_event_seq))
        database.execute(
            "UPDATE events SET payload=? WHERE seq=?", (json.dumps(payload), record.commit_seq)
        )
    with pytest.raises(ValueError):
        verify_protected_execution_observations(
            prepared,
            begun,
            service_journal=service,
            read_evidence=evidence.read,
            max_total_evidence_bytes=65536,
        )
