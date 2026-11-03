# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — root-owned finite prerequisite process subtrees
"""Own fresh cgroup v2 leaves, bound resources and stop every owned descendant."""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from synapse_channel.core.protected_write_cgroup_controls import (
    capture_protected_cgroup_controls,
    verify_protected_cgroup_controls,
)
from synapse_channel.core.protected_write_subtree_stop import stop_protected_writer_subtree

from . import require_native_linux

_ROOT = Path("/sys/fs/cgroup")
_ROLES = ("authority", "operator", "writer", "client")


async def finalize_probe_resources(
    cleanup: Awaitable[None],
    *,
    retire: Callable[[], None],
    publish: Callable[[], None],
    failed: Callable[[BaseException], None],
) -> None:
    """Retire owned endpoints and publish an outcome even if cleanup is cancelled.

    Parameters
    ----------
    cleanup:
        Already constructed cleanup operation for the actual owned children.
    retire:
        Synchronous closure of retained descriptors and ephemeral endpoints.
    publish:
        Synchronous publication of the final outcome after retirement.
    failed:
        Record any interruption or retirement failure before publishing.

    Raises
    ------
    BaseException
        Propagates cleanup, retirement or publication failure after attempting
        the remaining finalization steps. Cancellation never grades success.
    """
    try:
        try:
            await cleanup
        except BaseException as error:
            failed(error)
            raise
    finally:
        try:
            retire()
        except BaseException as error:
            failed(error)
            raise
        finally:
            publish()


def _root_only() -> None:
    """Refuse account-level mutation before any cgroup directory is created."""
    require_native_linux(sys.platform, operation="prerequisite resource controls")
    if os.getresuid() != (0, 0, 0) or os.getresgid() != (0, 0, 0):
        raise PermissionError("prerequisite resource controls require the root operator")


def _directory_flags() -> int:
    """Resolve native descriptor flags only after the Linux/root entry guard."""
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


def _read(directory: int, name: str) -> str:
    """Read bounded kernel control content through the retained cgroup directory."""
    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory)
    try:
        raw = os.read(descriptor, 4097)
        if len(raw) > 4096:
            raise ValueError("prerequisite cgroup control exceeds its read bound")
        return raw.decode("ascii").strip()
    finally:
        os.close(descriptor)


def _write(directory: int, name: str, value: str) -> None:
    """Require one complete write to an exact root-owned kernel control."""
    descriptor = os.open(name, os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory)
    try:
        metadata = os.fstat(descriptor)
        if metadata.st_uid or metadata.st_gid or metadata.st_mode & 0o022:
            raise ValueError("prerequisite cgroup control is not exclusively root owned")
        raw = value.encode("ascii")
        if os.write(descriptor, raw) != len(raw):
            raise OSError("prerequisite kernel control write was incomplete")
    finally:
        os.close(descriptor)


@dataclass(frozen=True)
class ResourceLeaf:
    """Root-retained kernel cgroup identity and complete finite resource readback."""

    name: str
    descriptor: int
    identity: tuple[int, int]
    controls: tuple[tuple[str, str], ...]


class ResourceScope:
    """Retain all qualification cgroups independently of their child processes.

    Notes
    -----
    Construct with ``create_resource_scope``. The root controller remains outside
    these groups. All descendants must inherit their leaf before executing the
    authority, client or namespace builder. This object grants no operation authority.
    """

    def __init__(self, parent: int, name: str, leaves: tuple[ResourceLeaf, ...]) -> None:
        """Retain only the newly created root-owned scope and its exact leaf FDs."""
        self.parent = parent
        self.name = name
        self.leaves = leaves
        self.closed = False

    def path(self, role: str) -> Path:
        """Return one owned host path for a root child to enter before spawning.

        Parameters
        ----------
        role:
            One of the four exact prerequisite role names without a prefix.

        Returns
        -------
        Path
            Absolute kernel cgroup path, never a caller-selected group.

        Raises
        ------
        ValueError
            On an unknown role or a closed scope.
        """
        if self.closed or role not in {leaf.name for leaf in self.leaves}:
            raise ValueError("unavailable prerequisite resource leaf")
        return _ROOT / self.name / role

    def close(self) -> None:
        """Stop all owned descendants before removing only fresh empty groups.

        Raises
        ------
        RuntimeError
            If any stop, empty-state verification or exact group removal fails.
            Every other owned leaf is still attempted and all retained FDs close.

        Notes
        -----
        Never signals a PID selected by a caller and never kills the controller.
        A stop error is preserved; it cannot be reported as successful quiescence.
        """
        _root_only()
        if self.closed:
            return
        failures: list[Exception] = []
        scope: int | None = None
        try:
            scope = os.open(self.name, _directory_flags(), dir_fd=self.parent)
        except OSError as error:
            failures.append(error)
        try:
            for leaf in self.leaves:
                try:
                    stop_protected_writer_subtree(
                        leaf.descriptor, expected_identity=leaf.identity, timeout_seconds=5
                    )
                    if scope is not None:
                        linked = os.stat(leaf.name, dir_fd=scope, follow_symlinks=False)
                        if (linked.st_dev, linked.st_ino) != leaf.identity:
                            raise ValueError("prerequisite leaf identity changed during cleanup")
                        os.rmdir(leaf.name, dir_fd=scope)
                except Exception as error:
                    failures.append(error)
                finally:
                    os.close(leaf.descriptor)
            if not failures:
                try:
                    os.rmdir(self.name, dir_fd=self.parent)
                except OSError as error:
                    failures.append(error)
        finally:
            if scope is not None:
                os.close(scope)
            os.close(self.parent)
            self.closed = True
        if failures:
            details = "; ".join(f"{type(error).__name__}: {error}" for error in failures)
            raise RuntimeError("prerequisite process cleanup incomplete: " + details) from failures[
                0
            ]


