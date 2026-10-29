# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — actual rename and unlink retained observation faults
"""Verify real filesystem effects and reject digest-valid false historical identities.

These storage and file-backend tests grant no isolation or quiescence authority.
They never replace a supervisor predicate with a positive callback.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterator, Mapping
from contextlib import ExitStack
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from synapse_channel.core.atomic_operations import canonical_request_digest
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protected_write_admission import bind_protected_write_admission
from synapse_channel.core.protected_write_descriptors import hold_protected_path
from synapse_channel.core.protected_write_evidence_store import ProtectedExecutionEvidenceStore
from synapse_channel.core.protected_write_execution_journal import record_protected_execution_start
from synapse_channel.core.protected_write_execution_steps import record_protected_execution_step
from synapse_channel.core.protected_write_file_execution import (
    create_retained_protected_entry,
    fsync_retained_protected_object,
    rename_retained_protected_file,
    unlink_retained_protected_file,
)
from synapse_channel.core.protected_write_inspection import inspect_protected_file
from synapse_channel.core.protected_write_preparation import PreparedProtectedWrite
from synapse_channel.core.protected_write_proposal import parse_protected_write_proposal
from test_protected_write_admission import CONTEXT, _claim
from test_protected_write_content import CONTENT
from test_protected_write_execution_observations import Execution
from test_protected_write_inspection import _inspect
from test_protected_write_preparation import _begun, _inputs


@pytest.fixture(params=["rename", "unlink"])
def completed_effect(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Execution]:
    """Retain a real admitted execution with native rename or unlink and checked readback."""
    kind = str(request.param)
    admission, proposal, policy, observations = _inputs(tmp_path)
    create = deepcopy(proposal["auxiliary_operations"][0])
    create["operation_id"] = "create-scratch"
    create["paths"][0]["relative_path"] = "records/scratch"
    file_state = create["paths"][0]["after"]
    source = {
        "root_id": "memory",
        "relative_path": "records/scratch",
        "before": file_state,
        "after": {"kind": "absent"},
    }
    paths = [source]
    extra = []
    if kind == "rename":
        destination = {
            "root_id": "memory",
            "relative_path": "records/renamed",
            "before": {"kind": "absent"},
            "after": file_state,
        }
        paths.append(destination)
        extra.append(
            {
                "operation_id": "flush-renamed",
                "opcode": "fsync",
                "content_reference": None,
                "paths": [{**destination, "before": file_state}],
            }
        )
    transfer = {
        "operation_id": "effect-scratch",
        "opcode": kind,
        "content_reference": None,
        "paths": paths,
    }
    flush_source = {
        "operation_id": "flush-scratch",
        "opcode": "fsync",
        "content_reference": None,
        "paths": [{**source, "after": file_state}],
    }
    proposal["auxiliary_operations"][1:1] = [create, flush_source, transfer, *extra]
    permissions, parents = dict(policy.auxiliary_opcodes), dict(policy.enrolled_parents)
    for name in ("records/scratch", "records/renamed"):
        permissions[("memory", name)] = frozenset({"create", "rename", "unlink", "fsync"})
        parents[("memory", name)] = ("memory", "records")
        observations[("memory", name)] = _inspect(tmp_path, name)
    policy = replace(policy, auxiliary_opcodes=permissions, enrolled_parents=parents)
    frame, result = json.loads(admission.request_bytes), json.loads(admission.result_bytes)
    frame["body"]["proposal"] = proposal
    frame["proposal_sha256"] = parse_protected_write_proposal(
        json.dumps(proposal), limits=policy.limits
    ).proposal_sha256
    result["proposal_sha256"] = frame["proposal_sha256"]
    result["body"]["request_digest"] = canonical_request_digest(frame)
    admission = bind_protected_write_admission(
        json.dumps(frame),
        json.dumps(result),
        context=CONTEXT,
        claims={"task-1": _claim()},
        limits=policy.limits,
        reason_codes=frozenset(),
    )
    authority, prepared, state = _begun(
        tmp_path, prepared_inputs=(admission, proposal, policy, observations)
    )
    root = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    service = EventStore(tmp_path / "completed.db")
    evidence = ProtectedExecutionEvidenceStore(
        service, "text", prepared.policy.limits.operation_limits, 4096, 128
    )
    begun = state.protected_write_reservations["reservation"]

    def observe(operation: dict[str, Any]) -> list[dict[str, Any]]:
        """Capture actual postconditions with native path restrictions."""
        after = []
        for path in operation["paths"]:
            actual = inspect_protected_file(
                root,
                path["relative_path"],
                root_identity=prepared.policy.enrolled_roots["memory"],
                max_bytes=4096,
                allow_directory=True,
                require_openat2=True,
            )
            if actual.file_identity is None:
                observed_state: dict[str, Any] = {"kind": "absent"}
            elif actual.is_directory:
                observed_state = {"kind": "directory", "mode": f"{actual.mode:04o}"}
            else:
                observed_state = {
                    "kind": "file",
                    "mode": f"{actual.mode:04o}",
                    "sha256": actual.sha256,
                    "size_bytes": actual.size_bytes,
                }
            assert observed_state == path["after"]
            after.append(
                {
                    "root_id": path["root_id"],
                    "relative_path": path["relative_path"],
                    "state": observed_state,
                    "identity": None
                    if actual.file_identity is None
                    else list(actual.file_identity),
                    "directories": [list(item) for item in actual.directories],
                }
            )
        return after

    try:
        execution = record_protected_execution_start(
            prepared, begun, service_journal=service, max_journal_operations=128
        )
        records = [execution.operation]
        previous = execution.operation
        for index, operation in enumerate(proposal["auxiliary_operations"]):
            started = record_protected_execution_step(
                prepared,
                execution,
                previous,
                step_index=index,
                phase="started",
                service_journal=service,
                max_journal_operations=128,
            )
            with ExitStack() as stack:
                targets = [
                    stack.enter_context(
                        hold_protected_path(
                            root,
                            inspect_protected_file(
                                root,
                                path["relative_path"],
                                root_identity=prepared.policy.enrolled_roots["memory"],
                                max_bytes=4096,
                                allow_directory=True,
                                require_openat2=True,
                            ),
                            owner_uid=os.getuid(),
                            max_bytes=4096,
                            require_openat2=True,
                        )
                    )
                    for path in operation["paths"]
                ]
                opcode = operation["opcode"]
                if opcode == "create":
                    create_retained_protected_entry(
                        targets[0], kind="file", mode=0o600, content=CONTENT, max_bytes=4096
                    )
                elif opcode == "rename":
                    rename_retained_protected_file(targets[0], targets[1])
                elif opcode == "unlink":
                    unlink_retained_protected_file(targets[0])
                else:
                    fsync_retained_protected_object(targets[0])
            after = observe(operation)
            document = {
                "domain": "synapse-protected-write.execution-observation.v1",
                "execution_start_sha256": execution.operation.response_sha256,
                "operation_id": operation["operation_id"],
                "step_index": index,
                "after": after,
            }
            encoded = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
            reference = evidence.write(encoded)

            def verify_observed(
                candidate: PreparedProtectedWrite,
                step: int,
                retained: Mapping[str, object],
                *,
                expected_step: int = index,
                expected_bytes: bytes = encoded,
                expected_operation: dict[str, Any] = operation,
                expected_after: list[dict[str, Any]] = after,
            ) -> bool:
                """Require exact stored bytes and a fresh matching native observation."""
                content = evidence.read(retained, 4096)
                return (
                    candidate is prepared
                    and step == expected_step
                    and content == expected_bytes
                    and hashlib.sha256(content).hexdigest() == retained["sha256"]
                    and observe(expected_operation) == expected_after
                )

            completed = record_protected_execution_step(
                prepared,
                execution,
                started.operation,
                step_index=index,
                phase="completed",
                service_journal=service,
                max_journal_operations=128,
                evidence_reference=reference,
                verify_completed=verify_observed,
            )
            previous = completed.operation
            records.append(previous)
        yield prepared, begun, service, tuple(records)
    finally:
        service.close()
        os.close(root)
        authority.close()
