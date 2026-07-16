# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — executable, cwd, and environment policy for outbound MCP
"""Validate the exact process launch derived from outbound MCP policy.

Every server command is an absolute regular file opened component by component
without following symlinks, then copied into a sealed descriptor snapshot.
Runtime launches that immutable snapshot rather than reopening the pathname.
Working directories remain descriptor-bound, and child environments contain
only literal policy values plus individually approved parent names.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import stat
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from synapse_channel.core.mcp_config import McpConfigError, McpServerSpec
from synapse_channel.core.secret_files import open_nofollow_descriptor

MCP_SDK_POSIX_DEFAULT_ENV = ("HOME", "LOGNAME", "PATH", "SHELL", "TERM", "USER")
"""Parent names MCP Python injects before the caller-supplied environment.

The SDK merges these values first and the explicit mapping second. Supplying an
empty value for every unapproved name therefore removes parent data without
forking the SDK's stdio framing and process-tree cleanup.
"""

MCP_EXECUTABLE_SNAPSHOT_LIMIT = 256 * 1024 * 1024
"""Maximum executable size copied into one sealed descriptor snapshot."""


@dataclass(frozen=True)
class McpExecutableEvidence:
    """Observed identity of one executable admitted for MCP launch.

    Parameters
    ----------
    server : str
        Configured MCP server name.
    path : str
        Absolute, no-follow executable path.
    sha256 : str
        Digest computed from the same descriptor that was validated.
    hash_pinned : bool
        Whether policy required that exact digest.
    """

    server: str
    path: str
    sha256: str
    hash_pinned: bool


@dataclass(frozen=True)
class BoundMcpLaunch:
    """Descriptor-bound executable and cwd paths retained through subprocess spawn."""

    evidence: McpExecutableEvidence
    command: str
    cwd: str


def validate_mcp_server_launch(spec: McpServerSpec) -> McpExecutableEvidence:
    """Prove one configured executable and working directory immediately pre-launch.

    The executable is opened component by component with ``O_NOFOLLOW``, copied
    into a sealed descriptor snapshot, and hashed. A configured digest must
    match the exact snapshot bytes. Runtime retains the same snapshot through
    process creation so a later pathname change cannot authorise different code.
    """
    with bind_mcp_server_launch(spec) as launch:
        return launch.evidence


@contextmanager
def bind_mcp_server_launch(spec: McpServerSpec) -> Iterator[BoundMcpLaunch]:
    """Yield immutable executable bytes and an exact cwd descriptor for spawning.

    Every source path component is opened with ``O_NOFOLLOW``. Executable bytes
    are copied from that descriptor into a sealed Linux ``memfd`` and the SDK is
    given the parent process's procfd path, so replacement or in-place mutation
    of the configured pathname cannot change what executes. A configured cwd is
    likewise held open and passed through its procfd path until the session ends.
    """
    if (
        os.name != "posix"
        or not hasattr(os, "O_NOFOLLOW")
        or not hasattr(os, "geteuid")
        or not hasattr(os, "memfd_create")
        or not Path("/proc/self/fd").is_dir()
    ):
        raise McpConfigError(
            f"MCP server {spec.name!r}: secure executable validation is unavailable "
            "on this platform"
        )
    command = Path(spec.command)
    if not command.is_absolute():
        raise McpConfigError(
            f"MCP server {spec.name!r}: command must be an absolute executable path"
        )
    try:
        descriptor = open_nofollow_descriptor(command)
    except OSError as exc:
        raise McpConfigError(
            f"MCP server {spec.name!r}: cannot securely open executable {command}: "
            f"{exc.strerror or exc}"
        ) from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise McpConfigError(
                f"MCP server {spec.name!r}: command {command} is not a regular executable"
            )
        if not _executable_by_current_user(info):
            raise McpConfigError(
                f"MCP server {spec.name!r}: command {command} is not executable by the "
                "effective user"
            )
        snapshot, digest = _sealed_executable_snapshot(descriptor, info, server=spec.name)
    except OSError as exc:
        raise McpConfigError(
            f"MCP server {spec.name!r}: cannot inspect executable {command}: {exc.strerror or exc}"
        ) from exc
    finally:
        os.close(descriptor)
    cwd_descriptor: int | None = None
    try:
        if spec.command_sha256 and not hmac.compare_digest(digest, spec.command_sha256):
            raise McpConfigError(
                f"MCP server {spec.name!r}: executable SHA-256 does not match command_sha256"
            )
        cwd_descriptor = _open_working_directory(
            spec.name,
            Path(spec.cwd) if spec.cwd else Path("/"),
        )
        evidence = McpExecutableEvidence(
            server=spec.name,
            path=str(command),
            sha256=digest,
            hash_pinned=bool(spec.command_sha256),
        )
        yield BoundMcpLaunch(
            evidence=evidence,
            command=_procfd_path(snapshot),
            cwd=_procfd_path(cwd_descriptor),
        )
    finally:
        os.close(snapshot)
        if cwd_descriptor is not None:
            os.close(cwd_descriptor)


def child_environment(
    spec: McpServerSpec, *, parent: Mapping[str, str] | None = None
) -> dict[str, str]:
    """Build the exact child environment from approved names and literal values.

    No parent value is inherited unless its name appears in ``spec.inherit_env``.
    The MCP SDK's POSIX default names are explicitly blanked first because the
    SDK otherwise injects them even when given an empty mapping. Literal config
    values override both the blank and an approved inherited value.
    """
    source = os.environ if parent is None else parent
    environment = {name: "" for name in MCP_SDK_POSIX_DEFAULT_ENV}
    environment.update({name: source[name] for name in spec.inherit_env if name in source})
    environment.update(spec.env)
    return environment


def _executable_by_current_user(info: os.stat_result) -> bool:
    """Return whether descriptor metadata grants execute access to this process."""
    mode = info.st_mode
    effective_uid = os.geteuid()
    if effective_uid == 0:
        return bool(mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))
    if info.st_uid == effective_uid:
        return bool(mode & stat.S_IXUSR)
    groups = {os.getegid(), *os.getgroups()}
    if info.st_gid in groups:
        return bool(mode & stat.S_IXGRP)
    return bool(mode & stat.S_IXOTH)


def _sealed_executable_snapshot(
    descriptor: int, info: os.stat_result, *, server: str
) -> tuple[int, str]:
    """Copy executable bytes into a sealed memfd and return its digest."""
    if info.st_size > MCP_EXECUTABLE_SNAPSHOT_LIMIT:
        raise McpConfigError(
            f"MCP server {server!r}: executable exceeds the "
            f"{MCP_EXECUTABLE_SNAPSHOT_LIMIT}-byte snapshot limit"
        )
    try:
        import fcntl
    except ImportError as exc:  # pragma: no cover - guarded Linux boundary
        raise McpConfigError(
            f"MCP server {server!r}: sealed executable snapshots are unavailable"
        ) from exc
    snapshot = os.memfd_create(
        "synapse-mcp-executable",
        getattr(os, "MFD_CLOEXEC", 0) | getattr(os, "MFD_ALLOW_SEALING", 0),
    )
    digest = hashlib.sha256()
    try:
        total = 0
        while chunk := os.read(descriptor, 1024 * 1024):
            total += len(chunk)
            if total > MCP_EXECUTABLE_SNAPSHOT_LIMIT:
                raise McpConfigError(
                    f"MCP server {server!r}: executable grew beyond the "
                    f"{MCP_EXECUTABLE_SNAPSHOT_LIMIT}-byte snapshot limit"
                )
            digest.update(chunk)
            _write_all(snapshot, chunk)
        after = os.fstat(descriptor)
        before_identity = (
            info.st_dev,
            info.st_ino,
            info.st_size,
            info.st_mtime_ns,
            info.st_ctime_ns,
        )
        after_identity = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        if before_identity != after_identity:
            raise McpConfigError(f"MCP server {server!r}: executable changed while snapshotting")
        os.fchmod(snapshot, 0o500)
        os.lseek(snapshot, 0, os.SEEK_SET)
        seals = fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL
        fcntl.fcntl(snapshot, fcntl.F_ADD_SEALS, seals)
    except BaseException:
        os.close(snapshot)
        raise
    return snapshot, digest.hexdigest()


def _write_all(descriptor: int, payload: bytes) -> None:
    """Write every byte to one descriptor, handling short writes."""
    view = memoryview(payload)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:  # pragma: no cover - defensive OS boundary
            raise OSError("short write while sealing MCP executable")
        view = view[written:]


def _open_working_directory(server: str, directory: Path) -> int:
    """Open and retain one absolute component-wise no-follow directory."""
    if not directory.is_absolute():
        raise McpConfigError(
            f"MCP server {server!r}: cwd must be an absolute directory path when configured"
        )
    try:
        descriptor = open_nofollow_descriptor(directory, directory=True)
    except OSError as exc:
        raise McpConfigError(
            f"MCP server {server!r}: cannot securely open cwd {directory}: {exc.strerror or exc}"
        ) from exc
    metadata = os.fstat(descriptor)
    if not stat.S_ISDIR(metadata.st_mode):
        os.close(descriptor)
        raise McpConfigError(f"MCP server {server!r}: cwd {directory} is not a directory")
    if metadata.st_mode & 0o022:
        os.close(descriptor)
        raise McpConfigError(
            f"MCP server {server!r}: cwd {directory} must not be group/world-writable"
        )
    return descriptor


def _procfd_path(descriptor: int) -> str:
    """Return a procfd path proven to resolve to ``descriptor``'s exact object."""
    path = f"/proc/{os.getpid()}/fd/{descriptor}"
    try:
        expected = os.fstat(descriptor)
        observed = os.stat(path)
    except OSError as exc:
        raise McpConfigError(f"cannot bind MCP launch descriptor through {path}: {exc}") from exc
    if (expected.st_dev, expected.st_ino) != (observed.st_dev, observed.st_ino):
        raise McpConfigError(f"MCP launch descriptor binding mismatch at {path}")
    return path
