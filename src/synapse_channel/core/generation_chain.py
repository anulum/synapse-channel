# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — hard-linked generation chain for conditional deployments
"""Publish, seal, and strictly read a directory-backed generation chain.

Each generation ``n >= 1`` is an immutable regular file named
``gen-{n:020d}.json`` inside an owner-only local directory. Publication writes
and syncs canonical ASCII JSON under a private ``.partial`` file, hard-links it
to the target slot (``FileExistsError`` refuses without overwriting), syncs the
directory, and unlinks the private name. A seal publishes ``kind="sealed"`` at
``base + 1`` carrying generation ``base``'s artefact forward (or 64 zeros when
``base == 0``).
"""

from __future__ import annotations

import ctypes
import json
import os
import re
import secrets
import stat
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from synapse_channel.core.errors import SynapseError
from synapse_channel.core.secure_path import (
    SecurePathError,
    assert_owner_only_dir_path,
    open_nofollow_leaf,
)

GENERATION_SCHEMA: Final = "synapse-generation.v1"
"""Schema identifier stored in every generation record."""

KIND_APPLIED: Final = "applied"
"""Generation kind written when a deployment publishes a new desired state."""

KIND_SEALED: Final = "sealed"
"""Generation kind written when a stopper seals an open attempt's slot."""

GENERATION_KINDS: Final = frozenset({KIND_APPLIED, KIND_SEALED})
"""Closed set of valid generation record kinds."""

STATUS_CREATED: Final = "created"
"""Publication status when the hard link claimed the slot."""

STATUS_REFUSED: Final = "refused"
"""Publication status when the target generation slot already existed."""

EMPTY_CHAIN_ARTEFACT: Final = "0" * 64
"""Carried-forward artefact digest when sealing generation 1 from base 0."""

MAX_GENERATION: Final = (1 << 64) - 1
"""Largest generation number representable in the 20-digit file name."""

FORBIDDEN_FS_MAGICS: Final = frozenset(
    {
        0x6969,  # NFS_SUPER_MAGIC
        0xFF534D42,  # CIFS_MAGIC_NUMBER
        0xFE534D42,  # SMB2_MAGIC_NUMBER
        0x517B,  # SMB_SUPER_MAGIC
        0x65735546,  # FUSE_SUPER_MAGIC
        0x65735543,  # FUSE_CTL_SUPER_MAGIC
        0x73636673,  # CEPH_SUPER_MAGIC
        0x01021997,  # V9FS_MAGIC (9P)
        0x47504653,  # GPFS
        0x0BD00BD0,  # LL_SUPER_MAGIC (Lustre)
    }
)
"""Linux ``statfs`` filesystem magics refused because hard-link atomicity is unverified."""

_RECORD_KEYS: Final = frozenset(
    {"artefact", "attempt", "generation", "kind", "operation", "schema"}
)
_IDENTIFIER: Final = re.compile(r"[A-Za-z0-9._:-]{1,128}")
_HEX64: Final = re.compile(r"[0-9a-f]{64}")
_GEN_FILE: Final = re.compile(r"gen-([0-9]{20})\.json")
_MAX_RECORD_BYTES: Final = 4096

StatfsProbe = Callable[[Path], int | None]
_STATFS_LIBC: Any = (
    ctypes.CDLL("libc.so.6", use_errno=True) if sys.platform.startswith("linux") else None
)


class GenerationChainError(SynapseError, ValueError):
    """Raised when a generation chain argument or directory violates its contract."""

    code = "generation_chain"


class GenerationChainCorruptionError(GenerationChainError):
    """Raised when an existing chain has a gap or a non-canonical slot file."""

    code = "generation_chain_corrupt"


def _require_generation_int(value: object, *, allow_zero: bool, label: str) -> int:
    """Validate an integer generation or base number, rejecting booleans."""
    minimum = 0 if allow_zero else 1
    if isinstance(value, bool) or not isinstance(value, int):
        raise GenerationChainError(f"{label} must be an integer")
    if value < minimum or value > MAX_GENERATION:
        raise GenerationChainError(f"{label} must lie in [{minimum}, {MAX_GENERATION}]")
    return value


def generation_name(number: int) -> str:
    """Return the 20-digit file name ``gen-{number:020d}.json`` for ``number``.

    Parameters
    ----------
    number : int
        Generation number in ``[1, 2**64 - 1]``.

    Returns
    -------
    str
        Canonical file name inside the chain directory.
    """
    checked = _require_generation_int(number, allow_zero=False, label="generation")
    return f"gen-{checked:020d}.json"


