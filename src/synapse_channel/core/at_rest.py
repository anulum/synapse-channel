# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — at-rest encryption envelope for local storage artifacts
"""Opt-in at-rest encryption for append-only local storage artifacts.

This is the first at-rest tranche: an AES-256-GCM envelope plus atomic
encrypted-file helpers and a key-file permission check, wired into the artifacts
that are written whole and not live-queried by SQLite — relay logs, A2A state
files, archive reports, and cursor files. The live ``synapse hub --db`` SQLite
event store needs SQLCipher-class transparent encryption and stays a separate,
later tranche (see ``docs/at-rest-encryption``).

Encryption protects data when files are copied, backed up, or read offline. It
does not protect data while the hub is running and does not replace filesystem
permissions. The AES-GCM primitive comes from the optional ``cryptography``
dependency (``pip install synapse-channel[encryption]``); importing this module
never requires it — :func:`require_aes_gcm` raises a clear error only when an
encryption operation is actually attempted without it installed.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import secrets
import stat
import tempfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:  # pragma: no cover - typing-only import, never required at runtime.
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ENVELOPE_MAGIC = b"SYNAR\x01\x00\x00"
"""Versioned envelope header bound into the AES-GCM additional authenticated data."""

KEY_BYTES = 32
"""AES-256-GCM key length in bytes."""

NONCE_BYTES = 12
"""AES-GCM nonce length in bytes."""

DEFAULT_SCRYPT_N = 2**15
"""Default scrypt CPU/memory cost; a power of two."""

DEFAULT_SCRYPT_R = 8
"""Default scrypt block size parameter."""

DEFAULT_SCRYPT_P = 1
"""Default scrypt parallelisation parameter."""

SCRYPT_SALT_BYTES = 16
"""Salt length, in bytes, for passphrase key derivation."""

BACKUP_MANIFEST_SCHEMA = "synapse-at-rest-backup.v1"
"""Schema marker for at-rest encrypted backup manifests."""


@dataclass(frozen=True)
class AtRestSurface:
    """One local file protected by the at-rest encryption profile.

    Attributes
    ----------
    role : str
        Storage role, for example ``sqlite-event-store`` or ``relay-log``.
    path : pathlib.Path
        Concrete file path for that role.
    """

    role: str
    path: Path


@dataclass(frozen=True)
class AtRestSurfaceStatus:
    """Inspection status for one configured storage surface."""

    surface: AtRestSurface
    exists: bool
    encrypted: bool
    decryptable: bool
    reason: str


@dataclass(frozen=True)
class AtRestProfileReport:
    """Inspection report for an at-rest encryption profile."""

    statuses: tuple[AtRestSurfaceStatus, ...]

    @property
    def total(self) -> int:
        """Return the number of configured profile surfaces."""
        return len(self.statuses)

    @property
    def existing(self) -> int:
        """Return the number of configured surfaces currently present on disk."""
        return sum(1 for status in self.statuses if status.exists)

    @property
    def missing(self) -> int:
        """Return the number of configured surfaces absent from disk."""
        return sum(1 for status in self.statuses if not status.exists)

    @property
    def encrypted(self) -> int:
        """Return the number of existing surfaces using the envelope format."""
        return sum(1 for status in self.statuses if status.exists and status.encrypted)

    @property
    def plaintext(self) -> int:
        """Return the number of existing surfaces that are not encrypted."""
        return sum(1 for status in self.statuses if status.exists and not status.encrypted)


@dataclass(frozen=True)
class AtRestOperationResult:
    """Result counters for at-rest profile mutation commands."""

    changed: int
    skipped: int
    manifest: Path | None = None


class _AESGCMFactory(Protocol):
    """Callable building an AES-GCM cipher from a 32-byte key."""

    def __call__(self, key: bytes) -> AESGCM:
        """Return an AES-GCM cipher bound to ``key``."""


def require_aes_gcm() -> _AESGCMFactory:
    """Return the AES-GCM cipher class, raising a clear error when it is absent.

    Returns
    -------
    _AESGCMFactory
        The ``cryptography`` ``AESGCM`` class.

    Raises
    ------
    RuntimeError
        When the optional ``cryptography`` dependency is not installed.
    """
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as exc:  # pragma: no cover - exercised via a patched import in tests.
        raise RuntimeError(
            "at-rest encryption requires the optional 'cryptography' dependency; "
            "install it with: pip install synapse-channel[encryption]"
        ) from exc
    return AESGCM


def derive_key(passphrase: str, salt: bytes, *, n: int, r: int, p: int) -> bytes:
    """Derive a 32-byte key from a passphrase with the memory-hard scrypt KDF.

    Parameters
    ----------
    passphrase : str
        Human-supplied secret.
    salt : bytes
        Per-store random salt recorded in the key metadata.
    n, r, p : int
        scrypt cost, block-size, and parallelisation parameters.

    Returns
    -------
    bytes
        A 32-byte AES-256-GCM key.

    Notes
    -----
    ``maxmem`` is set from the parameters rather than left at OpenSSL's 32 MiB
    default: scrypt needs ``128 * n * r`` bytes, which the secure default
    ``n = 2**15`` already meets, so ``maxmem=0`` would make the default profile
    raise ``ValueError: memory limit exceeded``.
    """
    n_i, r_i = int(n), int(r)
    maxmem = 2 * 128 * n_i * r_i
    return hashlib.scrypt(
        passphrase.encode("utf-8"),
        salt=salt,
        n=n_i,
        r=r_i,
        p=int(p),
        dklen=KEY_BYTES,
        maxmem=maxmem,
    )


class AtRestCipher:
    """An AES-256-GCM envelope cipher over a 32-byte key.

    The envelope is ``ENVELOPE_MAGIC || nonce || AESGCM(nonce, plaintext, magic)``.
    The magic header is bound as additional authenticated data, so a blob written
    by another format or version fails authentication rather than decrypting to
    garbage.
    """

    def __init__(self, key: bytes) -> None:
        if len(key) != KEY_BYTES:
            raise ValueError(f"at-rest key must be {KEY_BYTES} bytes, got {len(key)}")
        self._key = bytes(key)
        self._aesgcm = require_aes_gcm()(self._key)

    @classmethod
    def from_passphrase(
        cls,
        passphrase: str,
        salt: bytes,
        *,
        n: int = DEFAULT_SCRYPT_N,
        r: int = DEFAULT_SCRYPT_R,
        p: int = DEFAULT_SCRYPT_P,
    ) -> AtRestCipher:
        """Build a cipher from a passphrase and salt via scrypt."""
        return cls(derive_key(passphrase, salt, n=n, r=r, p=p))

    @classmethod
    def from_key_file(cls, path: str | Path) -> AtRestCipher:
        """Build a cipher from a raw 32-byte key file after a permission check.

        Parameters
        ----------
        path : str or pathlib.Path
            Key-file path holding exactly :data:`KEY_BYTES` raw bytes.

        Returns
        -------
        AtRestCipher
            A cipher bound to the file's key.

        Raises
        ------
        ValueError
            When the key file fails its ownership/mode check or has a wrong size.
        """
        target = Path(path)
        try:
            fd = os.open(target, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        except FileNotFoundError as exc:
            raise ValueError(f"key file does not exist: {target}") from exc
        except OSError as exc:  # O_NOFOLLOW raises (ELOOP) when the path is a symlink.
            raise ValueError(f"key file must not be a symlink: {target}") from exc
        try:
            ok, reason = _validate_key_stat(os.fstat(fd), target)
            if not ok:
                raise ValueError(reason)
            return cls(os.read(fd, KEY_BYTES))
        finally:
            os.close(fd)

    def encrypt(self, plaintext: bytes) -> bytes:
        """Return the AES-GCM envelope for ``plaintext`` with a fresh nonce."""
        nonce = secrets.token_bytes(NONCE_BYTES)
        sealed = self._aesgcm.encrypt(nonce, plaintext, ENVELOPE_MAGIC)
        return ENVELOPE_MAGIC + nonce + sealed

    def decrypt(self, blob: bytes) -> bytes:
        """Return the plaintext for an AES-GCM envelope, verifying the header.

        Raises
        ------
        ValueError
            When the header is missing or the blob is truncated.
        cryptography.exceptions.InvalidTag
            When authentication fails (wrong key or tampered ciphertext).
        """
        header = len(ENVELOPE_MAGIC)
        if len(blob) < header + NONCE_BYTES or not blob.startswith(ENVELOPE_MAGIC):
            raise ValueError("not a Synapse at-rest envelope")
        nonce = blob[header : header + NONCE_BYTES]
        sealed = blob[header + NONCE_BYTES :]
        decrypted: bytes = self._aesgcm.decrypt(nonce, sealed, ENVELOPE_MAGIC)
        return decrypted


def is_envelope(blob: bytes) -> bool:
    """Return whether ``blob`` begins with the at-rest envelope header."""
    return blob.startswith(ENVELOPE_MAGIC)


def generate_key_file(path: str | Path) -> Path:
    """Write a fresh random 32-byte key to ``path`` with owner-only permissions.

    Parameters
    ----------
    path : str or pathlib.Path
        Destination key-file path; refused when it already exists.

    Returns
    -------
    pathlib.Path
        The written key-file path.

    Raises
    ------
    FileExistsError
        When ``path`` already exists, so an existing key is never overwritten.
    """
    return _write_new_key_file(Path(path), secrets.token_bytes(KEY_BYTES))


def generate_key_file_from_passphrase(
    path: str | Path,
    passphrase: str,
    *,
    n: int = DEFAULT_SCRYPT_N,
    r: int = DEFAULT_SCRYPT_R,
    p: int = DEFAULT_SCRYPT_P,
) -> Path:
    """Derive a 32-byte key from a passphrase with scrypt and write it owner-only.

    The scrypt cost parameters are the caller's to tune for a security/performance
    trade-off (``n`` must be a power of two; larger ``n`` costs ``128 * n * r``
    bytes of memory). A fresh random salt is drawn per derivation and then
    discarded: the written file is a normal 32-byte key of record, protected
    exactly like a randomly generated one, and the passphrase alone cannot
    reconstruct it — the file is authoritative, the passphrase is only its source
    at creation. Prefer the random :func:`generate_key_file` unless a passphrase
    source is specifically wanted.

    Parameters
    ----------
    path : str or pathlib.Path
        Destination key-file path; refused when it already exists.
    passphrase : str
        Non-empty passphrase to derive the key from.
    n, r, p : int, optional
        scrypt cost, block-size, and parallelisation parameters.

    Returns
    -------
    pathlib.Path
        The written key-file path.

    Raises
    ------
    ValueError
        When the passphrase is empty or the scrypt parameters are invalid.
    FileExistsError
        When ``path`` already exists, so an existing key is never overwritten.
    """
    if not passphrase:
        raise ValueError("passphrase must not be empty")
    salt = secrets.token_bytes(SCRYPT_SALT_BYTES)
    key = derive_key(passphrase, salt, n=n, r=r, p=p)
    return _write_new_key_file(Path(path), key)


def _write_new_key_file(target: Path, key_bytes: bytes) -> Path:
    """Write ``key_bytes`` to a new owner-only (0600) file, never overwriting."""
    if target.exists():
        raise FileExistsError(f"refusing to overwrite existing key file: {target}")
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(key_bytes)
    return target


def _validate_key_stat(info: os.stat_result, target: Path) -> tuple[bool, str]:
    """Validate a key file's stat result for regularity, mode, owner, and size."""
    if not stat.S_ISREG(info.st_mode):
        return False, f"key file is not a regular file: {target}"
    if info.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        return False, f"key file must be owner-only (chmod 600): {target}"
    if hasattr(os, "geteuid") and info.st_uid != os.geteuid():
        return False, f"key file must be owned by the current user: {target}"
    if info.st_size != KEY_BYTES:
        return False, f"key file must hold exactly {KEY_BYTES} bytes: {target}"
    return True, "ok"


