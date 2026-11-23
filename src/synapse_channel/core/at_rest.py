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
files, archive reports, and cursor files. The live ``synapse hub --db`` event
store uses optional SQLCipher page encryption when ``--db-key-file`` is set
(see :mod:`synapse_channel.core.persistence_sqlcipher` and
``docs/at-rest-encryption``).

Encryption protects data when files are copied, backed up, or read offline. It
does not protect data while the hub is running and does not replace filesystem
permissions. The AES-GCM primitive comes from the optional ``cryptography``
dependency (``pip install synapse-channel[encryption]``); importing this module
never requires it — :func:`require_aes_gcm` raises a clear error only when an
encryption operation is actually attempted without it installed.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import secrets
import tempfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from synapse_channel.core.at_rest_counter import InMemoryMessageCounter, MessageCounter
from synapse_channel.core.at_rest_format import (
    BACKUP_MANIFEST_SCHEMA,
    DEFAULT_SCRYPT_N,
    DEFAULT_SCRYPT_P,
    DEFAULT_SCRYPT_R,
    ENVELOPE_MAGIC,
    GCM_MESSAGE_LIMIT,
    GCM_REKEY_WARNING_THRESHOLD,
    KEY_BYTES,
    NONCE_BYTES,
    require_aes_gcm,
)
from synapse_channel.core.at_rest_keys import (
    AtRestKeyExhausted,
    _load_wrapped_key,
    derive_key,
    load_key_file,
    write_owner_only,
)

logger = logging.getLogger("synapse.at_rest")


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


class AtRestCipher:
    """An AES-256-GCM envelope cipher over a 32-byte key.

    The envelope is ``ENVELOPE_MAGIC || nonce || AESGCM(nonce, plaintext, magic)``.
    The magic header is bound as additional authenticated data, so a blob written
    by another format or version fails authentication rather than decrypting to
    garbage.

    The cipher counts the messages it seals and refuses to encrypt past
    :data:`GCM_MESSAGE_LIMIT`, logging a one-time warning at
    :data:`GCM_REKEY_WARNING_THRESHOLD` so the key can be rotated in good time.
    The count is kept by an injected
    :class:`~synapse_channel.core.at_rest_counter.MessageCounter`. The default,
    :class:`~synapse_channel.core.at_rest_counter.InMemoryMessageCounter`, is
    per-process and resets whenever the cipher is rebuilt (a hub restart, a key
    reload) — it guards one long-running process. A long-lived store that must
    hold a key's cumulative lifetime across restarts passes a
    :class:`~synapse_channel.core.at_rest_counter.PersistentMessageCounter`, so
    the limit is enforced over the key's whole life rather than one process.
    """

    def __init__(self, key: bytes, *, counter: MessageCounter | None = None) -> None:
        if len(key) != KEY_BYTES:
            raise ValueError(f"at-rest key must be {KEY_BYTES} bytes, got {len(key)}")
        self._key = bytes(key)
        self._aesgcm = require_aes_gcm()(self._key)
        self._counter: MessageCounter = counter if counter is not None else InMemoryMessageCounter()
        self._warned = False

    @property
    def encrypted_count(self) -> int:
        """Return how many messages have been sealed under this key, per its counter."""
        return self._counter.count

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
    def from_key_file(
        cls, path: str | Path, *, counter: MessageCounter | None = None
    ) -> AtRestCipher:
        """Build a cipher from a raw 32-byte key file after a permission check.

        Parameters
        ----------
        path : str or pathlib.Path
            Key-file path holding exactly :data:`KEY_BYTES` raw bytes.
        counter : MessageCounter or None, optional
            The message counter enforcing the AES-GCM per-key limit; pass a
            :class:`~synapse_channel.core.at_rest_counter.PersistentMessageCounter` to hold the
            key's cumulative count across restarts. Defaults to per-process in-memory counting.

        Returns
        -------
        AtRestCipher
            A cipher bound to the file's key.

        Raises
        ------
        ValueError
            When the key file fails its ownership/mode check or has a wrong size.
        """
        return cls(load_key_file(path), counter=counter)

    @classmethod
    def from_wrapped_key_file(
        cls, path: str | Path, passphrase: str, *, counter: MessageCounter | None = None
    ) -> AtRestCipher:
        """Build a cipher from an envelope-encrypted (KEK-wrapped) key file and its passphrase.

        Parameters
        ----------
        path : str or pathlib.Path
            Wrapped-key file written by :func:`generate_wrapped_key_file`.
        passphrase : str
            The passphrase whose scrypt-derived key-encryption key unwraps the data key.
        counter : MessageCounter or None, optional
            The message counter enforcing the AES-GCM per-key limit; pass a
            :class:`~synapse_channel.core.at_rest_counter.PersistentMessageCounter` to hold the
            key's cumulative count across restarts. Defaults to per-process in-memory counting.

        Returns
        -------
        AtRestCipher
            A cipher bound to the unwrapped data key.

        Raises
        ------
        ValueError
            When the file is not a wrapped key file, is malformed, or the passphrase is wrong.
        """
        return cls(_load_wrapped_key(Path(path), passphrase), counter=counter)

    def encrypt(self, plaintext: bytes) -> bytes:
        """Return the AES-GCM envelope for ``plaintext`` with a fresh nonce.

        Raises
        ------
        AtRestKeyExhausted
            When this instance has already sealed :data:`GCM_MESSAGE_LIMIT`
            messages under its key. The cap is checked before a nonce is drawn,
            so a key at its limit never risks a fresh nonce colliding with an
            earlier one.
        """
        sealed_so_far = self._counter.count
        if sealed_so_far >= GCM_MESSAGE_LIMIT:
            raise AtRestKeyExhausted(
                f"at-rest key has reached its AES-GCM message limit "
                f"({GCM_MESSAGE_LIMIT} messages); rotate the key before encrypting more"
            )
        if not self._warned and sealed_so_far >= GCM_REKEY_WARNING_THRESHOLD:
            logger.warning(
                "at-rest key has sealed %d of %d AES-GCM messages; rotate the key soon",
                sealed_so_far,
                GCM_MESSAGE_LIMIT,
            )
            self._warned = True
        nonce = secrets.token_bytes(NONCE_BYTES)
        sealed = self._aesgcm.encrypt(nonce, plaintext, ENVELOPE_MAGIC)
        self._counter.increment()
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


def _prepare_backup_dir(path: str | Path) -> Path:
    """Create an owner-only backup directory and return it as a path."""
    from synapse_channel.core.secure_path import SecurePathError, apply_owner_only_dir

    backup_dir = Path(path)
    backup_dir.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError, SecurePathError):
        apply_owner_only_dir(backup_dir)
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
    write_owner_only(backup_path, surface.path.read_bytes())
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
    write_owner_only(
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
        write_owner_only(source_path, blob)
        changed += 1
    return AtRestOperationResult(changed=changed, skipped=0, manifest=manifest)
