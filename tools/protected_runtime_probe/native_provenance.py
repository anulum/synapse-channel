# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — installed package and pinned wheel native provenance
"""Verify detached native bytes against installed packages and a pinned wheel."""

from __future__ import annotations

import asyncio
import base64
import csv
import hashlib
import io
import json
import os
import re
import stat
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .capsule import read_relative_file, require_posix_descriptors
from .roles import ROOT_COMMAND_ENV


@dataclass(frozen=True)
class NativeWheelArtifact:
    """Detached wheel bytes whose complete artifact hash matches the profile lock."""

    filename: str
    sha256: str
    content: bytes


@dataclass(frozen=True)
class NativePackageTree:
    """Explicit package source context; the default is the actual system tree.

    ``root`` contains both source files and var/lib/dpkg. ``owner`` identifies
    the expected file UID/GID in that context. An alternate tree is ordinary
    input data, never proof that its packages are installed on the real host.
    The production packet preparer always uses the default system context.
    """

    root: Path = Path("/").absolute()
    owner: tuple[int, int] = (0, 0)

    def __post_init__(self) -> None:
        """Require an absolute context root before filesystem and dpkg interpretation."""
        if not self.root.is_absolute() or ".." in self.root.parts:
            raise ValueError("native package tree requires an absolute canonical root")


SYSTEM_PACKAGE_TREE = NativePackageTree()


def _read_file(path: Path, maximum: int) -> bytes:
    """Capture a bounded regular file through its retained no-follow parent."""
    require_posix_descriptors(os.name)
    parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        return read_relative_file(parent, path.name, maximum)
    finally:
        os.close(parent)


async def _read_command_output(stream: asyncio.StreamReader, maximum: int) -> bytes:
    """Read through actual EOF or refuse immediately after the bounded prefix."""
    try:
        await stream.readexactly(maximum + 1)
    except asyncio.IncompleteReadError as error:
        return error.partial
    raise ValueError("native metadata command exceeded its output bound")


async def _collect_command_result(
    process: asyncio.subprocess.Process, readers: list[asyncio.Task[bytes]]
) -> tuple[int, bytes]:
    """Collect both bounded streams and wait for the actual child exit."""
    output, _error = await asyncio.gather(*readers)
    return await process.wait(), output


async def collect_native_metadata_process(
    process: asyncio.subprocess.Process, *, timeout: float = 10
) -> tuple[int, bytes]:
    """Collect an owned metadata child, requiring both exclusively retained pipes.

    Parameters
    ----------
    process:
        Actual child owned by this caller. Both stdout and stderr must be pipes.
    timeout:
        Positive finite deadline, at most thirty seconds, covering IO and exit.

    Returns
    -------
    tuple[int, bytes]
        Actual return status and bounded stdout after the child is reaped.

    Raises
    ------
    ValueError, OSError, TimeoutError
        On missing pipes, invalid limits, excessive output or elapsed deadline.

    Notes
    -----
    Taking this call transfers responsibility for stopping/reaping this child;
    it grants no authority over another process or its descriptors.
    """
    if process.stdout is None or process.stderr is None:
        if process.returncode is None:
            process.kill()
        await process.communicate()
        raise ValueError("native metadata command output pipes are unavailable")
    readers = [
        asyncio.create_task(_read_command_output(process.stdout, 2 * 1024 * 1024)),
        asyncio.create_task(_read_command_output(process.stderr, 65536)),
    ]
    try:
        if not 0 < timeout <= 30:
            raise ValueError("native metadata command bounds are invalid")
        return await asyncio.wait_for(_collect_command_result(process, readers), timeout)
    finally:
        if process.returncode is None:
            process.kill()
        for reader in readers:
            reader.cancel()
        await asyncio.gather(*readers, return_exceptions=True)
        await process.communicate()


async def _capture_command(
    executable: str, arguments: tuple[str, ...], timeout: float
) -> tuple[int, bytes]:
    """Launch the fixed command and transfer the owned child to its pipe collector."""
    process = await asyncio.create_subprocess_exec(
        executable,
        *arguments,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=ROOT_COMMAND_ENV,
        cwd="/",
        start_new_session=True,
        limit=65536,
    )
    return await collect_native_metadata_process(process, timeout=timeout)