def check_key_file(path: str | Path) -> tuple[bool, str]:
    """Verify a key file exists, is owner-only, and holds a full-length key.

    Uses :func:`os.lstat`, so a symlink at the key path is reported as a
    non-regular file rather than silently validated against its target.

    Parameters
    ----------
    path : str or pathlib.Path
        Key-file path to check.

    Returns
    -------
    tuple[bool, str]
        ``(True, "ok")`` when the key file is safe, otherwise ``(False, reason)``.
    """
    target = Path(path)
    try:
        info = os.lstat(target)
    except FileNotFoundError:
        return False, f"key file does not exist: {target}"
    return _validate_key_stat(info, target)


def encrypt_file(path: str | Path, plaintext: bytes, cipher: AtRestCipher) -> None:
    """Atomically write an encrypted envelope of ``plaintext`` to ``path``.

    The ciphertext is written to a fresh ``mkstemp`` sibling — a random,
    ``O_EXCL``, ``0o600`` file that cannot be a pre-planted symlink — and renamed
    into place, so a reader never observes a half-written envelope and an attacker
    with directory write access cannot redirect the write. The temporary file is
    unlinked if anything fails before the rename.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(cipher.encrypt(plaintext))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, target)
        with contextlib.suppress(OSError):
            target.chmod(0o600)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise


def decrypt_file(path: str | Path, cipher: AtRestCipher) -> bytes:
    """Read and decrypt an at-rest envelope file, returning its plaintext."""
    return cipher.decrypt(Path(path).read_bytes())


def sqlite_sidecar_surfaces(path: str | Path) -> tuple[AtRestSurface, ...]:
    """Return the SQLite event-store surface plus its WAL and SHM sidecars.

    Parameters
    ----------
    path : str or pathlib.Path
        Main SQLite event-store path configured through ``synapse hub --db``.

    Returns
    -------
    tuple[AtRestSurface, ...]
        Main database, write-ahead-log sidecar, and shared-memory sidecar
        surfaces in deterministic order. Missing sidecars are still returned so
        fail-safe startup checks can reject a plaintext sidecar if it appears.
    """
    db = Path(path)
    return (
        AtRestSurface("sqlite-event-store", db),
        AtRestSurface("sqlite-wal", Path(f"{db}-wal")),
        AtRestSurface("sqlite-shm", Path(f"{db}-shm")),
    )


def full_profile_surfaces(
    *,
    sqlite_event_stores: Iterable[str | Path] = (),
    relay_logs: Iterable[str | Path] = (),
    a2a_state_files: Iterable[str | Path] = (),
    cursor_files: Iterable[str | Path] = (),
    archive_outputs: Iterable[str | Path] = (),
) -> tuple[AtRestSurface, ...]:
    """Return every file surface covered by the published at-rest profile.

    Parameters
    ----------
    sqlite_event_stores : iterable of str or pathlib.Path, optional
        ``synapse hub --db`` SQLite event-store files. Each contributes its
        main file, ``-wal`` sidecar, and ``-shm`` sidecar.
    relay_logs : iterable of str or pathlib.Path, optional
        Relay NDJSON logs mirrored by hub or relay workflows.
    a2a_state_files : iterable of str or pathlib.Path, optional
        Persisted Agent2Agent bridge task/push-configuration files.
    cursor_files : iterable of str or pathlib.Path, optional
        Byte or sequence cursor files used by relay and ingest consumers.
    archive_outputs : iterable of str or pathlib.Path, optional
        Static archive/postmortem/report outputs that may embed coordination
        evidence.

    Returns
    -------
    tuple[AtRestSurface, ...]
        Deterministically ordered surfaces suitable for inspection, migration,
        rekey, backup, and recovery.
    """
    surfaces: list[AtRestSurface] = []
    for db in sqlite_event_stores:
        surfaces.extend(sqlite_sidecar_surfaces(db))
    surfaces.extend(AtRestSurface("relay-log", Path(path)) for path in relay_logs)
    surfaces.extend(AtRestSurface("a2a-state-file", Path(path)) for path in a2a_state_files)
    surfaces.extend(AtRestSurface("cursor-file", Path(path)) for path in cursor_files)
    surfaces.extend(AtRestSurface("archive-output", Path(path)) for path in archive_outputs)
    return tuple(surfaces)


def inspect_profile(
    surfaces: Iterable[AtRestSurface],
    cipher: AtRestCipher | None = None,
) -> AtRestProfileReport:
    """Inspect configured at-rest surfaces without mutating them.

    Parameters
    ----------
    surfaces : iterable of AtRestSurface
        Profile surfaces to inspect.
    cipher : AtRestCipher or None, optional
        Cipher used to verify encrypted files are decryptable. When omitted,
        encrypted files are identified by header only.

    Returns
    -------
    AtRestProfileReport
        Per-surface status plus aggregate counters.
    """
    statuses: list[AtRestSurfaceStatus] = []
    for surface in surfaces:
        if not surface.path.exists():
            statuses.append(AtRestSurfaceStatus(surface, False, False, False, "missing"))
            continue
        try:
            blob = surface.path.read_bytes()
        except OSError as exc:
            statuses.append(AtRestSurfaceStatus(surface, True, False, False, str(exc)))
            continue
        encrypted = is_envelope(blob)
        if not encrypted:
            statuses.append(AtRestSurfaceStatus(surface, True, False, False, "plaintext"))
            continue
        if cipher is None:
            statuses.append(AtRestSurfaceStatus(surface, True, True, False, "encrypted"))
            continue
        try:
            cipher.decrypt(blob)
        except Exception as exc:
            statuses.append(
                AtRestSurfaceStatus(surface, True, True, False, f"cannot decrypt: {exc}")
            )
            continue
        statuses.append(AtRestSurfaceStatus(surface, True, True, True, "ok"))
    return AtRestProfileReport(tuple(statuses))


def require_encrypted_profile(
    surfaces: Iterable[AtRestSurface],
    cipher: AtRestCipher,
) -> AtRestProfileReport:
    """Fail closed unless every existing profile surface is encrypted and readable.

    Missing files are allowed because a first start may create them later. Any
    present plaintext file, unreadable file, or envelope that cannot be
    authenticated by ``cipher`` raises ``ValueError`` before startup proceeds.
    """
    report = inspect_profile(surfaces, cipher)
    problems = [
        status
        for status in report.statuses
        if status.exists and (not status.encrypted or not status.decryptable)
    ]
    if problems:
        first = problems[0]
        raise ValueError(
            f"{first.surface.role} is not safe for encrypted startup: "
            f"{first.surface.path} ({first.reason})"
        )
    return report


def _write_owner_only(path: Path, payload: bytes) -> None:
    """Atomically write ``payload`` to ``path`` with owner-only permissions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
        with contextlib.suppress(OSError):
            path.chmod(0o600)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise


def _prepare_backup_dir(path: str | Path) -> Path:
    """Create an owner-only backup directory and return it as a path."""
    backup_dir = Path(path)
    backup_dir.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        backup_dir.chmod(0o700)
    return backup_dir


def _backup_name(index: int, surface: AtRestSurface, suffix: str) -> str:
    """Return a deterministic backup filename for one surface."""
    return f"{index:04d}-{surface.path.name}{suffix}"


def _copy_to_backup(
    surface: AtRestSurface,
    *,
    backup_dir: Path,
    index: int,
    suffix: str,
) -> Path:
    """Copy one surface into ``backup_dir`` and return the backup path."""
    backup_path = backup_dir / _backup_name(index, surface, suffix)
    _write_owner_only(backup_path, surface.path.read_bytes())
    return backup_path


def migrate_profile(
    surfaces: Iterable[AtRestSurface],
    cipher: AtRestCipher,
    *,
    backup_dir: str | Path | None = None,
) -> AtRestOperationResult:
    """Encrypt every existing plaintext surface in a profile.

    Existing encrypted surfaces are authenticated with ``cipher`` and skipped.
    Plaintext surfaces are optionally copied to ``backup_dir`` before being
    atomically replaced by an AES-GCM envelope. Missing surfaces are skipped.
    """
    backup = _prepare_backup_dir(backup_dir) if backup_dir is not None else None
    changed = 0
    skipped = 0
    for index, surface in enumerate(tuple(surfaces), start=1):
        if not surface.path.exists():
            skipped += 1
            continue
        blob = surface.path.read_bytes()
        if is_envelope(blob):
            cipher.decrypt(blob)
            skipped += 1
            continue
        if backup is not None:
            _copy_to_backup(surface, backup_dir=backup, index=index, suffix=".plain")
        encrypt_file(surface.path, blob, cipher)
        changed += 1
    return AtRestOperationResult(changed=changed, skipped=skipped)


