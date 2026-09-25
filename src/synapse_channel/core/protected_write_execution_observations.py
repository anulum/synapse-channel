# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — retained execution observation verification
"""Verify historical observed bytes without certifying current state or quiescence."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import Any

from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protected_write_descriptors import hold_protected_path
from synapse_channel.core.protected_write_execution_journal import (
    verify_protected_execution_records,
)
from synapse_channel.core.protected_write_inspection import ProtectedFileInspection
from synapse_channel.core.protected_write_json import decode_protected_write_json
from synapse_channel.core.protected_write_lifecycle import ProtectedWriteReservation
from synapse_channel.core.protected_write_operations import (
    validate_protected_write_content_reference,
)
from synapse_channel.core.protected_write_preparation import PreparedProtectedWrite


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")


def _identity(value: object) -> tuple[int, int]:
    if (
        not isinstance(value, list)
        or len(value) != 2
        or any(type(part) is not int or part < 0 for part in value)
    ):
        raise ValueError("invalid retained filesystem identity")
    return value[0], value[1]


def _verify_after(
    actual: object,
    declared: Mapping[str, object],
    *,
    root: tuple[int, int],
) -> None:
    if not isinstance(actual, dict) or set(actual) != {
        "root_id",
        "relative_path",
        "state",
        "identity",
        "directories",
    }:
        raise ValueError("unsupported retained observation path fields")
    if (
        actual["root_id"] != declared["root_id"]
        or actual["relative_path"] != declared["relative_path"]
        or _canonical(actual["state"]) != _canonical(declared["after"])
    ):
        raise ValueError("retained observation differs from declared effect")
    directories = actual["directories"]
    if (
        not isinstance(directories, list)
        or len(directories) != len(str(declared["relative_path"]).split("/"))
        or not directories
    ):
        raise ValueError("retained observation directory chain differs from path")
    identities = tuple(_identity(item) for item in directories)
    if identities[0] != root:
        raise ValueError("retained observation root differs from enrollment")
    if actual["state"] == {"kind": "absent"}:
        # Actual descriptor inspection represents absence with no object inode.
        # A retained absent state carrying one is inconsistent evidence.
        if actual["identity"] is not None:
            raise ValueError("absent retained object carries a filesystem identity")
    else:
        _identity(actual["identity"])


class _Topology:
    """Track prepared physical names independently of root aliases."""

    def __init__(self, prepared: PreparedProtectedWrite) -> None:
        self.roots = prepared.policy.enrolled_roots
        self.entries: dict[tuple[tuple[int, int], str], tuple[int, int] | None] = {}
        self.directories = set(self.roots.values())
        for (_, path), observed in prepared.observations:
            # Preparation permits None only for a child of a planned mkdir;
            # parent binding is checked before this topology is constructed.
            if observed is None:
                continue
            parts = path.split("/")
            self.directories.update(observed.directories)
            for index, child in enumerate(observed.directories[1:]):
                self._remember((observed.directories[index], parts[index]), child)
            self._remember((observed.directories[-1], parts[-1]), observed.file_identity)
            if observed.is_directory and observed.file_identity is not None:
                self.directories.add(observed.file_identity)

    def _remember(
        self, location: tuple[tuple[int, int], str], identity: tuple[int, int] | None
    ) -> None:
        if location in self.entries and self.entries[location] != identity:
            raise ValueError("prepared physical aliases disagree")
        self.entries[location] = identity

    def advance(self, operation: Mapping[str, Any], after: list[Any]) -> None:
        locations = []
        initial = []
        resulting = []
        for declared, observed in zip(operation["paths"], after, strict=True):
            parts = declared["relative_path"].split("/")
            directories = [self.roots[declared["root_id"]]]
            for part in parts[:-1]:
                child = self.entries.get((directories[-1], part))
                if child is None or child not in self.directories:
                    raise ValueError("observation traverses an unproven directory")
                directories.append(child)
            # The held descriptor chain must match the modeled parent chain;
            # a different inode means a path substitution, not the same effect.
            if tuple(_identity(item) for item in observed["directories"]) != tuple(directories):
                raise ValueError("observation changed a retained directory identity")
            location = (directories[-1], parts[-1])
            before = self.entries.get(location)
            if (before is None) != (declared["before"] == {"kind": "absent"}):
                raise ValueError("observation initial object identity disagrees with plan")
            locations.append(location)
            initial.append(before)
            resulting.append(
                None if observed["identity"] is None else _identity(observed["identity"])
            )
        opcode = operation["opcode"]
        for index, identity in enumerate(resulting):
            if opcode in ("write", "fsync", "lock", "unlock"):
                if identity != initial[index]:
                    raise ValueError("observation changed an unchanged object identity")
            elif opcode == "rename" and index == 1:
                # A successful rename moves the source inode to the destination.
                if identity != initial[0]:
                    raise ValueError("rename destination did not retain source identity")
            elif opcode in ("create", "mkdir"):
                # Exclusive creation must yield a new live inode, not alias an
                # existing retained object or directory in this topology.
                if identity in self.directories or identity in self.entries.values():
                    raise ValueError("new object aliases an existing physical object")
        for location, identity in zip(locations, resulting, strict=True):
            self.entries[location] = identity
            if opcode == "mkdir" and identity is not None:
                self.directories.add(identity)


def verify_protected_execution_observations(
    prepared: PreparedProtectedWrite,
    begun: ProtectedWriteReservation,
    *,
    service_journal: EventStore,
    read_evidence: Callable[[Mapping[str, object], int], bytes],
    max_total_evidence_bytes: int,
) -> tuple[bytes, ...]:
    """Read and bind every observation to an exact complete execution chain.

    Parameters
    ----------
    prepared:
        Trusted immutable preparation with enrolled domain verifiers and roots.
    begun:
        Exact trusted begin, not an arbitrary client-supplied reservation.
    service_journal:
        Existing enrolled journal containing the complete execution chain.
    read_evidence:
        Bounded immutable-store reader. Reference paths are never resolved here.
        Receives a copied read-only ContentRef and declared size plus one byte.
    max_total_evidence_bytes:
        Positive exact aggregate bound; counts every reference occurrence.

    Returns
    -------
    tuple[bytes, ...]
        Exact canonical historical observation bytes in executable operation order.
        This return value is neither an activation receipt nor a write capability.

    Raises
    ------
    ValueError
        On partial execution, bad reference/domain/bytes, noncanonical observation,
        wrong operation or declared postcondition, root or identity shape.

    Notes
    -----
    Trust in the enrolled store and writer is a prerequisite. Valid hashes and
    journal entries alone do not prove that historical observations were truthful.
    This verifies closed schema, declared effects and identity continuity from
    preparation through every step, including root aliases and deferred parents.
    It does not verify current physical topology, final live contents, process
    termination or quiescence. Settlement must check those separately.
    No database write, network fetch, repair, cleanup or settlement is performed.
    """
    if type(max_total_evidence_bytes) is not int or not 0 < max_total_evidence_bytes < 2**53:
        raise ValueError("invalid aggregate execution evidence budget")
    records = verify_protected_execution_records(prepared, begun, service_journal=service_journal)
    operations = json.loads(prepared.admission.proposal_bytes)["auxiliary_operations"]
    topology = _Topology(prepared)
    result = []
    total = 0
    for index, (record, operation) in enumerate(zip(records[1:], operations, strict=True)):
        reference = dict(record.response["evidence_reference"])
        size = validate_protected_write_content_reference(
            reference, limits=prepared.policy.limits.operation_limits
        )
        total += size
        if total > max_total_evidence_bytes:
            raise ValueError("aggregate execution evidence exceeds budget")
        verifier = prepared.policy.domain_verifiers.get(reference["domain"])
        if verifier is None:
            raise ValueError("execution evidence domain verifier is unavailable")
        content = read_evidence(MappingProxyType(reference), size + 1)
        if (
            type(content) is not bytes
            or len(content) != size
            or verifier(content, reference["sha256"]) is not True
        ):
            raise ValueError("retained execution evidence bytes differ from reference")
        document = decode_protected_write_json(content, limits=prepared.policy.limits.json_limits)
        if (
            set(document)
            != {"domain", "execution_start_sha256", "operation_id", "step_index", "after"}
            or _canonical(document) != content
        ):
            raise ValueError("unsupported or noncanonical execution observation")
        if (
            document["domain"] != "synapse-protected-write.execution-observation.v1"
            or document["execution_start_sha256"] != records[0].response_sha256
            or document["operation_id"] != operation["operation_id"]
            or type(document["step_index"]) is not int
            or document["step_index"] != index
        ):
            raise ValueError("execution observation belongs to another operation")
        after = document["after"]
        if not isinstance(after, list) or len(after) != len(operation["paths"]):
            raise ValueError("execution observation path count differs")
        for actual, declared in zip(after, operation["paths"], strict=True):
            _verify_after(
                actual, declared, root=prepared.policy.enrolled_roots[declared["root_id"]]
            )
        topology.advance(operation, after)
        result.append(content)
    return tuple(result)


def verify_protected_execution_final_state(
    prepared: PreparedProtectedWrite,
    begun: ProtectedWriteReservation,
    *,
    service_journal: EventStore,
    read_evidence: Callable[[Mapping[str, object], int], bytes],
    max_total_evidence_bytes: int,
    root_descriptors: Mapping[str, int],
    owner_uid: int,
    max_total_content_bytes: int,
) -> tuple[tuple[tuple[str, str], ProtectedFileInspection], ...]:
    """Compare every final declared path with current descriptor-bound storage.

    Parameters
    ----------
    prepared:
        Independently verified preparation with enrolled identities and budgets.
    begun:
        Exact trusted begin binding the retained execution, not a fresh permit.
    service_journal:
        Enrolled journal of the completed execution.
    read_evidence:
        Enrolled bounded immutable evidence reader.
    max_total_evidence_bytes:
        Aggregate bound passed to complete observation verification.
    root_descriptors:
        Borrowed descriptors for exactly the prepared enrolled roots.
    owner_uid:
        Enrolled owner of all traversed directories and objects.
    max_total_content_bytes:
        Positive exact aggregate bound for final file sizes. Aliased declared
        paths count separately because each receives its own descriptor check.

    Returns
    -------
    tuple[tuple[tuple[str, str], ProtectedFileInspection], ...]
        Root/path keys and matched snapshots in first-observation order. The
        caller retains its root descriptors; this function closes only its copies.

    Raises
    ------
    ValueError
        On incomplete evidence, invalid budgets/enrollment, changed final bytes,
        modes, owners, identities, attachments, unsafe links or an absent target.
    OSError
        If a descriptor or filesystem operation fails.

    Notes
    -----
    Checks are sequential point-in-time observations, not an atomic snapshot or
    proof of writer termination. The caller must establish quiescence and retain
    namespace custody before treating them as settlement evidence. This function
    acquires no write lock and performs no mutation, repair, cleanup or settlement.
    Root descriptors are pinned; their external pathname attachment is a separate
    deployment check. Names outside declared execution paths are not inventoried.
    """
    if (
        type(max_total_content_bytes) is not int
        or not 0 < max_total_content_bytes < 2**53
        or type(owner_uid) is not int
        or owner_uid < 0
    ):
        raise ValueError("invalid final-state owner or content budget")
    roots = dict(root_descriptors)
    if set(roots) != set(prepared.policy.enrolled_roots):
        raise ValueError("final-state root descriptors differ from enrollment")
    observations = verify_protected_execution_observations(
        prepared,
        begun,
        service_journal=service_journal,
        read_evidence=read_evidence,
        max_total_evidence_bytes=max_total_evidence_bytes,
    )
    named: dict[tuple[str, str], dict[str, Any]] = {}
    physical: dict[tuple[tuple[int, int], str], dict[str, Any]] = {}
    for content in observations:
        for observed in json.loads(content)["after"]:
            key = observed["root_id"], observed["relative_path"]
            location = _identity(observed["directories"][-1]), key[1].rsplit("/", 1)[-1]
            named[key] = observed
            physical[location] = observed
    total = 0
    result = []
    for key, observed in named.items():
        location = _identity(observed["directories"][-1]), key[1].rsplit("/", 1)[-1]
        latest = physical[location]
        state = latest["state"]
        size = state.get("size_bytes", 0)
        total += size
        if total > max_total_content_bytes:
            raise ValueError("final-state content exceeds aggregate budget")
        expected = ProtectedFileInspection(
            tuple(_identity(item) for item in observed["directories"]),
            None if latest["identity"] is None else _identity(latest["identity"]),
            state.get("sha256"),
            size,
            None if state["kind"] == "absent" else int(state["mode"], 8),
            key[1],
            state["kind"] == "directory",
        )
        with hold_protected_path(
            roots[key[0]],
            expected,
            owner_uid=owner_uid,
            max_bytes=prepared.policy.limits.operation_limits.max_content_bytes,
        ):
            result.append((key, expected))
    return tuple(result)
