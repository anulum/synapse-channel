# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — protected writer kernel boundary evidence
"""Read and compare live Linux principal, namespace and mount evidence."""

from __future__ import annotations

import hashlib
import os
import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from synapse_channel.core.protected_write_cgroup_controls import verify_protected_cgroup_controls
from synapse_channel.core.protected_write_mounts import (
    ProtectedWriterMount,
    parse_protected_writer_mounts,
    verify_protected_writer_mounts,
)
from synapse_channel.core.protected_write_subtree import (
    ProtectedWriterSubtreeObservation,
    capture_protected_writer_subtree,
)

_STATUS_FIELDS = (
    "Uid",
    "Gid",
    "Groups",
    "CapInh",
    "CapPrm",
    "CapEff",
    "CapBnd",
    "CapAmb",
    "NoNewPrivs",
    "Seccomp",
)
_MAX_STATUS_BYTES = 65_536
_MAX_MOUNTINFO_BYTES = 1_048_576
_MAX_FDINFO_BYTES = 4_096


@dataclass(frozen=True)
class ProtectedRootKernelIdentity:
    """One retained descriptor's inode and namespace-local mount ID."""

    name: str
    device: int
    inode: int
    mount_id: int


@dataclass(frozen=True)
class ProtectedWriterKernelEvidence:
    """Point-in-time kernel observations, never an execution permit."""

    uids: tuple[int, int, int, int]
    gids: tuple[int, int, int, int]
    groups: tuple[int, ...]
    capabilities: tuple[int, int, int, int, int]
    no_new_privs: int
    seccomp: int
    mount_namespace: tuple[int, int]
    mountinfo_sha256: str
    roots: tuple[ProtectedRootKernelIdentity, ...]
    mounts: tuple[ProtectedWriterMount, ...]
    process_subtree: ProtectedWriterSubtreeObservation


def _read_bounded(path: Path, maximum: int) -> bytes:
    """Read one kernel record and refuse bytes beyond the enrolled budget."""
    with path.open("rb") as stream:
        raw = stream.read(maximum + 1)
    if len(raw) > maximum:
        raise ValueError("writer kernel evidence exceeds read budget")
    return raw


def _parse_status(raw: bytes) -> dict[str, str]:
    """Require each supported proc status field exactly once."""
    result: dict[str, str] = {}
    for line in raw.decode("ascii").splitlines():
        name, separator, value = line.partition(":")
        if separator and name in _STATUS_FIELDS:
            if name in result:
                raise ValueError("duplicate writer process status field")
            result[name] = value.strip()
    if set(result) != set(_STATUS_FIELDS):
        raise ValueError("incomplete writer process status evidence")
    return result


def _status() -> dict[str, str]:
    """Capture bounded current-process status with every required field."""
    return _parse_status(_read_bounded(Path("/proc/self/status"), _MAX_STATUS_BYTES))


def _four(value: str) -> tuple[int, int, int, int]:
    """Decode the four real, effective, saved and filesystem principal IDs."""
    parts = value.split()
    if len(parts) != 4 or any(not part.isdecimal() for part in parts):
        raise ValueError("invalid writer principal status evidence")
    return int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])


def _parse_mount_id(raw: bytes) -> int:
    """Require one positive mount ID in a descriptor's proc status."""
    values = [
        line.partition(":")[2].strip()
        for line in raw.decode("ascii").splitlines()
        if line.startswith("mnt_id:")
    ]
    if len(values) != 1 or not values[0].isdecimal() or int(values[0]) <= 0:
        raise ValueError("writer root mount ID is unavailable")
    return int(values[0])


def _mount_id(descriptor: int) -> int:
    """Read the actual mount ID associated with a borrowed descriptor."""
    return _parse_mount_id(
        _read_bounded(Path(f"/proc/self/fdinfo/{descriptor}"), _MAX_FDINFO_BYTES)
    )


