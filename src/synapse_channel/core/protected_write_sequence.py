# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — durable writer request sequence
"""Allocate checksummed durable sequences and refuse incomplete counter records."""

from __future__ import annotations

import fcntl
import hashlib
import os
import re
import stat
import struct
import threading
from types import TracebackType

_MAGIC = b"SPWSEQ2\0"
_MAX = (1 << 53) - 1
_PREFIX_SIZE = len(_MAGIC) + 32
_SIZE = _PREFIX_SIZE + 8 + 32


def _validate_sequence_directory(
    root_identity: tuple[int, int], name: str, owner_uid: int, create: bool
) -> None:
    """Require current ownership, pinned directory and exact private counter basename."""
    if (
        type(owner_uid) is not int
        or owner_uid != os.geteuid()
        or type(root_identity) is not tuple
        or (len(root_identity) != 2)
        or any(type(value) is not int or value < 0 for value in root_identity)
        or (type(name) is not str)
        or (name in {".", ".."})
        or (re.fullmatch("[A-Za-z0-9][A-Za-z0-9._-]{0,127}", name) is None)
        or (type(create) is not bool)
    ):
        raise ValueError("invalid protected writer sequence enrollment")


def _sequence_credential_digest(key_id: str, sender: str) -> bytes:
    """Bind an unambiguous credential pair before any counter file is created."""
    if (
        type(key_id) is not str
        or not key_id
        or type(sender) is not str
        or (not sender)
        or "\0" in key_id
        or "\0" in sender
    ):
        raise ValueError("invalid protected writer sequence enrollment")
    return hashlib.sha256((key_id + "\x00" + sender).encode("utf-8")).digest()


def _sequence_record(prefix: bytes, value: int) -> bytes:
    """Bind the entire credential/counter body to its damage-detection checksum."""
    body = prefix + struct.pack(">Q", value)
    return body + hashlib.sha256(body).digest()


def _checked_sequence_value(record: bytes, prefix: bytes) -> int:
    """Refuse truncated, foreign or torn records without falling back to an older value."""
    if (
        len(record) != _SIZE
        or not record.startswith(prefix)
        or hashlib.sha256(record[:-32]).digest() != record[-32:]
    ):
        raise ValueError("writer sequence record is missing or damaged")
    return int(struct.unpack(">Q", record[_PREFIX_SIZE : _PREFIX_SIZE + 8])[0])