def rekey_profile(
    surfaces: Iterable[AtRestSurface],
    old_cipher: AtRestCipher,
    new_cipher: AtRestCipher,
    *,
    backup_dir: str | Path | None = None,
) -> AtRestOperationResult:
    """Decrypt every encrypted surface with ``old_cipher`` and seal it anew.

    Plaintext files are refused so operators do not accidentally hide a missed
    migration inside a rotation procedure.
    """
    backup = _prepare_backup_dir(backup_dir) if backup_dir is not None else None
    changed = 0
    skipped = 0
    for index, surface in enumerate(tuple(surfaces), start=1):
        if not surface.path.exists():
            skipped += 1
            continue
        blob = surface.path.read_bytes()
        if not is_envelope(blob):
            raise ValueError(
                f"{surface.role} is plaintext; run migrate before rekey: {surface.path}"
            )
        plaintext = old_cipher.decrypt(blob)
        if backup is not None:
            _copy_to_backup(surface, backup_dir=backup, index=index, suffix=".encrypted")
        encrypt_file(surface.path, plaintext, new_cipher)
        changed += 1
    return AtRestOperationResult(changed=changed, skipped=skipped)


def backup_profile(
    surfaces: Iterable[AtRestSurface],
    backup_dir: str | Path,
    cipher: AtRestCipher,
) -> Path:
    """Create an encrypted-profile backup manifest and return its path.

    The backup copies encrypted bytes exactly as stored and verifies each copied
    file with ``cipher``. Key material is deliberately not included in the
    bundle.
    """
    profile = tuple(surfaces)
    require_encrypted_profile(profile, cipher)
    destination = _prepare_backup_dir(backup_dir)
    entries: list[dict[str, str]] = []
    for index, surface in enumerate(profile, start=1):
        if not surface.path.exists():
            continue
        backup_path = _copy_to_backup(
            surface, backup_dir=destination, index=index, suffix=".encrypted"
        )
        entries.append(
            {
                "role": surface.role,
                "source_path": str(surface.path),
                "backup_path": str(backup_path),
            }
        )
    manifest = destination / "manifest.json"
    payload = {
        "schema_version": BACKUP_MANIFEST_SCHEMA,
        "files": entries,
    }
    _write_owner_only(
        manifest,
        json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True).encode("utf-8") + b"\n",
    )
    return manifest


