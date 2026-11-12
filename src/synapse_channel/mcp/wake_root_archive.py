# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — verified raw generation archives
"""Publish immutable raw wake-generation archives before removing redundant files."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from synapse_channel.core.secure_path import (
    apply_owner_only_file,
    assert_owner_only_dir_path,
    assert_owner_only_file_path,
    read_owner_only_file_bytes,
)
from synapse_channel.durable_dir import private_dir, sync_directory
from synapse_channel.mcp.wake_limits import MAX_WAKE_DATABASE_BYTES, MAX_WAKE_JOURNAL_BYTES
from synapse_channel.mcp.wake_pointer import WakeLedgerError


@dataclass(frozen=True)
class ArchiveFile:
    """Bind one regular native main/journal filename to exact raw bytes."""

    name: str
    bytes: int
    sha256: str

    def __post_init__(self) -> None:
        """Refuse path selectors, unbounded files and malformed content fingerprints."""
        if (
            not isinstance(self.name, str)
            or re.fullmatch(r"[0-9a-f]{64}\.sqlite3(?:-journal)?", self.name) is None
            or type(self.bytes) is not int
            or not 0
            <= self.bytes
            <= (
                MAX_WAKE_JOURNAL_BYTES
                if self.name.endswith("-journal")
                else MAX_WAKE_DATABASE_BYTES
            )
            or not isinstance(self.sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", self.sha256) is None
        ):
            raise WakeLedgerError("invalid wake archive file")

    def encode(self) -> dict[str, object]:
        """Return bounded raw-file metadata without reading or decoding a delivery."""
        return {"name": self.name, "bytes": self.bytes, "sha256": self.sha256}


@dataclass(frozen=True)
class ArchiveSnapshot:
    """Fence a complete main/journal snapshot without native message contents."""

    key: str
    files: tuple[ArchiveFile, ...]

    def __post_init__(self) -> None:
        """Require a main file followed only by its optional matching rollback journal."""
        if (
            not isinstance(self.key, str)
            or re.fullmatch(r"[0-9a-f]{64}", self.key) is None
            or type(self.files) is not tuple
            or not 1 <= len(self.files) <= 2
            or not all(isinstance(item, ArchiveFile) for item in self.files)
        ):
            raise WakeLedgerError("invalid wake archive snapshot")
        expected = (self.key + ".sqlite3", self.key + ".sqlite3-journal")
        if tuple(item.name for item in self.files) != expected[: len(self.files)]:
            raise WakeLedgerError("wake archive file binding changed")

    def encode(self) -> str:
        """Render immutable canonical metadata used both for the intent and archive manifest."""
        return json.dumps(
            {"version": 1, "scope_key": self.key, "files": [item.encode() for item in self.files]},
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )

    @property
    def fingerprint(self) -> str:
        """Return the operator's exact snapshot compare-and-set fence."""
        return hashlib.sha256(self.encode().encode("ascii")).hexdigest()

    @classmethod
    def from_json(cls, raw: str) -> ArchiveSnapshot:
        """Validate bounded stored intent metadata before using any filesystem names."""
        if not isinstance(raw, str) or len(raw) > 8192:
            raise WakeLedgerError("invalid wake archive manifest")
        try:
            value = json.loads(raw)
        except ValueError:
            raise WakeLedgerError("invalid wake archive manifest") from None
        if (
            not isinstance(value, dict)
            or set(value) != {"version", "scope_key", "files"}
            or type(value["version"]) is not int
            or value["version"] != 1
            or not isinstance(value["files"], list)
            or not 1 <= len(value["files"]) <= 2
        ):
            raise WakeLedgerError("invalid wake archive manifest")
        files = []
        for item in value["files"]:
            if not isinstance(item, dict) or set(item) != {"name", "bytes", "sha256"}:
                raise WakeLedgerError("invalid wake archive metadata")
            files.append(ArchiveFile(item["name"], item["bytes"], item["sha256"]))
        return cls(value["scope_key"], tuple(files))


def _raw_file(path: Path, size: int | None = None) -> bytes:
    """Read one exact owner-only single-link file through the existing secure reader."""
    assert_owner_only_file_path(path, purpose="MCP wake archive input", require_single_link=True)
    cap = MAX_WAKE_JOURNAL_BYTES if path.name.endswith("-journal") else MAX_WAKE_DATABASE_BYTES
    return read_owner_only_file_bytes(
        path,
        purpose="MCP wake archive input",
        expected_size=size,
        max_bytes=cap,
    )


def snapshot_wake_generation(root: Path, key: str) -> ArchiveSnapshot:
    """Hash bounded main/journal bytes while the caller holds the wake root lock."""
    if not isinstance(key, str) or re.fullmatch(r"[0-9a-f]{64}", key) is None:
        raise WakeLedgerError("invalid wake generation key")
    names = [key + ".sqlite3"]
    if (root / (key + ".sqlite3-journal")).exists():
        names.append(key + ".sqlite3-journal")
    files = []
    for name in names:
        raw = _raw_file(root / name)
        files.append(ArchiveFile(name, len(raw), hashlib.sha256(raw).hexdigest()))
    return ArchiveSnapshot(key, tuple(files))


def prepare_archive_directory(path: Path) -> None:
    """Create an explicit private destination without tightening unrelated existing folders."""
    if path.exists() or path.is_symlink():
        assert_owner_only_dir_path(path, purpose="MCP wake archive directory")
    else:
        private_dir(path, purpose="MCP wake archive directory")