class ProtectedWriterSequence:
    """Retain one credential-bound counter under a descriptor and file lock.

    The operator supplies a private directory descriptor and exact identity.
    ``create=True`` is only for a newly enrolled writer key and incarnation;
    existing incarnations must reopen their original file. A missing, replaced,
    damaged or exhausted file fails closed. This does not prove OS isolation.
    Credential identifiers cannot contain the NUL separator; invalid declarations
    refuse before creating a private file.

    The closed version-two record is an eight-byte magic, a 32-byte credential
    digest, an unsigned big-endian eight-byte counter and a SHA-256 checksum of
    the preceding 48 bytes. Any incomplete or inconsistent record refuses, with
    no older-value fallback or automatic repair. The checksum detects torn or
    damaged writes; it cannot authenticate a maliciously replaced valid record.
    The operator must retain file custody and a filesystem honoring fsync.

    Parameters
    ----------
    root_descriptor : int
        Borrowed descriptor for the existing private counter directory.
    root_identity : tuple of int
        Enrolled device and inode of that directory.
    name : str
        Exact ASCII basename, at most 128 characters; no path traversal.
    owner_uid : int
        Enrolled owner, which must equal the current effective user ID.
    key_id : str
        Nonempty credential identifier without NUL bytes.
    sender : str
        Nonempty writer identity without NUL bytes; bound together with key_id.
    create : bool, optional
        Exclusively initialize a newly enrolled credential incarnation. Existing
        incarnations reopen their retained file with the default False.

    Raises
    ------
    ValueError
        On invalid enrollment, directory/file custody or credential binding.
    OSError
        On descriptor, exclusive creation or durable initialization failure.
        Partial files are preserved; failure does not authorize reinitialization.
    """

    def __init__(
        self,
        root_descriptor: int,
        *,
        root_identity: tuple[int, int],
        name: str,
        owner_uid: int,
        key_id: str,
        sender: str,
        create: bool = False,
    ) -> None:
        _validate_sequence_directory(root_identity, name, owner_uid, create)
        digest = _sequence_credential_digest(key_id, sender)
        prefix = _MAGIC + digest
        directory = os.dup(root_descriptor)
        try:
            root = os.fstat(directory)
            if (
                not stat.S_ISDIR(root.st_mode)
                or (root.st_dev, root.st_ino) != root_identity
                or root.st_uid != owner_uid
                or stat.S_IMODE(root.st_mode) != 0o700
            ):
                raise ValueError("writer sequence directory identity or ownership differs")
            flags = os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
            if create:
                flags |= os.O_CREAT | os.O_EXCL
            descriptor = os.open(name, flags, 0o600, dir_fd=directory)
            try:
                identity = self._file_identity(descriptor, root.st_dev, owner_uid)
                if create:
                    payload = _sequence_record(prefix, 0)
                    if os.pwrite(descriptor, payload, 0) != _SIZE:
                        raise OSError("incomplete writer sequence initialization")
                    os.fsync(descriptor)
                    os.fsync(directory)
                else:
                    record = os.pread(descriptor, _SIZE + 1, 0)
                    if record[:_PREFIX_SIZE] != prefix:
                        raise ValueError("writer sequence credential binding differs")
                    _checked_sequence_value(record, prefix)
                self._directory = directory
                self._descriptor = descriptor
                self._identity = identity
                self._root_identity = root_identity
                self._name = name
                self._owner_uid = owner_uid
                self._prefix = prefix
                self._closed = False
                self._mutex = threading.Lock()
            except BaseException:
                os.close(descriptor)
                raise
        except BaseException:
            os.close(directory)
            raise

    @staticmethod
    def _file_identity(descriptor: int, root_device: int, owner_uid: int) -> tuple[int, int]:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != owner_uid
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_nlink != 1
            or info.st_dev != root_device
        ):
            raise ValueError("writer sequence file ownership or type differs")
        return info.st_dev, info.st_ino

    def _check_attached(self) -> None:
        root = os.fstat(self._directory)
        if (
            (root.st_dev, root.st_ino) != self._root_identity
            or root.st_uid != self._owner_uid
            or stat.S_IMODE(root.st_mode) != 0o700
        ):
            raise ValueError("writer sequence directory changed")
        self._file_identity(self._descriptor, root.st_dev, self._owner_uid)
        linked = os.stat(self._name, dir_fd=self._directory, follow_symlinks=False)
        if (
            (linked.st_dev, linked.st_ino) != self._identity
            or not stat.S_ISREG(linked.st_mode)
            or linked.st_uid != self._owner_uid
            or stat.S_IMODE(linked.st_mode) != 0o600
            or linked.st_nlink != 1
        ):
            raise ValueError("writer sequence attachment changed")

    def next(self) -> int:
        """Persist the next sequence before the caller signs a request.

        Returns
        -------
        int
            Next credential-bound value, from 1 through 2**53 - 1. Values persist
            across reopen and are serialized across retained process handles.

        Raises
        ------
        ValueError
            On a closed handle, changed custody, damaged record or exhaustion.
        OSError
            On lock, write or durability failure. No value is returned, and
            possibly partial counter bytes are retained for reconciliation.
        """
        with self._mutex:
            if self._closed:
                raise ValueError("writer sequence file is closed")
            fcntl.flock(self._descriptor, fcntl.LOCK_EX)
            try:
                self._check_attached()
                record = os.pread(self._descriptor, _SIZE + 1, 0)
                previous = _checked_sequence_value(record, self._prefix)
                if previous >= _MAX:
                    raise ValueError("writer sequence budget exhausted")
                current = previous + 1
                if os.pwrite(self._descriptor, _sequence_record(self._prefix, current), 0) != _SIZE:
                    raise OSError("incomplete writer sequence write")
                os.fsync(self._descriptor)
                self._check_attached()
                return current
            finally:
                fcntl.flock(self._descriptor, fcntl.LOCK_UN)

    def close(self) -> None:
        """Release retained descriptors; never remove or reset the counter."""
        with self._mutex:
            if not self._closed:
                self._closed = True
                os.close(self._descriptor)
                os.close(self._directory)

    def __enter__(self) -> ProtectedWriterSequence:
        """Retain the opened file for the context lifetime."""
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        """Close descriptors on context exit."""
        self.close()