@dataclass(frozen=True, slots=True)
class GenerationRecord:
    """Immutable content of one ``synapse-generation.v1`` slot file.

    Attributes
    ----------
    generation : int
        Slot number ``>= 1`` matching the 20-digit file name.
    operation : str
        Declared operation identifier.
    attempt : str
        Attempt identifier competing for ``generation``.
    kind : str
        ``"applied"`` or ``"sealed"``.
    artefact : str
        Lower-case 64-character SHA-256 hex digest.
    schema : str
        Fixed ``"synapse-generation.v1"`` schema tag.
    """

    generation: int
    operation: str
    attempt: str
    kind: str
    artefact: str
    schema: str = GENERATION_SCHEMA

    def __post_init__(self) -> None:
        """Validate schema, integer generation range, identifiers, kind, and hex digest."""
        if self.schema != GENERATION_SCHEMA:
            raise GenerationChainError(f"unsupported generation schema: {self.schema!r}")
        _require_generation_int(self.generation, allow_zero=False, label="generation")
        if not isinstance(self.operation, str) or not _IDENTIFIER.fullmatch(self.operation):
            raise GenerationChainError("operation has the wrong form")
        if not isinstance(self.attempt, str) or not _IDENTIFIER.fullmatch(self.attempt):
            raise GenerationChainError("attempt has the wrong form")
        if self.kind not in GENERATION_KINDS:
            raise GenerationChainError(f"kind must be 'applied' or 'sealed', got {self.kind!r}")
        if not isinstance(self.artefact, str) or not _HEX64.fullmatch(self.artefact):
            raise GenerationChainError("artefact must be 64 lower-case hex digits")

    def to_dict(self) -> dict[str, int | str]:
        """Return the exact six-key dictionary representation."""
        return {
            "artefact": self.artefact,
            "attempt": self.attempt,
            "generation": self.generation,
            "kind": self.kind,
            "operation": self.operation,
            "schema": self.schema,
        }

    def canonical_bytes(self) -> bytes:
        """Return sorted-key compact ASCII JSON bytes with no trailing whitespace."""
        return json.dumps(
            self.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("ascii")


@dataclass(frozen=True, slots=True)
class PublishOutcome:
    """Result of attempting to claim generation ``base + 1``.

    Attributes
    ----------
    status : str
        ``"created"`` when this call linked the slot, or ``"refused"`` when
        the slot already existed.
    record : GenerationRecord
        The record now standing at ``base + 1`` (the caller's own record when
        ``status == "created"``, or the winning slot record read back strictly
        when ``status == "refused"``).
    """

    status: str
    record: GenerationRecord


def _linux_statfs_type(path: Path, *, libc: Any = _STATFS_LIBC) -> int | None:
    """Return the unsigned 32-bit ``statfs64.f_type`` on Linux, or ``None`` elsewhere."""
    if libc is None:
        return None
    buf = (ctypes.c_uint64 * 32)()
    rc = int(libc.statfs64(os.fsencode(path), buf))
    if rc != 0:
        err = ctypes.get_errno()
        raise GenerationChainError(
            f"generation chain directory: statfs64 failed on {path}: {os.strerror(err)}"
        )
    return int(buf[0]) & 0xFFFFFFFF


def validate_chain_dir(
    chain: str | Path, *, statfs_probe: StatfsProbe = _linux_statfs_type
) -> Path:
    """Prove ``chain`` is an owner-only local directory and not on NFS/CIFS/SMB/FUSE.

    Parameters
    ----------
    chain : str or pathlib.Path
        Candidate chain directory.
    statfs_probe : callable, optional
        Filesystem-type probe returning an integer ``statfs`` magic or ``None``.

    Returns
    -------
    pathlib.Path
        Validated ``Path`` object.
    """
    target = Path(chain)
    try:
        assert_owner_only_dir_path(target, purpose="generation chain directory")
    except SecurePathError as exc:
        raise GenerationChainError(str(exc)) from exc
    magic = statfs_probe(target)
    if magic is not None and (magic & 0xFFFFFFFF) in FORBIDDEN_FS_MAGICS:
        raise GenerationChainError(
            f"generation chain directory: {target} lies on a refused network or FUSE "
            f"filesystem (statfs magic 0x{magic & 0xFFFFFFFF:x})"
        )
    return target


def _fsync_directory(directory: Path, *, posix: bool = os.name == "posix") -> None:
    """Flush ``directory`` entries to stable storage on POSIX hosts."""
    if not posix:
        return
    fd = open_nofollow_leaf(directory, directory=True)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def chain_head(chain: str | Path, *, statfs_probe: StatfsProbe = _linux_statfs_type) -> int:
    """Return the highest contiguous generation in ``chain``, or ``0`` when empty.

    ``.partial`` files and non-generation names are ignored. Any ``gen-000...0.json``
    file or any missing generation below the highest generation raises
    :class:`GenerationChainCorruptionError`.

    Parameters
    ----------
    chain : str or pathlib.Path
        Owner-only local generation chain directory.
    statfs_probe : callable, optional
        Filesystem-type probe for ``validate_chain_dir``.

    Returns
    -------
    int
        Head generation number (``0`` for an empty chain).
    """
    directory = validate_chain_dir(chain, statfs_probe=statfs_probe)
    numbers: list[int] = []
    for entry in os.listdir(directory):
        match = _GEN_FILE.fullmatch(entry)
        if match is None:
            continue
        number = int(match.group(1))
        if number == 0 or number > MAX_GENERATION:
            raise GenerationChainCorruptionError(
                f"invalid generation file name {entry!r} in {directory}"
            )
        numbers.append(number)
    if not numbers:
        return 0
    highest = max(numbers)
    if len(numbers) != highest:
        missing = min(set(range(1, len(numbers) + 2)) - set(numbers))
        raise GenerationChainCorruptionError(
            f"generation chain {directory} has a gap below head {highest}: "
            f"missing {generation_name(missing)}"
        )
    return highest


def _read_slot_bytes(target: Path) -> bytes | None:
    """Read raw bytes from ``target`` without following symlinks, or ``None`` if absent."""
    try:
        fd = open_nofollow_leaf(target, directory=False)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise GenerationChainCorruptionError(
            f"cannot open generation file {target}: {exc.strerror or exc}"
        ) from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise GenerationChainCorruptionError(f"generation slot {target} is not a regular file")
        raw = os.read(fd, _MAX_RECORD_BYTES + 1)
    finally:
        os.close(fd)
    if len(raw) > _MAX_RECORD_BYTES:
        raise GenerationChainCorruptionError(f"generation slot {target} exceeds size bound")
    return raw


def _decode_strict_record(
    raw: bytes, *, expected_generation: int, target: Path
) -> GenerationRecord:
    """Decode ``raw`` and enforce exact key set, slot match, and canonical byte equality."""
    try:
        parsed = json.loads(raw.decode("ascii"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GenerationChainCorruptionError(
            f"generation slot {target} is not valid ASCII JSON"
        ) from exc
    if not isinstance(parsed, dict) or set(parsed) != _RECORD_KEYS:
        raise GenerationChainCorruptionError(
            f"generation slot {target} does not carry the exact synapse-generation.v1 keys"
        )
    try:
        record = GenerationRecord(
            generation=parsed["generation"],
            operation=parsed["operation"],
            attempt=parsed["attempt"],
            kind=parsed["kind"],
            artefact=parsed["artefact"],
            schema=parsed["schema"],
        )
    except GenerationChainError as exc:
        raise GenerationChainCorruptionError(f"generation slot {target}: {exc}") from exc
    if record.generation != expected_generation:
        raise GenerationChainCorruptionError(
            f"generation slot {target} records generation {record.generation}, "
            f"expected {expected_generation}"
        )
    if raw != record.canonical_bytes():
        raise GenerationChainCorruptionError(
            f"generation slot {target} is not in canonical JSON encoding"
        )
    return record


def read_generation(
    chain: str | Path,
    number: int,
    *,
    statfs_probe: StatfsProbe = _linux_statfs_type,
) -> GenerationRecord | None:
    """Read generation ``number`` strictly, returning ``None`` when absent.

    Parameters
    ----------
    chain : str or pathlib.Path
        Owner-only local generation chain directory.
    number : int
        Generation number ``>= 1`` to read.
    statfs_probe : callable, optional
        Filesystem-type probe for ``validate_chain_dir``.

    Returns
    -------
    GenerationRecord or None
        Verified generation record, or ``None`` if the slot file does not exist.
    """
    checked = _require_generation_int(number, allow_zero=False, label="generation")
    directory = validate_chain_dir(chain, statfs_probe=statfs_probe)
    target = directory / generation_name(checked)
    raw = _read_slot_bytes(target)
    if raw is None:
        return None
    return _decode_strict_record(raw, expected_generation=checked, target=target)


def _publish_record(
    directory: Path,
    candidate: GenerationRecord,
    *,
    statfs_probe: StatfsProbe,
) -> PublishOutcome:
    """Write ``candidate`` to a private ``.partial`` file and hard-link it once."""
    raw = candidate.canonical_bytes()
    nonce = secrets.token_hex(8)
    private = directory / f".{candidate.attempt}.{os.getpid()}.{nonce}.partial"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(private, flags, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(private, directory / generation_name(candidate.generation))
        except FileExistsError:
            winner = read_generation(directory, candidate.generation, statfs_probe=statfs_probe)
            if winner is None:
                raise GenerationChainCorruptionError(
                    f"slot {candidate.generation} vanished after FileExistsError"
                ) from None
            return PublishOutcome(status=STATUS_REFUSED, record=winner)
        _fsync_directory(directory)
        return PublishOutcome(status=STATUS_CREATED, record=candidate)
    finally:
        private.unlink(missing_ok=True)


def publish_generation(
    chain: str | Path,
    *,
    base: int,
    operation: str,
    attempt: str,
    artefact: str,
    statfs_probe: StatfsProbe = _linux_statfs_type,
) -> PublishOutcome:
    """Publish ``kind="applied"`` at ``base + 1`` by a single hard link.

    Parameters
    ----------
    chain : str or pathlib.Path
        Owner-only local generation chain directory.
    base : int
        Declared pre-state generation (``0`` for an empty chain).
    operation : str
        Operation identifier.
    attempt : str
        Attempt identifier claiming ``base + 1``.
    artefact : str
        Lower-case 64-hex SHA-256 of the deployed artefact.
    statfs_probe : callable, optional
        Filesystem-type probe for ``validate_chain_dir``.

    Returns
    -------
    PublishOutcome
        ``status="created"`` when linked, or ``status="refused"`` with the
        existing record at ``base + 1``.
    """
    checked_base = _require_generation_int(base, allow_zero=True, label="base")
    if checked_base == MAX_GENERATION:
        raise GenerationChainError("base generation cannot advance past 2**64 - 1")
    directory = validate_chain_dir(chain, statfs_probe=statfs_probe)
    if (
        checked_base > 0
        and read_generation(directory, checked_base, statfs_probe=statfs_probe) is None
    ):
        raise GenerationChainError(f"base generation {checked_base} does not exist in {directory}")
    candidate = GenerationRecord(
        generation=checked_base + 1,
        operation=operation,
        attempt=attempt,
        kind=KIND_APPLIED,
        artefact=artefact,
    )
    return _publish_record(directory, candidate, statfs_probe=statfs_probe)


def seal_generation(
    chain: str | Path,
    *,
    base: int,
    operation: str,
    attempt: str,
    statfs_probe: StatfsProbe = _linux_statfs_type,
) -> PublishOutcome:
    """Seal ``base + 1`` carrying generation ``base``'s artefact forward (or 64 zeros at 0).

    Parameters
    ----------
    chain : str or pathlib.Path
        Owner-only local generation chain directory.
    base : int
        Declared pre-state generation of the open attempt.
    operation : str
        Operation identifier.
    attempt : str
        Attempt identifier being sealed.
    statfs_probe : callable, optional
        Filesystem-type probe for ``validate_chain_dir``.

    Returns
    -------
    PublishOutcome
        ``status="created"`` when sealed first, or ``status="refused"`` with
        the record that already claimed ``base + 1`` (for example ``applied``).
    """
    checked_base = _require_generation_int(base, allow_zero=True, label="base")
    if checked_base == MAX_GENERATION:
        raise GenerationChainError("base generation cannot advance past 2**64 - 1")
    directory = validate_chain_dir(chain, statfs_probe=statfs_probe)
    if checked_base == 0:
        carried = EMPTY_CHAIN_ARTEFACT
    else:
        previous = read_generation(directory, checked_base, statfs_probe=statfs_probe)
        if previous is None:
            raise GenerationChainError(
                f"base generation {checked_base} does not exist in {directory}"
            )
        carried = previous.artefact
    candidate = GenerationRecord(
        generation=checked_base + 1,
        operation=operation,
        attempt=attempt,
        kind=KIND_SEALED,
        artefact=carried,
    )
    return _publish_record(directory, candidate, statfs_probe=statfs_probe)
