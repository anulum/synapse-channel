# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — success-dependent protected execution driver
"""Success-dependent execution over retained paths and one-time step records.

Trust, isolation and immutable evidence storage are explicit enrolled integration
seams. This module is not installed as a wire handler or activated service.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from contextlib import ExitStack
from types import MappingProxyType
from typing import Any

from synapse_channel.core.persistence import EventStore, OperationCommitResult
from synapse_channel.core.protected_write_descriptors import (
    ProtectedPathDescriptors,
    hold_protected_path,
)
from synapse_channel.core.protected_write_execution_journal import record_protected_execution_start
from synapse_channel.core.protected_write_execution_steps import record_protected_execution_step
from synapse_channel.core.protected_write_file_execution import (
    ProtectedDescriptorLocks,
    create_retained_protected_entry,
    fsync_retained_protected_object,
    overwrite_retained_protected_file,
    rename_retained_protected_file,
    unlink_retained_protected_file,
)
from synapse_channel.core.protected_write_inspection import (
    ProtectedFileInspection,
    _observed_before,
    inspect_protected_file,
)
from synapse_channel.core.protected_write_lifecycle import ProtectedWriteReservation
from synapse_channel.core.protected_write_operations import (
    validate_protected_write_content_reference,
)
from synapse_channel.core.protected_write_preparation import PreparedProtectedWrite
from synapse_channel.core.protected_write_proposal import parse_protected_write_proposal


def _dispatch(
    operation: dict[str, Any],
    targets: list[ProtectedPathDescriptors],
    content: Mapping[str, bytes],
    locks: ProtectedDescriptorLocks,
    max_bytes: int,
) -> None:
    opcode = operation["opcode"]
    target = targets[0]
    if opcode in ("create", "mkdir"):
        create_retained_protected_entry(
            target,
            kind="file" if opcode == "create" else "directory",
            mode=int(operation["paths"][0]["after"]["mode"], 8),
            content=content[operation["operation_id"]] if opcode == "create" else b"",
            max_bytes=max_bytes,
        )
    elif opcode == "write":
        overwrite_retained_protected_file(
            target, content[operation["operation_id"]], max_bytes=max_bytes
        )
    elif opcode == "rename":
        rename_retained_protected_file(target, targets[1])
    elif opcode == "unlink":
        unlink_retained_protected_file(target)
    elif opcode == "lock":
        locks.acquire(target)
    elif opcode == "unlock":
        locks.release(target)
    else:
        # The complete proposal is strictly parsed before consumption.
        fsync_retained_protected_object(target)


def _reference_verifier(
    expected: Mapping[str, object],
) -> Callable[[PreparedProtectedWrite, int, Mapping[str, object]], bool]:
    def verify(
        _prepared: PreparedProtectedWrite, _index: int, actual: Mapping[str, object]
    ) -> bool:
        return dict(actual) == dict(expected)

    return verify


def execute_prepared_protected_write(
    prepared: PreparedProtectedWrite,
    *,
    root_descriptors: Mapping[str, int],
    owner_uid: int,
    service_journal: EventStore,
    max_journal_operations: int,
    max_locks: int,
    max_evidence_bytes: int,
    verify_current: Callable[[], ProtectedWriteReservation],
    verify_isolation: Callable[[], bool],
    store_evidence: Callable[[bytes], Mapping[str, object]],
    read_evidence: Callable[[Mapping[str, object], int], bytes],
) -> tuple[OperationCommitResult, ...]:
    """Execute a prepared sequence once, checking physical states at every step.

    Parameters
    ----------
    prepared:
        Complete trusted admission, immutable bytes, permissions and observations.
    root_descriptors:
        Borrowed operator-enrolled root descriptors, copied before execution.
    owner_uid:
        Enrolled filesystem writer owner.
    service_journal:
        Separately enrolled bounded execution-evidence database.
    max_journal_operations:
        Positive total retained operation budget.
    max_locks:
        Positive retained lock descriptor budget.
    max_evidence_bytes:
        Positive per-step immutable evidence byte budget.
    verify_current:
        Trusted current authority verifier returning the exact still-open begin.
        Must perform the real actor-consistent begin/custody/trust check.
    verify_isolation:
        Enrolled OS-principal/topology verifier; requires exactly True.
    store_evidence:
        Enrolled immutable storage writer returning a ContentRef for supplied
        bytes. Its domain verifier must verify those exact bytes and digest.
    read_evidence:
        Enrolled bounded readback of that exact reference. Receives a read-only
        reference and maximum byte count; must return immutable bytes. A writer's
        returned reference alone never proves successful evidence storage.
        The read budget includes one overflow-detection byte beyond expected size.

    Returns
    -------
    tuple[OperationCommitResult, ...]
        Consumption followed by completed step records. Replayed/conflicted
        consumption returns alone and performs no filesystem operation.

    Raises
    ------
    ValueError
        On failed authority/isolation, before/after state, identity or evidence.
    OSError
        On physical I/O failure; partial contents are preserved.
    Exception
        On storage or verifier failure. A started step is marked unknown when
        possible; journal failure itself preserves the durable started record.

    Notes
    -----
    Run outside the authority lock. Only inserted started records execute.
    Failure stops immediately without cleanup or rollback. Result references are
    not a signed quiescence/settlement receipt. Real verifier/store enrollment and
    independent acceptance remain required before activating any live handler.
    """
    if type(max_evidence_bytes) is not int or not 0 < max_evidence_bytes < 2**53:
        raise ValueError("invalid execution evidence budget")
    roots = dict(root_descriptors)
    parsed = parse_protected_write_proposal(
        prepared.admission.proposal_bytes, limits=prepared.policy.limits
    )
    if parsed.proposal_sha256 != prepared.content.proposal_sha256:
        raise ValueError("prepared execution proposal digest mismatch")
    limits = prepared.policy.limits.operation_limits
    if verify_isolation() is not True:
        raise ValueError("protected writer isolation is not verified")
    begun = verify_current()
    locks = ProtectedDescriptorLocks(max_locks)
    try:
        execution = record_protected_execution_start(
            prepared,
            begun,
            service_journal=service_journal,
            max_journal_operations=max_journal_operations,
        )
        results = [execution]
        if execution.outcome != "inserted":
            return tuple(results)
        previous = execution.operation
        known = dict(prepared.observations)
        content = dict(prepared.content.operation_content)

        def observe(key: tuple[str, str]) -> ProtectedFileInspection:
            return inspect_protected_file(
                roots[key[0]],
                key[1],
                root_identity=prepared.policy.enrolled_roots[key[0]],
                max_bytes=limits.max_content_bytes,
                allow_directory=True,
            )

        for index, operation in enumerate(
            json.loads(parsed.canonical_bytes)["auxiliary_operations"]
        ):
            if verify_isolation() is not True or verify_current() != begun:
                raise ValueError("protected authority or isolation changed")
            entries = operation["paths"]
            keys = [(entry["root_id"], entry["relative_path"]) for entry in entries]
            before = [observe(key) for key in keys]
            for key, actual, entry in zip(keys, before, entries, strict=True):
                if _observed_before(actual) != entry["before"] or (
                    known.get(key) is not None and actual != known[key]
                ):
                    raise ValueError("execution before-state or identity changed")
                if operation["opcode"] in ("mkdir", "create", "rename", "unlink"):
                    parent_key = prepared.policy.enrolled_parents[key]
                    parent = observe(parent_key)
                    if (
                        parent != known[parent_key]
                        or parent.file_identity != actual.directories[-1]
                    ):
                        raise ValueError("execution immediate-parent binding changed")
            with ExitStack() as stack:
                targets = [
                    stack.enter_context(
                        hold_protected_path(
                            roots[key[0]],
                            actual,
                            owner_uid=owner_uid,
                            max_bytes=limits.max_content_bytes,
                            writable=operation["opcode"] == "write",
                        )
                    )
                    for key, actual in zip(keys, before, strict=True)
                ]
                started = record_protected_execution_step(
                    prepared,
                    execution,
                    previous,
                    step_index=index,
                    phase="started",
                    service_journal=service_journal,
                    max_journal_operations=max_journal_operations,
                )
                if started.outcome != "inserted":
                    raise ValueError("execution step already consumed or conflicted")
                try:
                    if verify_isolation() is not True or verify_current() != begun:
                        raise ValueError("protected authority or isolation changed before effect")
                    _dispatch(operation, targets, content, locks, limits.max_content_bytes)
                    after = [observe(key) for key in keys]
                    for position, (actual, initial, entry) in enumerate(
                        zip(after, before, entries, strict=True)
                    ):
                        if (
                            _observed_before(actual) != entry["after"]
                            or actual.directories != initial.directories
                        ):
                            raise ValueError("execution after-state or topology mismatch")
                        expected_identity = (
                            before[0].file_identity
                            if operation["opcode"] == "rename" and position == 1
                            else initial.file_identity
                        )
                        if operation["opcode"] in ("write", "fsync", "lock", "unlock") or (
                            operation["opcode"] == "rename" and position == 1
                        ):
                            if actual.file_identity != expected_identity:
                                raise ValueError("execution resulting object identity mismatch")
                    evidence = json.dumps(
                        {
                            "domain": "synapse-protected-write.execution-observation.v1",
                            "execution_start_sha256": execution.operation.response_sha256,
                            "operation_id": operation["operation_id"],
                            "step_index": index,
                            "after": [
                                {
                                    "root_id": key[0],
                                    "relative_path": key[1],
                                    "state": _observed_before(actual),
                                    "identity": actual.file_identity,
                                    "directories": actual.directories,
                                }
                                for key, actual in zip(keys, after, strict=True)
                            ],
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=True,
                        allow_nan=False,
                    ).encode("ascii")
                    if len(evidence) > max_evidence_bytes:
                        raise ValueError("execution result evidence exceeds budget")
                    reference = dict(store_evidence(evidence))
                    validate_protected_write_content_reference(reference, limits=limits)
                    verifier = prepared.policy.domain_verifiers[str(reference["domain"])]
                    if (
                        reference["size_bytes"] != len(evidence)
                        or verifier(evidence, str(reference["sha256"])) is not True
                    ):
                        raise ValueError("stored execution evidence verification failed")
                    retained = read_evidence(MappingProxyType(reference), len(evidence) + 1)
                    if type(retained) is not bytes or retained != evidence:
                        raise ValueError("execution evidence readback mismatch")
                    completed = record_protected_execution_step(
                        prepared,
                        execution,
                        started.operation,
                        step_index=index,
                        phase="completed",
                        service_journal=service_journal,
                        max_journal_operations=max_journal_operations,
                        evidence_reference=reference,
                        verify_completed=_reference_verifier(reference),
                    )
                    if completed.outcome != "inserted":
                        raise ValueError("execution result slot already consumed or conflicted")
                except Exception:
                    record_protected_execution_step(
                        prepared,
                        execution,
                        started.operation,
                        step_index=index,
                        phase="unknown",
                        service_journal=service_journal,
                        max_journal_operations=max_journal_operations,
                    )
                    raise
                known.update(zip(keys, after, strict=True))
                results.append(completed)
                previous = completed.operation
        return tuple(results)
    finally:
        locks.close()
