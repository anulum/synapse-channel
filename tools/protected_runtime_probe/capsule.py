# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — bounded immutable prerequisite capsule codec
"""Capture approved capsule bytes before privileged filesystem or process actions."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any
from uuid import uuid4

SCHEMA = "synapse-protected-runtime-prerequisites.v3"
NATIVE_ABI = "linux-x86_64-64"
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 256 * 1024 * 1024
MAX_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_FILES = 10_000


def _closed_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Refuse duplicate manifest keys before any interpretation."""
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate prerequisite capsule key")
        result[key] = value
    return result


def _require_relative_name(path: str) -> None:
    """Require a bounded canonical relative name before opening any descriptor."""
    if (
        type(path) is not str
        or not path
        or path.startswith("/")
        or len(path.encode("utf-8")) > 4096
        or any(part in {"", ".", ".."} for part in path.split("/"))
        or chr(92) in path
        or any(ord(character) < 32 or ord(character) == 127 for character in path)
    ):
        raise ValueError("unsafe prerequisite capsule relative path")


def _require_payload_name(path: str) -> None:
    """Confine payload entries to application, probe, runtime and licence roots."""
    _require_relative_name(path)
    if not path.startswith(("app/", "probe/", "runtime/", "licenses/")):
        raise ValueError("unsafe prerequisite capsule payload scope")


def _require_backing_parent(path: str) -> None:
    """Require the approved Samsung storage scope without creating or resolving it."""
    if (
        type(path) is not str
        or not path.startswith("/media/anulum/GOTM/")
        or str(PurePosixPath(path)) != path
        or ".." in PurePosixPath(path).parts
        or chr(92) in path
        or len(path.encode("utf-8")) > 4096
        or any(ord(character) < 32 or ord(character) == 127 for character in path)
    ):
        raise ValueError("prerequisite backing must remain in the approved Samsung scope")


@dataclass(frozen=True)
class FilePin:
    """One approved regular payload entry, with exact raw-byte length and SHA256."""

    path: str
    sha256: str
    size_bytes: int

    def __post_init__(self) -> None:
        """Refuse traversal, malformed digests and unbounded lengths."""
        _require_payload_name(self.path)
        if type(self.sha256) is not str or re.fullmatch("[0-9a-f]{64}", self.sha256) is None:
            raise ValueError("invalid prerequisite payload digest")
        if type(self.size_bytes) is not int or not 0 <= self.size_bytes <= MAX_FILE_BYTES:
            raise ValueError("invalid prerequisite payload size")