def _capture_root_kernel_identities(
    root_descriptors: Mapping[str, int],
    mounts_by_id: Mapping[int, ProtectedWriterMount],
) -> tuple[ProtectedRootKernelIdentity, ...]:
    """Bind each borrowed directory descriptor to its unchanged namespace mount/inode."""
    roots = []
    for name, descriptor in sorted(root_descriptors.items()):
        before = os.fstat(descriptor)
        if not stat.S_ISDIR(before.st_mode):
            raise ValueError("writer enrolled root is not a directory")
        mount_id = _mount_id(descriptor)
        mount = mounts_by_id.get(mount_id)
        if mount is None or mount.device != (os.major(before.st_dev), os.minor(before.st_dev)):
            raise ValueError("writer root mount is outside current namespace")
        after = os.fstat(descriptor)
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise ValueError("writer root descriptor changed during capture")
        roots.append(ProtectedRootKernelIdentity(name, before.st_dev, before.st_ino, mount_id))
    return tuple(roots)


def capture_protected_writer_kernel_evidence(
    root_descriptors: Mapping[str, int],
    *,
    subtree_descriptor: int | None = None,
) -> ProtectedWriterKernelEvidence:
    """Capture actual process and retained-root evidence without changing state.

    Parameters
    ----------
    root_descriptors:
        Enrolled directory descriptors held open for the whole capture.
    subtree_descriptor:
        Independently supervisor-retained cgroup descriptor. When absent, capture
        observes current membership for diagnostics, without proving custody.

    Returns
    -------
    ProtectedWriterKernelEvidence
        Actual principal, namespace, root, mount and process-subtree observations.

    Raises
    ------
    ValueError
        On invalid roots, another subtree or changed kernel observations.
    OSError
        On inaccessible kernel files or retained descriptors.

    Notes
    -----
    The mount table and process subtree must remain unchanged across capture.
    This does not prove root-owned installation, ancestor or descriptor custody,
    permitted mounts, migration controls or distinct writer identity by itself.
    """
    if not root_descriptors or any(
        type(name) is not str or not name or type(descriptor) is not int or descriptor < 0
        for name, descriptor in root_descriptors.items()
    ):
        raise ValueError("writer kernel evidence requires enrolled root descriptors")
    subtree = capture_protected_writer_subtree(subtree_descriptor)
    status = _status()
    uids = _four(status["Uid"])
    gids = _four(status["Gid"])
    if uids[:3] != os.getresuid() or gids[:3] != os.getresgid():
        raise ValueError("writer principal evidence changed during capture")
    groups = tuple(int(part) for part in status["Groups"].split())
    if groups != tuple(os.getgroups()):
        raise ValueError("writer supplementary groups changed during capture")
    capabilities = (
        int(status["CapInh"], 16),
        int(status["CapPrm"], 16),
        int(status["CapEff"], 16),
        int(status["CapBnd"], 16),
        int(status["CapAmb"], 16),
    )
    namespace = Path("/proc/self/ns/mnt").stat()
    mountinfo = _read_bounded(Path("/proc/self/mountinfo"), _MAX_MOUNTINFO_BYTES)
    mounts = parse_protected_writer_mounts(mountinfo)
    mounts_by_id = {mount.mount_id: mount for mount in mounts}
    roots = _capture_root_kernel_identities(root_descriptors, mounts_by_id)
    final_namespace = Path("/proc/self/ns/mnt").stat()
    if (namespace.st_dev, namespace.st_ino) != (final_namespace.st_dev, final_namespace.st_ino) or (
        _read_bounded(Path("/proc/self/mountinfo"), _MAX_MOUNTINFO_BYTES) != mountinfo
    ):
        raise ValueError("writer mount namespace changed during capture")
    if capture_protected_writer_subtree(subtree_descriptor) != subtree:
        raise ValueError("writer process subtree changed during kernel capture")
    return ProtectedWriterKernelEvidence(
        uids=uids,
        gids=gids,
        groups=groups,
        capabilities=capabilities,
        no_new_privs=int(status["NoNewPrivs"]),
        seccomp=int(status["Seccomp"]),
        mount_namespace=(namespace.st_dev, namespace.st_ino),
        mountinfo_sha256=hashlib.sha256(mountinfo).hexdigest(),
        roots=roots,
        mounts=mounts,
        process_subtree=subtree,
    )


