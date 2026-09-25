# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — parent-bound recovery admission
"""Read-only POSIX inspection beneath an already enrolled directory descriptor."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass

from synapse_channel.core.protected_write_proposal import (
    ProtectedWriteProposalLimits,
    parse_protected_write_proposal,
)


@dataclass(frozen=True)
class ProtectedFileInspection:
    """Observed object identities and bytes, never a retained execution capability."""

    directories: tuple[tuple[int, int], ...]
    file_identity: tuple[int, int] | None
    sha256: str | None
    size_bytes: int
    mode: int | None
    relative_path: str
    is_directory: bool = False


def _observed_before(observed: ProtectedFileInspection) -> dict[str, object]:
    """Convert a trusted descriptor observation to the protocol state domain."""
    if observed.file_identity is None:
        return {"kind": "absent"}
    if observed.mode is None:
        raise ValueError("incomplete object observation")
    if observed.is_directory:
        return {"kind": "directory", "mode": f"{observed.mode:04o}"}
    if observed.sha256 is None:
        raise ValueError("incomplete file observation")
    return {
        "kind": "file",
        "sha256": observed.sha256,
        "size_bytes": observed.size_bytes,
        "mode": f"{observed.mode:04o}",
    }


def verify_protected_primary_before_states(
    proposal: str | bytes,
    *,
    limits: ProtectedWriteProposalLimits,
    inspections: Mapping[tuple[str, str], ProtectedFileInspection],
    enrolled_roots: Mapping[str, tuple[int, int]],
) -> tuple[ProtectedFileInspection, ...]:
    """Bind every intended file's observed initial state to the exact proposal.

    Parameters
    ----------
    proposal:
        Original complete proposal, strictly parsed before inspecting effects.
    limits:
        Explicit enrolled representation limits.
    inspections:
        Trusted descriptor observations keyed by root ID and exact relative path.
    enrolled_roots:
        Operator-pinned device/inode identities for the allowed root IDs.

    Returns
    -------
    tuple[ProtectedFileInspection, ...]
        Observations in primary-operation order, with exact root/path/before binding.

    Raises
    ------
    ValueError
        On missing, wrong-root/path or mismatched before-state observations.

    Notes
    -----
    This verifies initial primary file states only. It does not grant authority
    over auxiliary paths or parent directories, establish claim authorization,
    or close the check-to-execution race. Those independent gates remain required.
    """
    parsed = parse_protected_write_proposal(proposal, limits=limits)
    document = json.loads(parsed.canonical_bytes)
    bound: list[ProtectedFileInspection] = []
    for operation in document["operations"]:
        root_id, path = operation["root_id"], operation["relative_path"]
        observed = inspections.get((root_id, path))
        expected_root = enrolled_roots.get(root_id)
        if (
            observed is None
            or expected_root is None
            or not observed.directories
            or observed.relative_path != path
            or observed.directories[0] != expected_root
        ):
            raise ValueError("primary inspection root/path binding mismatch")
        actual = _observed_before(observed)
        if actual != operation["before"]:
            raise ValueError("primary file does not match declared before state")
        bound.append(observed)
    return tuple(bound)


def verify_protected_auxiliary_before_states(
    proposal: str | bytes,
    *,
    limits: ProtectedWriteProposalLimits,
    inspections: Mapping[tuple[str, str], ProtectedFileInspection],
    enrolled_roots: Mapping[str, tuple[int, int]],
) -> tuple[tuple[str, str], ...]:
    """Bind initial auxiliary states and children of planned new directories.

    Parameters
    ----------
    proposal:
        Complete strictly validated success-only operation chain.
    limits:
        Explicit enrolled representation limits.
    inspections:
        Trusted initial descriptor observations, not speculative future states.
    enrolled_roots:
        Operator-pinned root identities, independent of proposal declarations.

    Returns
    -------
    tuple[tuple[str, str], ...]
        Unique verified initial root/path keys in execution order.

    Raises
    ------
    ValueError
        On missing, misrouted or contradictory observations and unproved absence.

    Notes
    -----
    Missing child observations prove nothing by themselves. Absence may be derived
    only beneath an earlier validated mkdir of an initially absent directory.
    Unreadable existing ancestors never confer that exception. Repeat-path states
    are checked by the strict parser. This grants no effect permission and proves
    neither parent fsync, stable topology nor completed execution.
    """
    document = json.loads(parse_protected_write_proposal(proposal, limits=limits).canonical_bytes)
    seen: dict[tuple[str, str], None] = {}
    created: set[tuple[str, str]] = set()
    for operation in document["auxiliary_operations"]:
        for path in operation["paths"]:
            root_id, relative = path["root_id"], path["relative_path"]
            key = (root_id, relative)
            if key in seen:
                continue
            expected_root = enrolled_roots.get(root_id)
            if expected_root is None:
                raise ValueError("auxiliary root is not enrolled")
            observed = inspections.get(key)
            if observed is None:
                ancestor_created = (root_id, relative.rpartition("/")[0]) in created
                if path["before"] != {"kind": "absent"} or not ancestor_created:
                    raise ValueError("auxiliary initial state has no absence proof")
            elif (
                not observed.directories
                or observed.directories[0] != expected_root
                or observed.relative_path != relative
                or _observed_before(observed) != path["before"]
            ):
                raise ValueError("auxiliary initial observation mismatch")
            seen[key] = None
            if operation["opcode"] == "mkdir":
                created.add(key)
    return tuple(seen)


def verify_protected_parent_bindings(
    proposal: str | bytes,
    *,
    limits: ProtectedWriteProposalLimits,
    inspections: Mapping[tuple[str, str], ProtectedFileInspection],
    enrolled_roots: Mapping[str, tuple[int, int]],
    enrolled_parents: Mapping[tuple[str, str], tuple[str, str]],
) -> tuple[tuple[tuple[str, str], tuple[str, str]], ...]:
    """Verify physical immediate parents and retain planned-directory obligations.

    Parameters
    ----------
    proposal:
        Complete strictly validated success-only proposal.
    limits:
        Explicit enrolled representation limits.
    inspections:
        Trusted nofollow initial object/directory observations.
    enrolled_roots:
        Pinned device/inode identities.
    enrolled_parents:
        Declared exact target-to-parent keys, verified against physical identities.

    Returns
    -------
    tuple
        Deferred target/parent pairs beneath earlier planned mkdir operations.
        The executor must inspect their actual identities after directory creation.

    Raises
    ------
    ValueError
        On an unproven, wrong-object or incorrectly derived parent binding.

    Notes
    -----
    A parent reachable through another enrolled root is accepted only when its
    actual directory object matches the target's last opened parent descriptor.
    No textual ancestor prefix can substitute for that object comparison.
    New-directory dependencies retain exact same-root immediate-parent paths
    until they can be physically revalidated. This API performs no mutation.
    """
    verify_protected_auxiliary_before_states(
        proposal, limits=limits, inspections=inspections, enrolled_roots=enrolled_roots
    )
    document = json.loads(parse_protected_write_proposal(proposal, limits=limits).canonical_bytes)
    created: set[tuple[str, str]] = set()
    deferred: list[tuple[tuple[str, str], tuple[str, str]]] = []
    for operation in document["auxiliary_operations"]:
        if operation["opcode"] not in ("mkdir", "create", "rename", "unlink"):
            continue
        for path in operation["paths"]:
            key = (path["root_id"], path["relative_path"])
            parent_key = enrolled_parents.get(key)
            if parent_key is None:
                raise ValueError("entry mutation has no parent binding")
            target = inspections.get(key)
            if target is None:
                immediate = (key[0], key[1].rpartition("/")[0])
                if parent_key != immediate or parent_key not in created:
                    raise ValueError("planned target lacks exact immediate-parent provenance")
                deferred.append((key, parent_key))
            else:
                parent = inspections.get(parent_key)
                if (
                    parent is None
                    or not parent.is_directory
                    or parent.relative_path != parent_key[1]
                    or not parent.directories
                    or parent.directories[0] != enrolled_roots.get(parent_key[0])
                    or not target.directories
                    or parent.file_identity != target.directories[-1]
                ):
                    raise ValueError("physical immediate-parent identity mismatch")
            if operation["opcode"] == "mkdir":
                created.add(key)
    return tuple(deferred)


def inspect_protected_file(
    root_descriptor: int,
    relative_path: str,
    *,
    root_identity: tuple[int, int],
    max_bytes: int,
    allow_directory: bool = False,
) -> ProtectedFileInspection:
    """Inspect exact bytes without following aliases or reopening by pathname.

    Parameters
    ----------
    root_descriptor:
        Borrowed already enrolled directory descriptor, never closed by this API.
    relative_path:
        Strict relative POSIX path beneath that descriptor.
    root_identity:
        Operator-pinned device/inode identity, not selected by a client.
    max_bytes:
        Positive explicit byte budget for bounded inspection.
    allow_directory:
        Explicitly permit directory identity/mode observations, never child access.

    Returns
    -------
    ProtectedFileInspection
        Exact directory/object identities and raw-byte digest, or an absent leaf.

    Raises
    ------
    ValueError
        On malformed path/budget, wrong root, unsafe object or changing file.
    OSError
        If no-follow descriptor walking is unavailable or a component cannot open.

    Notes
    -----
    Run outside the authority actor lock. This performs no mutation or enrollment.
    It is a point-in-time observation, not TOCTOU protection for a future write:
    the isolated writer must retain/revalidate enrolled descriptors at execution.
    Symlinks, hard links, device/FIFO nodes and cross-device ancestor traversal
    are refused. Parent absence is not silently converted to target absence.
    """
    if type(max_bytes) is not int or not 0 < max_bytes < 2**53:
        raise ValueError("invalid inspection byte budget")
    if type(allow_directory) is not bool:
        raise ValueError("invalid directory inspection option")
    if (
        type(root_identity) is not tuple
        or len(root_identity) != 2
        or any(type(value) is not int or value < 0 for value in root_identity)
    ):
        raise ValueError("invalid pinned root identity")
    if (
        not isinstance(relative_path, str)
        or not relative_path
        or any(part in ("", ".", "..") for part in relative_path.split("/"))
        or any(ord(char) < 32 or ord(char) == 127 or char in "\\*?[]" for char in relative_path)
    ):
        raise ValueError("invalid protected relative path")
    if os.name != "posix" or not hasattr(os, "O_NOFOLLOW"):
        raise OSError("protected descriptor inspection unavailable")
    descriptor = os.dup(root_descriptor)
    directories: list[tuple[int, int]] = []
    try:
        root = os.fstat(descriptor)
        if not stat.S_ISDIR(root.st_mode) or (root.st_dev, root.st_ino) != root_identity:
            raise ValueError("protected root identity mismatch")
        directories.append(root_identity)
        parts = relative_path.split("/")
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
        for part in parts[:-1]:
            child = os.open(part, flags | os.O_DIRECTORY, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
            info = os.fstat(descriptor)
            if info.st_dev != root.st_dev:
                raise ValueError("protected path crosses enrolled device boundary")
            directories.append((info.st_dev, info.st_ino))
        try:
            leaf = os.open(parts[-1], flags, dir_fd=descriptor)
        except FileNotFoundError:
            return ProtectedFileInspection(tuple(directories), None, None, 0, None, relative_path)
        try:
            before = os.fstat(leaf)
            if allow_directory and stat.S_ISDIR(before.st_mode):
                if before.st_dev != root.st_dev:
                    raise ValueError("protected directory crosses enrolled device boundary")
                return ProtectedFileInspection(
                    tuple(directories),
                    (before.st_dev, before.st_ino),
                    None,
                    0,
                    stat.S_IMODE(before.st_mode),
                    relative_path,
                    True,
                )
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_nlink != 1
                or before.st_dev != root.st_dev
            ):
                raise ValueError("protected target is not a single-link enrolled regular file")
            if before.st_size > max_bytes:
                raise ValueError("protected file exceeds inspection budget")
            digest = hashlib.sha256()
            total = 0
            while True:
                chunk = os.read(leaf, min(65536, max_bytes + 1 - total))
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise ValueError("protected file grew beyond inspection budget")
                digest.update(chunk)
            after = os.fstat(leaf)
            fields = (
                "st_dev",
                "st_ino",
                "st_mode",
                "st_uid",
                "st_gid",
                "st_nlink",
                "st_size",
                "st_mtime_ns",
                "st_ctime_ns",
            )
            if total != before.st_size or any(
                getattr(before, field) != getattr(after, field) for field in fields
            ):
                raise ValueError("protected file changed during inspection")
            return ProtectedFileInspection(
                tuple(directories),
                (before.st_dev, before.st_ino),
                digest.hexdigest(),
                total,
                stat.S_IMODE(before.st_mode),
                relative_path,
            )
        finally:
            os.close(leaf)
    finally:
        os.close(descriptor)