def run_native_metadata_command(
    executable: str, arguments: tuple[str, ...], *, timeout: float = 10
) -> tuple[int, bytes]:
    """Run one fixed host metadata tool with bounded output and complete child reaping.

    Parameters
    ----------
    executable:
        Absolute system dpkg-query, readelf or Python 3.12 executable.
    arguments:
        Fixed preparation-selected arguments; no shell or caller environment.
    timeout:
        Positive finite observation deadline, at most thirty seconds.

    Returns
    -------
    tuple[int, bytes]
        Actual exit status and stdout, bounded to two MiB; stderr is bounded to 64 KiB.

    Raises
    ------
    ValueError, OSError, TimeoutError
        On an unsupported tool, invalid bounds, excessive output or failed launch.

    Notes
    -----
    This is an ordinary preparation utility, not privileged execution authority.
    Owned children are killed and reaped on refusal, with no broad process signals.
    """
    if executable not in {"/usr/bin/dpkg-query", "/usr/bin/readelf", "/usr/bin/python3.12"}:
        raise ValueError("native metadata executable is outside the fixed tool set")
    if not 0 < timeout <= 30 or len(arguments) > 64 or any(len(arg) > 65536 for arg in arguments):
        raise ValueError("native metadata command bounds are invalid")
    return asyncio.run(_capture_command(executable, arguments, timeout))


def _query(*arguments: str, tree: NativePackageTree, missing: bool = False) -> str:
    """Query the declared real dpkg tree with a fixed executable, environment and deadline."""
    database = tree.root / "var/lib/dpkg"
    returncode, output = run_native_metadata_command(
        "/usr/bin/dpkg-query", ("--admindir=" + str(database), *arguments)
    )
    if missing and returncode == 1 and not output:
        return ""
    if returncode:
        raise ValueError("installed native package metadata is unavailable")
    return output.decode("utf-8")


def _package_owner(path: Path, tree: NativePackageTree) -> tuple[str, str]:
    """Resolve one exact package owner, accommodating verified merged-usr aliases."""
    root = tree.root.resolve(strict=True)
    if not path.is_relative_to(root):
        raise ValueError("native source is outside its declared package tree")
    logical = Path("/") / path.relative_to(root)
    candidates = [logical]
    if logical.is_relative_to("/usr/lib"):
        alias = Path("/") / logical.relative_to("/usr")
        actual_alias = root / alias.relative_to("/")
        if actual_alias.exists() and actual_alias.resolve(strict=True) == path:
            candidates.append(alias)
    for candidate in candidates:
        output = _query("--search", str(candidate), tree=tree, missing=True)
        if not output:
            continue
        fields = output.strip().split(": ")
        if (
            len(fields) != 2
            or fields[1] != str(candidate)
            or re.fullmatch(r"[a-z0-9][a-z0-9+.:_-]*", fields[0]) is None
        ):
            raise ValueError("installed native package ownership is ambiguous")
        return fields[0], str(candidate).lstrip("/")
    raise ValueError("installed native file has no verifiable package owner")


def verify_dpkg_native_file(
    source: Path, captured: bytes, *, tree: NativePackageTree = SYSTEM_PACKAGE_TREE
) -> dict[str, object]:
    """Match captured native bytes to the current installed file and dpkg checksum.

    Parameters
    ----------
    source:
        Observed native file. Its canonical object must match the declared
        context owner (root by default) and not be writable by group or others.
    captured:
        Detached bytes proposed for the qualification packet.
    tree:
        Explicit filesystem/package context; the default verifies the actual host.
        Alternate contexts retain their root and expected owner in the result and
        do not establish installation on the real host.

    Returns
    -------
    dict[str, object]
        Installed source, package/version, verified dpkg MD5 and captured SHA-256.

    Raises
    ------
    ValueError, OSError, TimeoutError
        On changed bytes, unsafe ownership or unverifiable installed metadata.

    Notes
    -----
    The MD5 is the package manager's file checksum, not a cryptographic trust
    claim. Independent review binds the packet's SHA-256 and package provenance.
    """
    source = source.resolve(strict=True)
    metadata = source.stat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != tree.owner[0]
        or metadata.st_gid != tree.owner[1]
        or metadata.st_mode & 0o022
    ):
        raise ValueError("installed native file lacks protected system ownership")
    installed = _read_file(source, 64 * 1024 * 1024)
    if captured != installed:
        raise ValueError("captured native object differs from its installed source")
    package, recorded_path = _package_owner(source, tree)
    fields = (
        _query(
            "--show",
            "--showformat=${binary:Package}\t${Version}\t${db:Status-Status}\n",
            package,
            tree=tree,
        )
        .strip()
        .split("\t")
    )
    if len(fields) != 3 or fields[0] != package or not fields[1] or fields[2] != "installed":
        raise ValueError("native package is not currently installed at a verified version")
    checksums = [
        parts[0]
        for line in _query("--control-show", package, "md5sums", tree=tree).splitlines()
        if len(parts := line.split(maxsplit=1)) == 2 and parts[1] == recorded_path
    ]
    actual_md5 = hashlib.md5(installed, usedforsecurity=False).hexdigest()
    if checksums != [actual_md5]:
        raise ValueError("installed native file differs from its dpkg checksum")
    return {
        "installed_source": str(source),
        "source_root": str(tree.root.resolve(strict=True)),
        "expected_owner_uid": tree.owner[0],
        "expected_owner_gid": tree.owner[1],
        "source_context": "system" if tree == SYSTEM_PACKAGE_TREE else "explicit-tree",
        "package": package,
        "package_record_path": recorded_path,
        "version": fields[1],
        "dpkg_md5": actual_md5,
        "dpkg_md5_verified": True,
        "sha256": hashlib.sha256(captured).hexdigest(),
        "size_bytes": len(captured),
        "verified_at": datetime.now(UTC).isoformat(),
    }