def _manifest_entries(manifest_path: Path) -> Sequence[dict[str, str]]:
    """Read and validate an at-rest backup manifest."""
    raw: Any = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("schema_version") != BACKUP_MANIFEST_SCHEMA:
        raise ValueError(f"not a Synapse at-rest backup manifest: {manifest_path}")
    files = raw.get("files")
    if not isinstance(files, list):
        raise ValueError(f"at-rest backup manifest has no file list: {manifest_path}")
    entries: list[dict[str, str]] = []
    for entry in files:
        if not isinstance(entry, dict):
            raise ValueError(
                f"at-rest backup manifest contains a malformed file entry: {manifest_path}"
            )
        role = entry.get("role")
        source_path = entry.get("source_path")
        backup_path = entry.get("backup_path")
        if (
            not isinstance(role, str)
            or not isinstance(source_path, str)
            or not isinstance(backup_path, str)
        ):
            raise ValueError(
                f"at-rest backup manifest contains a malformed file entry: {manifest_path}"
            )
        entries.append({"role": role, "source_path": source_path, "backup_path": backup_path})
    return tuple(entries)


def restore_profile_backup(
    manifest_path: str | Path,
    cipher: AtRestCipher,
) -> AtRestOperationResult:
    """Restore encrypted files listed in a backup manifest.

    Each backup file is authenticated with ``cipher`` before it is atomically
    written back to its recorded source path. The function cannot recover lost
    key material; a wrong or missing key fails before data is restored.
    """
    manifest = Path(manifest_path)
    entries = _manifest_entries(manifest)
    changed = 0
    for entry in entries:
        backup_path = Path(entry["backup_path"])
        source_path = Path(entry["source_path"])
        blob = backup_path.read_bytes()
        if not is_envelope(blob):
            raise ValueError(f"backup file is plaintext or corrupt: {backup_path}")
        cipher.decrypt(blob)
        _write_owner_only(source_path, blob)
        changed += 1
    return AtRestOperationResult(changed=changed, skipped=0, manifest=manifest)