@dataclass(frozen=True)
class CapsuleManifest:
    """Closed immutable byte inventory, not root authorization or runtime acceptance."""

    source_head: str
    backing_parent: str
    files: tuple[FilePin, ...]
    guest_uuid: str = field(default_factory=lambda: str(uuid4()))

    def __post_init__(self) -> None:
        """Require a unique canonical complete inventory within total byte bounds."""
        if (
            type(self.source_head) is not str
            or re.fullmatch("(?:[0-9a-f]{40}|[0-9a-f]{64})", self.source_head) is None
        ):
            raise ValueError("invalid prerequisite source head")
        _require_backing_parent(self.backing_parent)
        if (
            type(self.guest_uuid) is not str
            or re.fullmatch(
                "[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
                self.guest_uuid,
            )
            is None
        ):
            raise ValueError("prerequisite guest requires a canonical per-attempt UUID4")
        if type(self.files) is not tuple or not 0 < len(self.files) <= MAX_FILES:
            raise ValueError("invalid prerequisite payload count")
        if any(type(pin) is not FilePin for pin in self.files):
            raise ValueError("prerequisite inventory requires immutable file pins")
        names = tuple(pin.path for pin in self.files)
        if names != tuple(sorted(set(names))):
            raise ValueError("prerequisite payload paths must be unique and sorted")
        if sum(pin.size_bytes for pin in self.files) > MAX_TOTAL_BYTES:
            raise ValueError("prerequisite capsule exceeds total byte budget")

    def canonical_bytes(self) -> bytes:
        """Encode the exact versioned native-profile manifest deterministically."""
        return json.dumps(
            {
                "schema": SCHEMA,
                "native_abi": NATIVE_ABI,
                "source_head": self.source_head,
                "backing_parent": self.backing_parent,
                "guest_uuid": self.guest_uuid,
                "files": [
                    {"path": pin.path, "sha256": pin.sha256, "size_bytes": pin.size_bytes}
                    for pin in self.files
                ],
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")


@dataclass(frozen=True)
class CapturedCapsule:
    """Exact approved bytes detached from a mutable caller directory.

    The content mapping is copied and read-only. Neither a caller-constructed
    record nor any stored path grants privileges or permission to execute.
    """

    manifest: CapsuleManifest
    manifest_sha256: str
    content: Mapping[str, bytes] = field(repr=False)

    def __post_init__(self) -> None:
        """Detach and check the entire byte inventory without granting root approval."""
        content = dict(self.content)
        pins = {pin.path: pin for pin in self.manifest.files}
        if hashlib.sha256(self.manifest.canonical_bytes()).hexdigest() != self.manifest_sha256:
            raise ValueError("captured prerequisite manifest digest differs")
        if content.keys() != pins.keys():
            raise ValueError("captured prerequisite payload scope differs")
        for name, data in content.items():
            pin = pins[name]
            if (
                type(data) is not bytes
                or len(data) != pin.size_bytes
                or hashlib.sha256(data).hexdigest() != pin.sha256
            ):
                raise ValueError("captured prerequisite payload bytes differ")
        object.__setattr__(self, "content", MappingProxyType(content))


def decode_manifest(raw: bytes) -> CapsuleManifest:
    """Decode a closed bounded manifest without reading payloads or touching state.

    Parameters
    ----------
    raw:
        Complete canonical manifest bytes, bounded to 2 MiB.

    Returns
    -------
    CapsuleManifest
        Exact native-profile inventory with unique sorted payload paths.

    Raises
    ------
    ValueError
        On malformed, noncanonical, duplicate, unbounded or unsupported input.
    """
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_MANIFEST_BYTES:
        raise ValueError("invalid prerequisite manifest byte budget")
    record = json.loads(raw, object_pairs_hook=_closed_object)
    if type(record) is not dict or record.keys() != {
        "schema",
        "native_abi",
        "source_head",
        "backing_parent",
        "guest_uuid",
        "files",
    }:
        raise ValueError("invalid prerequisite manifest shape")
    if record["schema"] != SCHEMA or record["native_abi"] != NATIVE_ABI:
        raise ValueError("unsupported prerequisite manifest profile")
    if type(record["files"]) is not list or not 0 < len(record["files"]) <= MAX_FILES:
        raise ValueError("invalid prerequisite manifest file count")
    pins = []
    for row in record["files"]:
        if type(row) is not dict or row.keys() != {"path", "sha256", "size_bytes"}:
            raise ValueError("invalid prerequisite file pin")
        pins.append(FilePin(row["path"], row["sha256"], row["size_bytes"]))
    manifest = CapsuleManifest(
        record["source_head"], record["backing_parent"], tuple(pins), record["guest_uuid"]
    )
    if manifest.canonical_bytes() != raw:
        raise ValueError("prerequisite manifest must retain exact canonical bytes")
    return manifest


def require_posix_descriptors(observed_os: str) -> None:
    """Require the observed descriptor backend before accessing a native object.

    Parameters
    ----------
    observed_os:
        Backend name supplied by the host adapter from os.name.

    Raises
    ------
    OSError
        Unless the observed backend provides POSIX descriptor semantics.

    Notes
    -----
    A passing domain check supplies no file, identity or capture authority.
    Each caller must still retain and independently validate actual descriptors.
    """
    if observed_os != "posix":
        raise OSError("prerequisite descriptor capture requires native POSIX descriptors")


def read_relative_file(
    directory: int, name: str, max_bytes: int, *, require_single_link: bool = False
) -> bytes:
    """Read a bounded regular file through no-follow descriptor ancestry.

    Parameters
    ----------
    directory:
        Borrowed capsule root directory descriptor.
    name:
        Canonical relative name, validated before any ancestor is opened.
    max_bytes:
        Nonnegative exact read bound, at most 64 MiB.
    require_single_link:
        Require one link for staged capsule/runtime files. Ordinary package
        sources may retain stable cache hardlinks before their bytes are copied.

    Returns
    -------
    bytes
        Captured complete bytes, detached from later source mutations.

    Raises
    ------
    ValueError
        On invalid input, nonregular objects, changes during capture or overflow.
    OSError
        On symlinks, missing entries or unavailable descriptor operations.
    """
    _require_relative_name(name)
    require_posix_descriptors(os.name)
    if (
        type(directory) is not int
        or directory < 0
        or type(max_bytes) is not int
        or not 0 <= max_bytes <= MAX_FILE_BYTES
        or type(require_single_link) is not bool
    ):
        raise ValueError("invalid prerequisite file capture enrollment")
    current = os.dup(directory)
    try:
        parts = name.split("/")
        for part in parts[:-1]:
            child = os.open(
                part,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=current,
            )
            os.close(current)
            current = child
        leaf = os.open(
            parts[-1],
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
            dir_fd=current,
        )
        try:
            return _read_stable_regular(leaf, max_bytes, require_single_link=require_single_link)
        finally:
            os.close(leaf)
    finally:
        os.close(current)


def _read_stable_regular(descriptor: int, maximum: int, *, require_single_link: bool) -> bytes:
    """Capture only stable regular bytes, excluding read-induced atime changes."""
    before = os.fstat(descriptor)
    if not stat.S_ISREG(before.st_mode) or before.st_size > maximum:
        raise ValueError("prerequisite source is not a bounded regular file")
    if require_single_link and before.st_nlink != 1:
        raise ValueError("prerequisite staged file requires exactly one link")
    raw = bytearray()
    while len(raw) <= maximum:
        chunk = os.read(descriptor, min(262144, maximum + 1 - len(raw)))
        if not chunk:
            break
        raw.extend(chunk)
    after = os.fstat(descriptor)
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
    if len(raw) > maximum or any(getattr(before, key) != getattr(after, key) for key in fields):
        raise ValueError("prerequisite source changed or exceeded its byte budget")
    return bytes(raw)


def capture_capsule(directory: Path, expected_sha256: str) -> CapturedCapsule:
    """Capture all manifest-approved bytes before privileged use.

    Parameters
    ----------
    directory:
        Untrusted staging directory; no descendant symlink is followed.
    expected_sha256:
        Independently approved raw canonical-manifest SHA256.

    Returns
    -------
    CapturedCapsule
        Complete immutable snapshot after every payload pin matches.

    Raises
    ------
    ValueError
        On invalid digest, malformed inventory or any content/length mismatch.
    OSError
        On inaccessible, nonregular, linked or missing source entries.

    Notes
    -----
    Root execution must use these captured bytes or fresh verified root-owned
    copies. Reopening caller paths after hashing would not retain this proof.
    This routine installs no account, mount, file, process or authority grant.
    """
    if type(expected_sha256) is not str or re.fullmatch("[0-9a-f]{64}", expected_sha256) is None:
        raise ValueError("invalid independently approved manifest digest")
    require_posix_descriptors(os.name)
    root = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        raw = read_relative_file(
            root, "manifest.json", MAX_MANIFEST_BYTES, require_single_link=True
        )
        if hashlib.sha256(raw).hexdigest() != expected_sha256:
            raise ValueError("prerequisite manifest differs from independent approval")
        manifest = decode_manifest(raw)
        content = {}
        for pin in manifest.files:
            data = read_relative_file(root, pin.path, pin.size_bytes, require_single_link=True)
            if len(data) != pin.size_bytes or hashlib.sha256(data).hexdigest() != pin.sha256:
                raise ValueError("prerequisite payload differs from approved bytes")
            content[pin.path] = data
        return CapturedCapsule(manifest, expected_sha256, content)
    finally:
        os.close(root)
