# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — known-state recovery observation
"""Compare current retained paths with an exact interrupted execution prefix."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protected_write_descriptors import hold_protected_path
from synapse_channel.core.protected_write_execution_observations import (
    ProtectedExecutionPrefixEvidence,
    _identity,
    _Topology,
    _verify_after,
    verify_protected_execution_prefix_observations,
)
from synapse_channel.core.protected_write_inspection import (
    ProtectedFileInspection,
    _observed_before,
    inspect_protected_file,
)
from synapse_channel.core.protected_write_lifecycle import ProtectedWriteReservation
from synapse_channel.core.protected_write_preparation import PreparedProtectedWrite

PathKey = tuple[str, str]
Location = tuple[tuple[int, int], str]


@dataclass(frozen=True)
class ProtectedRecoverySnapshot:
    """Known point-in-time files, neither quiescence nor recovery admission."""

    prefix: ProtectedExecutionPrefixEvidence
    position: Literal["before_frontier", "after_frontier", "complete"]
    observations: tuple[tuple[PathKey, ProtectedFileInspection | None], ...]


def _model(
    prepared: PreparedProtectedWrite, prefix: ProtectedExecutionPrefixEvidence
) -> tuple[_Topology, dict[Location, dict[str, Any]]]:
    """Reconstruct exact prefix names, inodes and contents from verified bytes."""
    topology = _Topology(prepared)
    states: dict[Location, dict[str, Any]] = {}
    for (_, path), observed in prepared.observations:
        if observed is None:
            continue
        location = observed.directories[-1], path.rsplit("/", 1)[-1]
        state = _observed_before(observed)
        if location in states and states[location] != state:
            raise ValueError("recovery prepared aliases disagree on bytes")
        states[location] = state
    operations = json.loads(prepared.admission.proposal_bytes)["auxiliary_operations"]
    for index, content in enumerate(prefix.observations):
        after = json.loads(content)["after"]
        topology.advance(operations[index], after)
        _update_states(states, after)
    return topology, states


def _update_states(states: dict[Location, dict[str, Any]], after: list[Any]) -> None:
    """Apply observed states through physical names, including root aliases."""
    for observed in after:
        location = (
            _identity(observed["directories"][-1]),
            observed["relative_path"].rsplit("/", 1)[-1],
        )
        states[location] = dict(observed["state"])


def _expected(
    topology: _Topology,
    states: Mapping[Location, dict[str, Any]],
    key: PathKey,
    keys: frozenset[PathKey],
    deferred: frozenset[PathKey],
) -> ProtectedFileInspection | None:
    """Resolve one modeled name or derive absence under a checked absent parent."""
    parts = key[1].split("/")
    directories = [topology.roots[key[0]]]
    for index, part in enumerate(parts[:-1]):
        location = directories[-1], part
        if location not in topology.entries:
            raise ValueError("recovery traverses an unmodeled parent")
        child = topology.entries[location]
        if child is None:
            ancestor = key[0], "/".join(parts[: index + 1])
            if ancestor not in keys or states.get(location) != {"kind": "absent"}:
                raise ValueError("recovery child has no checked absence ancestor")
            return None
        if child not in topology.directories:
            raise ValueError("recovery parent is not a modeled directory")
        directories.append(child)
    location = directories[-1], parts[-1]
    if location not in topology.entries and key in deferred:
        state: dict[str, Any] = {"kind": "absent"}
        identity = None
    elif location not in topology.entries or location not in states:
        raise ValueError("recovery path has no modeled state")
    else:
        state = states[location]
        identity = topology.entries[location]
    return ProtectedFileInspection(
        tuple(directories),
        identity,
        state.get("sha256"),
        state.get("size_bytes", 0),
        None if state["kind"] == "absent" else int(state["mode"], 8),
        key[1],
        state["kind"] == "directory",
    )


def _check_current(
    prepared: PreparedProtectedWrite,
    topology: _Topology,
    states: Mapping[Location, dict[str, Any]],
    roots: Mapping[str, int],
    owner_uid: int,
    budget: int,
) -> tuple[tuple[PathKey, ProtectedFileInspection | None], ...]:
    """Compare every declared path with the retained exact modeled snapshot."""
    keys = tuple(key for key, _ in prepared.observations)
    enrolled = frozenset(keys)
    deferred = frozenset(key for key, observed in prepared.observations if observed is None)
    result = []
    total = 0
    for key in keys:
        expected = _expected(topology, states, key, enrolled, deferred)
        if expected is not None:
            total += expected.size_bytes
            if total > budget:
                raise ValueError("recovery content exceeds aggregate budget")
            with hold_protected_path(
                roots[key[0]],
                expected,
                owner_uid=owner_uid,
                max_bytes=prepared.policy.limits.operation_limits.max_content_bytes,
                require_openat2=True,
            ):
                pass
        result.append((key, expected))
    return tuple(result)


def inspect_protected_recovery_snapshot(
    prepared: PreparedProtectedWrite,
    begun: ProtectedWriteReservation,
    *,
    service_journal: EventStore,
    read_evidence: Callable[[Mapping[str, object], int], bytes],
    max_total_evidence_bytes: int,
    root_descriptors: Mapping[str, int],
    owner_uid: int,
    max_total_content_bytes: int,
) -> ProtectedRecoverySnapshot:
    """Accept only exact known before/after states at an interrupted frontier.

    Parameters
    ----------
    prepared:
        Trusted original preparation with exact enrolled paths and initial states.
    begun:
        Retained original authority begin, not a current retry permit.
    service_journal:
        Enrolled retained execution journal, read outside the authority lock.
    read_evidence:
        Bounded immutable-store reader for completed-prefix observations.
    max_total_evidence_bytes:
        Positive aggregate cap for verified historical observation bytes.
    root_descriptors:
        Borrowed descriptors for exactly the enrolled roots.
    owner_uid:
        Enrolled filesystem owner required for every retained path component.
    max_total_content_bytes:
        Positive aggregate live-file byte cap, counting each declared path.

    Returns
    -------
    ProtectedRecoverySnapshot
        Verified prefix, before/after classification and exact current paths.
        None denotes a child proven absent beneath a checked absent ancestor.

    Raises
    ------
    ValueError
        On bad enrollment/budgets, damaged evidence, unknown bytes or topology.
    OSError
        On inaccessible descriptors, unsafe kernel path resolution or I/O failure.

    Notes
    -----
    Unknown partial bytes, unexpected inodes, topology, owners and modes refuse.
    Reads are sequential, not atomic. The recovery supervisor must establish and
    retain old-writer quiescence and filesystem custody before collecting this
    snapshot. It cannot authorize recovery or transfer custody by itself.
    """
    if (
        type(owner_uid) is not int
        or owner_uid < 0
        or type(max_total_content_bytes) is not int
        or not 0 < max_total_content_bytes < 2**53
        or set(root_descriptors) != set(prepared.policy.enrolled_roots)
    ):
        raise ValueError("invalid recovery roots, owner or content budget")
    roots = dict(root_descriptors)
    prefix = verify_protected_execution_prefix_observations(
        prepared,
        begun,
        service_journal=service_journal,
        read_evidence=read_evidence,
        max_total_evidence_bytes=max_total_evidence_bytes,
    )
    topology, states = _model(prepared, prefix)
    try:
        current = _check_current(
            prepared, topology, states, roots, owner_uid, max_total_content_bytes
        )
        position: Literal["before_frontier", "complete"] = (
            "complete" if prefix.frontier.phase == "complete" else "before_frontier"
        )
        return ProtectedRecoverySnapshot(prefix, position, current)
    except (ValueError, FileNotFoundError):
        if prefix.frontier.phase in {"before_step", "complete"}:
            raise
    operation = json.loads(prepared.admission.proposal_bytes)["auxiliary_operations"][
        prefix.frontier.step_index
    ]
    after = []
    for declared in operation["paths"]:
        key = declared["root_id"], declared["relative_path"]
        actual = inspect_protected_file(
            roots[key[0]],
            key[1],
            root_identity=prepared.policy.enrolled_roots[key[0]],
            max_bytes=prepared.policy.limits.operation_limits.max_content_bytes,
            allow_directory=True,
            require_openat2=True,
        )
        observed = {
            "root_id": key[0],
            "relative_path": key[1],
            "state": _observed_before(actual),
            "identity": actual.file_identity,
            "directories": [list(identity) for identity in actual.directories],
        }
        if observed["identity"] is not None:
            observed["identity"] = list(actual.file_identity or ())
        _verify_after(observed, declared, root=prepared.policy.enrolled_roots[key[0]])
        after.append(observed)
    topology.advance(operation, after)
    _update_states(states, after)
    current = _check_current(prepared, topology, states, roots, owner_uid, max_total_content_bytes)
    return ProtectedRecoverySnapshot(prefix, "after_frontier", current)