def archive_destination(root: Path, parent: Path, key: str, fingerprint: str) -> Path:
    """Select an explicit private archive outside the bounded active-state root."""
    if (
        not isinstance(key, str)
        or re.fullmatch(r"[0-9a-f]{64}", key) is None
        or not isinstance(fingerprint, str)
        or re.fullmatch(r"[0-9a-f]{64}", fingerprint) is None
    ):
        raise WakeLedgerError("invalid wake archive identity")
    root = root.resolve()
    parent = parent.absolute()
    if parent.resolve().is_relative_to(root):
        raise WakeLedgerError("wake archive must be outside its state root")
    destination = parent / (key + "-" + fingerprint)
    if len(str(destination)) > 4096:
        raise WakeLedgerError("wake archive path exceeds its budget")
    return destination


def archive_location_digest(path: Path) -> str:
    """Bind a compact retired fence to its exact canonical operator-selected location."""
    return hashlib.sha256(os.path.normcase(str(path.resolve())).encode()).hexdigest()


def _verify_bytes(path: Path, size: int, digest: str) -> None:
    """Refuse an existing final file with changed contents instead of overwriting it."""
    raw = _raw_file(path, size)
    if hashlib.sha256(raw).hexdigest() != digest:
        raise WakeLedgerError("wake archive bytes changed")


def _publish(path: Path, raw: bytes) -> None:
    """Publish an immutable final name with synced partial bytes and no overwrite."""
    digest = hashlib.sha256(raw).hexdigest()
    if path.exists():
        _verify_bytes(path, len(raw), digest)
        return
    partial = path.with_name("." + path.name + ".partial")
    if partial.exists():
        assert_owner_only_file_path(
            partial, purpose="MCP wake archive partial", require_single_link=True
        )
        if partial.stat().st_size > len(raw):
            raise WakeLedgerError("unexpected wake archive partial")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(partial, flags, 0o600), "wb") as stream:
        apply_owner_only_file(partial)
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        os.link(partial, path)
    except FileExistsError:
        _verify_bytes(path, len(raw), digest)
    partial.unlink()
    sync_directory(path.parent)


def _repair_publications(directory: Path, snapshot: ArchiveSnapshot) -> None:
    """Finish only a verified two-link publication left by a crashed process."""
    encoded = snapshot.encode().encode("ascii")
    records = [(item.name, item.bytes, item.sha256) for item in snapshot.files]
    records.append(("manifest.json", len(encoded), hashlib.sha256(encoded).hexdigest()))
    for name, size, digest in records:
        final = directory / name
        partial = directory / ("." + name + ".partial")
        if not final.exists() or not partial.exists():
            continue
        for path in (final, partial):
            assert_owner_only_file_path(path, purpose="MCP wake interrupted publication")
        if final.stat().st_nlink != 2 or not final.samefile(partial):
            continue
        cap = MAX_WAKE_JOURNAL_BYTES if name.endswith("-journal") else MAX_WAKE_DATABASE_BYTES
        raw = read_owner_only_file_bytes(
            final,
            purpose="MCP wake interrupted publication",
            expected_size=size,
            max_bytes=cap,
        )
        if hashlib.sha256(raw).hexdigest() != digest:
            raise WakeLedgerError("wake archive publication changed")
        partial.unlink()
        sync_directory(directory)


def _check_destination(directory: Path, snapshot: ArchiveSnapshot) -> None:
    """Permit only the exact manifest, files and their resumable partial names."""
    allowed = {"manifest.json", *(item.name for item in snapshot.files)}
    allowed |= {"." + name + ".partial" for name in tuple(allowed)}
    with os.scandir(directory) as entries:
        for index, entry in enumerate(entries):
            if index >= len(allowed) or entry.name not in allowed:
                raise WakeLedgerError("unexpected file in wake archive")
            assert_owner_only_file_path(
                directory / entry.name,
                purpose="MCP wake archive",
                require_single_link=True,
            )


def verify_wake_archive(directory: Path, snapshot: ArchiveSnapshot) -> None:
    """Confirm all raw files and the durable metadata before active-root removal."""
    _check_destination(directory, snapshot)
    encoded = snapshot.encode().encode("ascii")
    _verify_bytes(directory / "manifest.json", len(encoded), hashlib.sha256(encoded).hexdigest())
    for item in snapshot.files:
        _verify_bytes(directory / item.name, item.bytes, item.sha256)


def archive_wake_files(root: Path, directory: Path, snapshot: ArchiveSnapshot) -> None:
    """Copy, sync and verify complete raw evidence before removing redundant source files.

    Notes
    -----
    Root admission must already hold a committed matching archive intent. The
    caller holds the shared OS lock throughout. Existing final bytes are never
    overwritten; a partial removal resumes using the same verified manifest.
    """
    prepare_archive_directory(directory)
    _repair_publications(directory, snapshot)
    _check_destination(directory, snapshot)
    for item in snapshot.files:
        destination = directory / item.name
        if destination.exists():
            _verify_bytes(destination, item.bytes, item.sha256)
        else:
            raw = _raw_file(root / item.name, item.bytes)
            if hashlib.sha256(raw).hexdigest() != item.sha256:
                raise WakeLedgerError("wake archive source changed")
            _publish(destination, raw)
    _publish(directory / "manifest.json", snapshot.encode().encode("ascii"))
    verify_wake_archive(directory, snapshot)
    sync_directory(directory)
    sync_directory(directory.parent)
    expected = {item.name for item in snapshot.files}
    journal = snapshot.key + ".sqlite3-journal"
    if journal not in expected and (root / journal).exists():
        raise WakeLedgerError("wake archive source changed")
    for item in snapshot.files:
        source = root / item.name
        if source.exists():
            _verify_bytes(source, item.bytes, item.sha256)
            source.unlink()
    sync_directory(root)