def load_native_wheel(lock: Path, wheel: Path) -> NativeWheelArtifact:
    """Capture the exact local dependency wheel named and hashed by the profile lock.

    Parameters
    ----------
    lock:
        Reviewed native-dependencies.lock.json from the candidate probe package.
    wheel:
        Already downloaded local wheel. This function performs no network access.

    Returns
    -------
    NativeWheelArtifact
        Detached artifact whose name, byte count and SHA-256 match the lock.

    Raises
    ------
    ValueError, OSError
        On a malformed lock, changed artifact or unsupported dependency profile.
    """
    record = json.loads(_read_file(lock, 65536))
    if (
        type(record) is not dict
        or record.get("schema") != "synapse.native-dependency-lock.v1"
        or record.get("native_abi") != "linux-x86_64-cpython312"
        or type(pin := record.get("websockets")) is not dict
        or pin.get("version") != "17.4"
        or pin.get("filename") != wheel.name
        or type(digest := pin.get("sha256")) is not str
        or re.fullmatch("[0-9a-f]{64}", digest) is None
        or type(size := pin.get("size_bytes")) is not int
        or not 0 < size <= 8 * 1024 * 1024
    ):
        raise ValueError("native dependency wheel lock is malformed or unsupported")
    content = _read_file(wheel, 8 * 1024 * 1024)
    if len(content) != size or hashlib.sha256(content).hexdigest() != digest:
        raise ValueError("native dependency wheel differs from its artifact pin")
    return NativeWheelArtifact(wheel.name, digest, content)


def verify_wheel_native_file(
    source: Path, member: str, captured: bytes, wheel: NativeWheelArtifact
) -> dict[str, object]:
    """Verify installed native bytes against a pinned wheel member and its RECORD.

    Parameters
    ----------
    source:
        Installed native module observed in the actual probe import closure.
    member:
        Exact corresponding websockets member inside the wheel.
    captured:
        Detached packet bytes, which must equal installed and wheel bytes.
    wheel:
        Complete artifact already verified against the reviewed profile lock.

    Returns
    -------
    dict[str, object]
        Installed path, wheel artifact hash, verified RECORD hash and payload hash.

    Raises
    ------
    ValueError, OSError, zipfile.BadZipFile
        On changed installed bytes, missing member or a different RECORD binding.
    """
    if not member.startswith("websockets/") or ".." in Path(member).parts:
        raise ValueError("native wheel member is outside the declared dependency")
    if captured != _read_file(source.resolve(strict=True), 64 * 1024 * 1024):
        raise ValueError("captured wheel native differs from its installed source")
    with zipfile.ZipFile(io.BytesIO(wheel.content)) as archive:
        records = list(
            csv.reader(io.StringIO(archive.read("websockets-17.4.dist-info/RECORD").decode()))
        )
        rows = [row for row in records if len(row) == 3 and row[0] == member]
        data = archive.read(member)
    digest = hashlib.sha256(captured).digest()
    record_hash = "sha256=" + base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    if data != captured or rows != [[member, record_hash, str(len(captured))]]:
        raise ValueError("native dependency member differs from its wheel RECORD")
    return {
        "installed_source": str(source.resolve(strict=True)),
        "distribution": "websockets",
        "version": "17.4",
        "wheel_filename": wheel.filename,
        "wheel_sha256": wheel.sha256,
        "wheel_record_hash": record_hash,
        "wheel_record_verified": True,
        "sha256": digest.hex(),
        "size_bytes": len(captured),
    }
