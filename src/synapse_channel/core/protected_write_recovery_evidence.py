# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — immutable recovery observation custody
"""Persist known recovery observations outside the authority actor lock."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

from synapse_channel.core.protected_write_evidence_store import ProtectedExecutionEvidenceStore
from synapse_channel.core.protected_write_execution_observations import (
    _identity,
    verify_protected_execution_prefix_observations,
)
from synapse_channel.core.protected_write_inspection import (
    ProtectedFileInspection,
    _observed_before,
)
from synapse_channel.core.protected_write_json import decode_protected_write_json
from synapse_channel.core.protected_write_lifecycle import ProtectedWriteReservation
from synapse_channel.core.protected_write_preparation import PreparedProtectedWrite
from synapse_channel.core.protected_write_recovery_snapshot import (
    inspect_protected_recovery_snapshot,
)

_SCHEMA = "synapse-protected-write.recovery-snapshot.v1"
_FIELDS = {
    "schema_version",
    "reservation_id",
    "admission_request_sha256",
    "admission_result_sha256",
    "proposal_sha256",
    "begin_sequence",
    "begin_result_sha256",
    "position",
    "frontier_phase",
    "frontier_step",
    "execution_start_sha256",
    "completed_prefix_sha256",
    "observations",
}
_PATH_FIELDS = {"root_id", "relative_path", "directories", "identity", "state"}


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")


def _binding(
    prepared: PreparedProtectedWrite, begun: ProtectedWriteReservation
) -> dict[str, object]:
    if begun.admission != prepared.admission:
        raise ValueError("recovery evidence preparation/begin mismatch")
    return {
        "schema_version": _SCHEMA,
        "reservation_id": prepared.admission.reservation_id,
        "admission_request_sha256": hashlib.sha256(prepared.admission.request_bytes).hexdigest(),
        "admission_result_sha256": hashlib.sha256(prepared.admission.result_bytes).hexdigest(),
        "proposal_sha256": prepared.content.proposal_sha256,
        "begin_sequence": begun.transition_sequence,
        "begin_result_sha256": hashlib.sha256(begun.result_bytes).hexdigest(),
    }


@dataclass(frozen=True)
class FrozenProtectedRecoveryEvidence:
    """Immutable observed bytes and names, neither quiescence nor admission."""

    content: bytes
    observations: tuple[tuple[tuple[str, str], ProtectedFileInspection | None], ...]


def collect_protected_recovery_evidence(
    prepared: PreparedProtectedWrite,
    begun: ProtectedWriteReservation,
    *,
    evidence_store: ProtectedExecutionEvidenceStore,
    root_descriptors: Mapping[str, int],
    owner_uid: int,
    max_total_evidence_bytes: int,
    max_total_content_bytes: int,
) -> Mapping[str, object]:
    """Inspect and persist one parent-bound known-state packet outside the actor.

    Parameters
    ----------
    prepared:
        Trusted original preparation and exact enrolled roots.
    begun:
        Retained original durable begin, never a retry permit.
    evidence_store:
        Existing operator-enrolled immutable execution store and journal.
    root_descriptors:
        Borrowed exact enrolled roots retained throughout collection.
    owner_uid:
        Required owner for every inspected path component.
    max_total_evidence_bytes:
        Aggregate bound on historical completed-prefix observation reads.
    max_total_content_bytes:
        Aggregate bound on live descriptor-verified file contents.

    Returns
    -------
    Mapping[str, object]
        Existing ContentRef for immutable canonical recovery observations.

    Raises
    ------
    ValueError
        On unknown bytes, changed topology, binding, budget or store conflict.
    OSError
        On inaccessible descriptors or unavailable safe kernel resolution.

    Notes
    -----
    Run outside the actor lock after the supervisor establishes and retains
    quiescence/custody. This function does not prove either prerequisite. Its
    packet must not be accepted from an arbitrary client or a writable store.
    Failed collection stores no snapshot; prior execution evidence is preserved.
    """
    binding = _binding(prepared, begun)
    snapshot = inspect_protected_recovery_snapshot(
        prepared,
        begun,
        service_journal=evidence_store.journal,
        read_evidence=evidence_store.read,
        max_total_evidence_bytes=max_total_evidence_bytes,
        root_descriptors=root_descriptors,
        owner_uid=owner_uid,
        max_total_content_bytes=max_total_content_bytes,
    )
    paths = []
    for (root, path), observed in snapshot.observations:
        paths.append(
            {
                "root_id": root,
                "relative_path": path,
                "directories": None if observed is None else observed.directories,
                "identity": None if observed is None else observed.file_identity,
                "state": None if observed is None else _observed_before(observed),
            }
        )
    document = {
        **binding,
        "position": snapshot.position,
        "frontier_phase": snapshot.prefix.frontier.phase,
        "frontier_step": snapshot.prefix.frontier.step_index,
        "execution_start_sha256": snapshot.prefix.frontier.start.response_sha256,
        "completed_prefix_sha256": [
            hashlib.sha256(raw).hexdigest() for raw in snapshot.prefix.observations
        ],
        "observations": paths,
    }
    return evidence_store.write(_canonical(document))


def read_protected_recovery_evidence(
    reference: Mapping[str, object],
    *,
    prepared: PreparedProtectedWrite,
    begun: ProtectedWriteReservation,
    evidence_store: ProtectedExecutionEvidenceStore,
    max_bytes: int,
    max_total_evidence_bytes: int,
) -> FrozenProtectedRecoveryEvidence:
    """Prefetch immutable observations before actor-side recovery verification.

    Parameters
    ----------
    reference:
        Exact previously collected ContentRef in the enrolled evidence store.
    prepared:
        Retained original preparation, independent of the reference.
    begun:
        Exact original begin retained by the supervisor.
    evidence_store:
        Trusted immutable store; reads perform I/O outside the authority lock.
    max_bytes:
        Positive bounded packet read allowance, also limited by the store's blob cap.
    max_total_evidence_bytes:
        Independent aggregate cap for immutable historical prefix verification.

    Returns
    -------
    FrozenProtectedRecoveryEvidence
        Bytes and immutable path states for a separate pure admission verifier.

    Raises
    ------
    ValueError
        On changed content, parent binding, schema, paths, identity or frontier.
    FileNotFoundError
        If the immutable packet is absent.

    Notes
    -----
    The store authenticates custody only through its operator enrollment. Hashes
    do not establish truthful observations, current files or old-writer quiescence.
    The exact current retained frontier and completed observation hashes must
    match: further journal progress invalidates this packet for recovery use.
    No live-file reads occur, so physical custody remains an independent gate.
    This reader neither authorises a child proposal nor transfers custody.
    """
    raw = evidence_store.read(reference, max_bytes)
    source = decode_protected_write_json(raw, limits=prepared.policy.limits.json_limits)
    if set(source) != _FIELDS or _canonical(source) != raw:
        raise ValueError("unsupported recovery evidence schema or canonical bytes")
    if type(source["begin_sequence"]) is not int or any(
        source[field] != value for field, value in _binding(prepared, begun).items()
    ):
        raise ValueError("recovery evidence belongs to another parent or begin")
    step, phase = _checked_frontier(source, prepared)
    prefixes = _checked_prefix_hashes(
        source["completed_prefix_sha256"], step, source["execution_start_sha256"]
    )
    observations = _checked_observations(source["observations"], prepared)
    _check_deferred_ancestors(observations)
    prefix = verify_protected_execution_prefix_observations(
        prepared,
        begun,
        service_journal=evidence_store.journal,
        read_evidence=evidence_store.read,
        max_total_evidence_bytes=max_total_evidence_bytes,
    )
    if (
        prefix.frontier.phase != phase
        or prefix.frontier.step_index != step
        or prefix.frontier.start.response_sha256 != source["execution_start_sha256"]
        or [hashlib.sha256(content).hexdigest() for content in prefix.observations] != prefixes
    ):
        raise ValueError("recovery evidence differs from retained execution frontier")
    return FrozenProtectedRecoveryEvidence(raw, tuple(observations))


def _checked_frontier(
    source: dict[str, object], prepared: PreparedProtectedWrite
) -> tuple[int, str]:
    """Validate the recorded step and position without consulting live history."""
    step, phase, position = source["frontier_step"], source["frontier_phase"], source["position"]
    count = len(json.loads(prepared.admission.proposal_bytes)["auxiliary_operations"])
    if (
        type(phase) is not str
        or type(position) is not str
        or type(step) is not int
        or not 0 <= step <= count
        or phase not in {"before_step", "started", "failed", "unknown", "complete"}
        or position not in {"before_frontier", "after_frontier", "complete"}
        or (phase == "complete") != (step == count)
        or (position == "complete") != (phase == "complete")
        or (position == "after_frontier" and phase not in {"started", "failed", "unknown"})
    ):
        raise ValueError("invalid immutable recovery frontier")
    return step, phase


def _checked_prefix_hashes(prefixes: object, step: int, start: object) -> list[str]:
    """Validate bounded completed-prefix digests after validating the frontier index."""
    if (
        not isinstance(prefixes, list)
        or len(prefixes) != step
        or any(not _digest(item) for item in [start, *prefixes])
    ):
        raise ValueError("invalid immutable recovery frontier")
    return cast(list[str], prefixes)


def _checked_observed_metadata(
    state: object,
) -> tuple[dict[str, object], str, str | None]:
    """Decode one closed observed-kind/mode schema before interpreting content."""
    if (
        not isinstance(state, dict)
        or type(state.get("kind")) is not str
        or state.get("kind") not in {"absent", "file", "directory"}
    ):
        raise ValueError("invalid recovery observed state")
    kind = state["kind"]
    fields = {"kind"} if kind == "absent" else {"kind", "mode"}
    if kind == "file":
        fields |= {"sha256", "size_bytes"}
    if set(state) != fields:
        raise ValueError("invalid recovery observed state fields")
    mode = state.get("mode")
    if kind != "absent" and (
        not isinstance(mode, str) or len(mode) != 4 or any(c not in "01234567" for c in mode)
    ):
        raise ValueError("invalid recovery observed mode")
    return cast(dict[str, object], state), cast(str, kind), cast(str | None, mode)


def _checked_leaf_observation(
    state: object,
    identity: object,
    chain: tuple[tuple[int, int], ...],
    relative_path: str,
    max_content_bytes: int,
) -> ProtectedFileInspection:
    """Bind bounded observed bytes and object identity to a checked directory chain."""
    state, kind, mode = _checked_observed_metadata(state)
    size = state.get("size_bytes", 0)
    if kind == "file" and (
        not _digest(state["sha256"]) or type(size) is not int or not 0 <= size <= max_content_bytes
    ):
        raise ValueError("invalid recovery observed file content")
    if kind == "absent" and identity is not None:
        raise ValueError("absent recovery object carries identity")
    object_identity = None if kind == "absent" else _identity(identity)
    return ProtectedFileInspection(
        chain,
        object_identity,
        cast(str | None, state.get("sha256")),
        cast(int, size),
        None if mode is None else int(mode, 8),
        relative_path,
        kind == "directory",
    )


def _checked_observations(
    paths: object, prepared: PreparedProtectedWrite
) -> list[tuple[tuple[str, str], ProtectedFileInspection | None]]:
    """Bind each decoded path and parent chain to the original ordered observation set."""
    expected = [key for key, _ in prepared.observations]
    if not isinstance(paths, list) or len(paths) != len(expected):
        raise ValueError("recovery evidence path count mismatch")
    observations: list[tuple[tuple[str, str], ProtectedFileInspection | None]] = []
    for path, key in zip(paths, expected, strict=True):
        observations.append((key, _checked_path_observation(path, key, prepared)))
    return observations


def _checked_path_observation(
    path: object, key: tuple[str, str], prepared: PreparedProtectedWrite
) -> ProtectedFileInspection | None:
    """Decode one exact enrolled path with its checked directory/absence provenance."""
    if (
        not isinstance(path, dict)
        or set(path) != _PATH_FIELDS
        or (path["root_id"], path["relative_path"]) != key
    ):
        raise ValueError("recovery evidence path binding mismatch")
    state, directories, identity = path["state"], path["directories"], path["identity"]
    if state is None:
        if (
            directories is not None
            or identity is not None
            or dict(prepared.observations)[key] is not None
        ):
            raise ValueError("inconsistent deferred recovery absence")
        return None
    if not isinstance(directories, list) or len(directories) != len(key[1].split("/")):
        raise ValueError("invalid recovery directory chain")
    chain = tuple(_identity(item) for item in directories)
    if not chain or chain[0] != prepared.policy.enrolled_roots[key[0]]:
        raise ValueError("recovery evidence root differs from enrollment")
    return _checked_leaf_observation(
        state,
        identity,
        chain,
        key[1],
        prepared.policy.limits.operation_limits.max_content_bytes,
    )


def _check_deferred_ancestors(
    observations: list[tuple[tuple[str, str], ProtectedFileInspection | None]],
) -> None:
    """Require a checked absent ancestor for every deferred child observation."""
    by_key = dict(observations)
    for key, observed in observations:
        if observed is not None:
            continue
        parts = key[1].split("/")
        ancestors = [
            by_key.get((key[0], "/".join(parts[:index]))) for index in range(1, len(parts))
        ]
        if not any(parent is not None and parent.file_identity is None for parent in ancestors):
            raise ValueError("deferred recovery absence lacks checked absent ancestor")


def _digest(value: object) -> bool:
    return (
        isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)
    )