def _require_dedicated_writer_principal(writer_uid: int, writer_gid: int, shared_uid: int) -> None:
    """Require non-root integral writer credentials distinct from shared agents."""
    if (
        any(type(value) is not int or value < 0 for value in (writer_uid, writer_gid, shared_uid))
        or writer_uid in {0, 1000, shared_uid}
        or writer_gid == 0
    ):
        raise ValueError("invalid dedicated writer kernel policy")


def _require_mount_namespace_identity(identity: tuple[int, int]) -> None:
    """Require immutable exact integer kernel device/inode namespace enrollment."""
    if (
        type(identity) is not tuple
        or len(identity) != 2
        or type(identity[0]) is not int
        or identity[0] < 0
        or type(identity[1]) is not int
        or identity[1] <= 0
    ):
        raise ValueError("invalid dedicated writer kernel policy")


@dataclass(frozen=True)
class ProtectedWriterKernelPolicy:
    """Trusted owner-reviewed expected process and mount snapshot."""

    writer_uid: int
    writer_gid: int
    shared_uid: int
    host_mount_namespace: tuple[int, int]
    writer_mount_namespace: tuple[int, int]
    mountinfo_sha256: str
    roots: tuple[ProtectedRootKernelIdentity, ...]

    writable_mount_targets: frozenset[str]
    writer_subtree_identity: tuple[int, int]

    def __post_init__(self) -> None:
        """Reject shared, root or unspecified writer boundaries."""
        _require_dedicated_writer_principal(self.writer_uid, self.writer_gid, self.shared_uid)
        _require_mount_namespace_identity(self.host_mount_namespace)
        _require_mount_namespace_identity(self.writer_mount_namespace)
        if (
            self.writer_mount_namespace == self.host_mount_namespace
            or re.fullmatch("[0-9a-f]{64}", self.mountinfo_sha256) is None
            or (not self.roots)
            or (len({item.name for item in self.roots}) != len(self.roots))
            or (type(self.writable_mount_targets) is not frozenset)
            or (type(self.writer_subtree_identity) is not tuple)
            or (len(self.writer_subtree_identity) != 2)
            or any(type(value) is not int or value <= 0 for value in self.writer_subtree_identity)
        ):
            raise ValueError("invalid dedicated writer kernel policy")


def verify_protected_writer_kernel_boundary(
    evidence: ProtectedWriterKernelEvidence,
    policy: ProtectedWriterKernelPolicy,
) -> bool:
    """Require exact kernel facts before a separate install/OS custody proof.

    A true result covers only these live kernel facts. It cannot certify the
    reviewed code snapshot, root parent chain or
    exclusion of same-UID processes. Those must be independently enforced and
    checked before this becomes the driver's full isolation predicate. The mount
    portion also requires a read-only base, private propagation and exact
    nosuid/nodev/noexec writable targets; a digest alone does not prove these.
    The current process must match the enrolled root-owned subtree inode. Root
    ownership alone does not prove migration controls or descriptor isolation.
    """
    if (
        evidence.uids != (policy.writer_uid,) * 4
        or evidence.gids != (policy.writer_gid,) * 4
        or evidence.groups
        or any(evidence.capabilities)
        or evidence.no_new_privs != 1
        or evidence.seccomp != 2
        or evidence.mount_namespace != policy.writer_mount_namespace
        or evidence.mountinfo_sha256 != policy.mountinfo_sha256
        or evidence.roots != policy.roots
        or (evidence.process_subtree.device, evidence.process_subtree.inode)
        != policy.writer_subtree_identity
        or evidence.process_subtree.owner_uid != 0
    ):
        raise ValueError("writer kernel boundary differs from reviewed enrollment")
    verify_protected_cgroup_controls(evidence.process_subtree.controls)
    verify_protected_writer_mounts(evidence.mounts, writable_targets=policy.writable_mount_targets)
    return True