def _create_leaf(scope: int, role: str) -> ResourceLeaf:
    """Create one empty domain and verify every requested finite resource limit."""
    os.mkdir(role, 0o755, dir_fd=scope)
    descriptor = os.open(role, _directory_flags(), dir_fd=scope)
    try:
        limits = (
            ("memory.max", str(256 * 1024 * 1024)),
            ("memory.swap.max", "0"),
            ("pids.max", "32"),
            ("cpu.max", "50000 100000"),
        )
        for control, value in limits:
            _write(descriptor, control, value)
            if _read(descriptor, control) != value:
                raise ValueError("prerequisite finite resource readback differs")
        verify_protected_cgroup_controls(capture_protected_cgroup_controls(descriptor))
        if _read(descriptor, "cgroup.procs"):
            raise ValueError("new prerequisite resource leaf already contains a process")
        metadata = os.fstat(descriptor)
        return ResourceLeaf(role, descriptor, (metadata.st_dev, metadata.st_ino), limits)
    except BaseException:
        os.close(descriptor)
        os.rmdir(role, dir_fd=scope)
        raise


def create_resource_scope(run_id: str) -> ResourceScope:
    """Create bounded root-owned cgroups without changing an existing group.

    Parameters
    ----------
    run_id:
        Unique reviewed run identifier, consisting of ``run-`` and 16 hex digits.

    Returns
    -------
    ResourceScope
        Four fresh empty retained domain leaves, each limited to 256 MiB RAM,
        zero swap, 32 processes and one-half CPU. Caller must close the scope.

    Raises
    ------
    PermissionError
        Unless all real, effective and saved root UID/GID values are present.
    ValueError
        On invalid scope, unavailable enabled controllers or unsafe kernel controls.
    OSError
        On a missing cgroup v2 feature or refused kernel write; there is no fallback.
    """
    _root_only()
    if re.fullmatch("run-[0-9a-f]{16}", run_id) is None:
        raise ValueError("invalid prerequisite resource run identifier")
    root = os.open(_ROOT, _directory_flags())
    name = "synapse-qualification-" + run_id
    scope: int | None = None
    created = False
    leaves: list[ResourceLeaf] = []
    try:
        enabled = set(_read(root, "cgroup.subtree_control").split())
        if not {"cpu", "memory", "pids"} <= enabled:
            raise ValueError("prerequisite resource controllers are not already enabled")
        os.mkdir(name, 0o755, dir_fd=root)
        created = True
        scope = os.open(name, _directory_flags(), dir_fd=root)
        _write(scope, "cgroup.subtree_control", "+cpu +memory +pids")
        if not {"cpu", "memory", "pids"} <= set(_read(scope, "cgroup.subtree_control").split()):
            raise ValueError("fresh prerequisite controller delegation differs")
        for role in _ROLES:
            leaves.append(_create_leaf(scope, role))
        return ResourceScope(root, name, tuple(leaves))
    except BaseException:
        if scope is not None:
            for leaf in leaves:
                os.close(leaf.descriptor)
                os.rmdir(leaf.name, dir_fd=scope)
        if created:
            os.rmdir(name, dir_fd=root)
        os.close(root)
        raise
    finally:
        if scope is not None:
            os.close(scope)


def enter_resource_leaf(path: Path) -> None:
    """Move only this root child into an exact fresh prerequisite cgroup.

    Parameters
    ----------
    path:
        Independently retained controller path for the known role wrapper.

    Raises
    ------
    PermissionError
        Unless called before the disposable child's credential drop.
    ValueError
        On foreign ancestry, unsafe controls, previous membership or failed migration.
    OSError
        On unavailable kernel control files or a refused membership write.
    """
    _root_only()
    if (
        path.parent.parent != _ROOT
        or re.fullmatch("synapse-qualification-run-[0-9a-f]{16}", path.parent.name) is None
        or path.name not in _ROLES
    ):
        raise ValueError("prerequisite child resource path is outside its exact scope")
    descriptor = os.open(path, _directory_flags())
    try:
        verify_protected_cgroup_controls(capture_protected_cgroup_controls(descriptor))
        if _read(descriptor, "cgroup.procs"):
            raise ValueError("prerequisite leaf must be empty before its one root wrapper")
        _write(descriptor, "cgroup.procs", str(os.getpid()))
        if _read(descriptor, "cgroup.procs") != str(os.getpid()):
            raise ValueError("prerequisite root child membership readback differs")
    finally:
        os.close(descriptor)
