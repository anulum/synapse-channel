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

import hashlib
import os
import secrets
import stat
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

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
    """
    return hashlib.scrypt(
        passphrase.encode("utf-8"),
        salt=salt,
        n=int(n),
        r=int(r),
        p=int(p),
        dklen=KEY_BYTES,
        maxmem=0,
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
        ok, reason = check_key_file(path)
        if not ok:
            raise ValueError(reason)
        return cls(Path(path).read_bytes())

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
    target = Path(path)
    if target.exists():
        raise FileExistsError(f"refusing to overwrite existing key file: {target}")
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(secrets.token_bytes(KEY_BYTES))
    return target


def check_key_file(path: str | Path) -> tuple[bool, str]:
    """Verify a key file exists, is owner-only, and holds a full-length key.

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
        info = target.stat()
    except FileNotFoundError:
        return False, f"key file does not exist: {target}"
    if not stat.S_ISREG(info.st_mode):
        return False, f"key file is not a regular file: {target}"
    if info.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        return False, f"key file must be owner-only (chmod 600): {target}"
    if hasattr(os, "geteuid") and info.st_uid != os.geteuid():
        return False, f"key file must be owned by the current user: {target}"
    if info.st_size != KEY_BYTES:
        return False, f"key file must hold exactly {KEY_BYTES} bytes: {target}"
    return True, "ok"


def encrypt_file(path: str | Path, plaintext: bytes, cipher: AtRestCipher) -> None:
    """Atomically write an encrypted envelope of ``plaintext`` to ``path``.

    The ciphertext is written to a sibling temporary file and renamed into place,
    so a reader never observes a half-written envelope.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + ".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(cipher.encrypt(plaintext))
    os.replace(temp, target)


def decrypt_file(path: str | Path, cipher: AtRestCipher) -> bytes:
    """Read and decrypt an at-rest envelope file, returning its plaintext."""
    return cipher.decrypt(Path(path).read_bytes())
