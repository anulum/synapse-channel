# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — key lifecycle for at-rest encryption
"""Key generation, passphrase derivation, AES-KW wrapping, and key-file checks.

This module owns the at-rest key lifecycle: scrypt key derivation, raw and
passphrase-derived key file generation, RFC 3394 AES-KW wrapping and
unwrapping, wrapped-key JSON document serialisation and passphrase rotation,
owner-only atomic file writes, key-file permission checks, and the per-key
AES-GCM exhaustion exception.

Importing this module never requires the optional ``cryptography`` dependency:
AES key wrap is loaded lazily inside :func:`_require_key_wrap` only when a
wrap or unwrap operation is requested.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import stat
import tempfile
from pathlib import Path
from typing import Any, Protocol

from synapse_channel.core.at_rest_format import (
    DEFAULT_SCRYPT_N,
    DEFAULT_SCRYPT_P,
    DEFAULT_SCRYPT_R,
    KEY_BYTES,
    PASSPHRASE_SCRYPT_BACKEND,
    SCRYPT_SALT_BYTES,
    WRAPPED_KEY_SCHEMA,
)


class AtRestKeyExhausted(RuntimeError):
    """Raised when a cipher instance reaches its per-key AES-GCM message cap.

    This is a fail-closed backstop, not a routine condition: reaching
    :data:`GCM_MESSAGE_LIMIT` messages under a single key means the key must be
    rotated before any further data is sealed with it.
    """


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
    from synapse_channel.core.secure_path import apply_owner_only_file

    if target.exists():
        raise FileExistsError(f"refusing to overwrite existing key file: {target}")
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(key_bytes)
    apply_owner_only_file(target)
    return target


def _require_key_wrap() -> tuple[Any, Any, type[Exception]]:
    """Return the RFC 3394 AES-KW wrap/unwrap callables and the unwrap-failure type.

    Raises
    ------
    RuntimeError
        When the optional ``cryptography`` dependency is not installed.
    """
    try:
        from cryptography.hazmat.primitives.keywrap import (
            InvalidUnwrap,
            aes_key_unwrap,
            aes_key_wrap,
        )
    except ImportError as exc:
        raise RuntimeError(
            "at-rest key wrapping requires the optional 'cryptography' dependency; "
            "install it with: pip install synapse-channel[encryption]"
        ) from exc
    return aes_key_wrap, aes_key_unwrap, InvalidUnwrap


def wrap_data_key(data_key: bytes, key_encryption_key: bytes) -> bytes:
    """Wrap a data key under a key-encryption key with RFC 3394 AES-KW.

    Parameters
    ----------
    data_key : bytes
        The :data:`KEY_BYTES`-length AES-256-GCM data key to protect.
    key_encryption_key : bytes
        A 16-, 24-, or 32-byte key-encryption key (from a passphrase now; from an HSM later).

    Returns
    -------
    bytes
        The wrapped data key (deterministic, integrity-checked on unwrap).

    Raises
    ------
    ValueError
        When either key has an invalid length.
    """
    if len(data_key) != KEY_BYTES:
        raise ValueError(f"data key must be {KEY_BYTES} bytes, got {len(data_key)}")
    if len(key_encryption_key) not in (16, 24, 32):
        raise ValueError("key-encryption key must be 16, 24, or 32 bytes")
    aes_key_wrap, _unwrap, _invalid = _require_key_wrap()
    wrapped: bytes = aes_key_wrap(key_encryption_key, data_key)
    return wrapped


def unwrap_data_key(wrapped: bytes, key_encryption_key: bytes) -> bytes:
    """Recover a data key wrapped with :func:`wrap_data_key`.

    Parameters
    ----------
    wrapped : bytes
        The wrapped data key.
    key_encryption_key : bytes
        The 16-, 24-, or 32-byte key-encryption key.

    Returns
    -------
    bytes
        The recovered data key.

    Raises
    ------
    ValueError
        When the key-encryption key has an invalid length, is wrong, or the wrapped blob is corrupt.
    """
    if len(key_encryption_key) not in (16, 24, 32):
        raise ValueError("key-encryption key must be 16, 24, or 32 bytes")
    _wrap, aes_key_unwrap, invalid_unwrap = _require_key_wrap()
    try:
        recovered: bytes = aes_key_unwrap(key_encryption_key, wrapped)
    except invalid_unwrap as exc:
        raise ValueError(
            "cannot unwrap data key: wrong key-encryption key or corrupt wrapped key"
        ) from exc
    return recovered


class KeyEncryptionKey(Protocol):
    """Wraps and unwraps an at-rest data key.

    A key-encryption key (KEK) never touches stored data directly — it only wraps the random data
    key :class:`AtRestCipher` uses for bulk AES-GCM. The backend that provides it — a passphrase
    now, a PKCS#11 token, a TPM, or a cloud HSM in an optional backend — is recorded in the
    wrapped-key file so a fresh process can rebuild it. Rotating the KEK re-wraps the same data key,
    so no encrypted data is ever rewritten.
    """

    def wrap(self, data_key: bytes) -> bytes:
        """Return ``data_key`` wrapped under this key-encryption key."""

    def unwrap(self, wrapped: bytes) -> bytes:
        """Return the data key recovered from ``wrapped``."""


class PassphraseKeyEncryptionKey:
    """A software key-encryption key derived from a passphrase with scrypt.

    The derived key wraps and unwraps the data key with RFC 3394 AES-KW in-process. This is the
    default, dependency-free backend; hardware backends keep their key material in the device while
    implementing the same :class:`KeyEncryptionKey` shape.
    """

    def __init__(self, kek: bytes) -> None:
        self._kek = bytes(kek)

    def wrap(self, data_key: bytes) -> bytes:
        """Wrap ``data_key`` under the passphrase-derived key with RFC 3394 AES-KW."""
        return wrap_data_key(data_key, self._kek)

    def unwrap(self, wrapped: bytes) -> bytes:
        """Unwrap the data key under the passphrase-derived key with RFC 3394 AES-KW."""
        return unwrap_data_key(wrapped, self._kek)


def _new_passphrase_kek(
    passphrase: str, *, n: int, r: int, p: int
) -> tuple[dict[str, Any], PassphraseKeyEncryptionKey]:
    """Draw a fresh salt, derive a passphrase KEK, and return its file params and the KEK."""
    salt = secrets.token_bytes(SCRYPT_SALT_BYTES)
    kek = PassphraseKeyEncryptionKey(derive_key(passphrase, salt, n=n, r=r, p=p))
    params = {"n": int(n), "r": int(r), "p": int(p), "salt": base64.b64encode(salt).decode("ascii")}
    return params, kek


def _passphrase_kek_from_params(
    passphrase: str, params: dict[str, Any], path: Path
) -> PassphraseKeyEncryptionKey:
    """Rebuild a passphrase KEK from a wrapped-key file's recorded scrypt params."""
    try:
        n, r, p = int(params["n"]), int(params["r"]), int(params["p"])
        salt = base64.b64decode(params["salt"], validate=True)
    except (KeyError, ValueError, TypeError) as exc:
        raise ValueError(f"malformed wrapped at-rest key file: {path}") from exc
    return PassphraseKeyEncryptionKey(derive_key(passphrase, salt, n=n, r=r, p=p))


def _write_wrapped_key_document(*, backend: str, params: dict[str, Any], wrapped: bytes) -> bytes:
    """Serialise a wrapped-key file's JSON document (deterministic, newline-terminated)."""
    document = {
        "schema": WRAPPED_KEY_SCHEMA,
        "backend": backend,
        "params": params,
        "wrapped_key": base64.b64encode(wrapped).decode("ascii"),
    }
    return json.dumps(document, ensure_ascii=True, indent=2, sort_keys=True).encode("utf-8") + b"\n"


def _read_wrapped_key_document(path: Path) -> tuple[str, dict[str, Any], bytes]:
    """Read and validate a wrapped-key file, returning ``(backend, params, wrapped_key)``.

    Raises
    ------
    ValueError
        When the file is not a wrapped key file or its top-level fields are malformed.
    """
    raw: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("schema") != WRAPPED_KEY_SCHEMA:
        raise ValueError(f"not a Synapse wrapped at-rest key file: {path}")
    backend = raw.get("backend")
    params = raw.get("params")
    if not isinstance(backend, str) or not isinstance(params, dict):
        raise ValueError(f"malformed wrapped at-rest key file: {path}")
    try:
        wrapped = base64.b64decode(raw["wrapped_key"], validate=True)
    except (KeyError, ValueError, TypeError) as exc:
        raise ValueError(f"malformed wrapped at-rest key file: {path}") from exc
    return backend, params, wrapped


def _load_wrapped_key(path: Path, passphrase: str) -> bytes:
    """Read a passphrase-wrapped key file and return the unwrapped data key.

    Raises
    ------
    ValueError
        When the file is not a passphrase-wrapped key file, its fields are malformed, or the
        passphrase is wrong.
    """
    backend, params, wrapped = _read_wrapped_key_document(path)
    if backend != PASSPHRASE_SCRYPT_BACKEND:
        raise ValueError(f"wrapped key file uses the {backend!r} backend, not a passphrase: {path}")
    return _passphrase_kek_from_params(passphrase, params, path).unwrap(wrapped)


def generate_wrapped_key_file(
    path: str | Path,
    passphrase: str,
    *,
    n: int = DEFAULT_SCRYPT_N,
    r: int = DEFAULT_SCRYPT_R,
    p: int = DEFAULT_SCRYPT_P,
) -> Path:
    """Write a random data key wrapped under a passphrase-derived KEK, owner-only.

    Unlike :func:`generate_key_file_from_passphrase` — which derives the key *itself* from the
    passphrase and discards the salt, so changing the passphrase means re-encrypting every file —
    this writes a **random** data key wrapped by a key-encryption key derived from the passphrase,
    and keeps the salt. The passphrase can then be rotated with :func:`rewrap_wrapped_key_file`
    without touching any encrypted data. This is the envelope-encryption model an HSM-held KEK plugs
    into: only the KEK source changes, the data key and the wrapped-file format stay the same.

    Parameters
    ----------
    path : str or pathlib.Path
        Destination file; refused when it already exists.
    passphrase : str
        Non-empty passphrase whose scrypt-derived key wraps the data key.
    n, r, p : int, optional
        scrypt cost, block-size, and parallelisation parameters (``n`` a power of two).

    Returns
    -------
    pathlib.Path
        The written wrapped-key file path.

    Raises
    ------
    ValueError
        When the passphrase is empty or the scrypt parameters are invalid.
    FileExistsError
        When ``path`` already exists, so an existing key is never overwritten.
    """
    if not passphrase:
        raise ValueError("passphrase must not be empty")
    data_key = secrets.token_bytes(KEY_BYTES)
    params, kek = _new_passphrase_kek(passphrase, n=n, r=r, p=p)
    document = _write_wrapped_key_document(
        backend=PASSPHRASE_SCRYPT_BACKEND, params=params, wrapped=kek.wrap(data_key)
    )
    return _write_new_key_file(Path(path), document)


def rewrap_wrapped_key_file(
    path: str | Path,
    old_passphrase: str,
    new_passphrase: str,
    *,
    n: int = DEFAULT_SCRYPT_N,
    r: int = DEFAULT_SCRYPT_R,
    p: int = DEFAULT_SCRYPT_P,
) -> Path:
    """Re-wrap a wrapped-key file's data key under a new passphrase-derived KEK, in place.

    The underlying data key is unchanged, so no encrypted data is rewritten — only the KEK rotates.
    A fresh salt is drawn and the file is atomically replaced.

    Parameters
    ----------
    path : str or pathlib.Path
        Existing wrapped-key file.
    old_passphrase : str
        The current passphrase, used to unwrap the data key.
    new_passphrase : str
        The non-empty replacement passphrase whose derived key re-wraps the same data key.
    n, r, p : int, optional
        scrypt cost parameters for the new KEK.

    Returns
    -------
    pathlib.Path
        The rewrapped file path.

    Raises
    ------
    ValueError
        When the new passphrase is empty, or the old passphrase is wrong / the file is malformed.
    """
    if not new_passphrase:
        raise ValueError("passphrase must not be empty")
    target = Path(path)
    data_key = _load_wrapped_key(target, old_passphrase)
    params, kek = _new_passphrase_kek(new_passphrase, n=n, r=r, p=p)
    document = _write_wrapped_key_document(
        backend=PASSPHRASE_SCRYPT_BACKEND, params=params, wrapped=kek.wrap(data_key)
    )
    write_owner_only(target, document)
    return target


def _validate_key_stat(info: os.stat_result, target: Path) -> tuple[bool, str]:
    """Validate a key file's stat result for regularity, mode, owner, and size."""
    if not stat.S_ISREG(info.st_mode):
        return False, f"key file is not a regular file: {target}"
    if os.name == "posix" and info.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
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
    On Windows, owner-only is proven via the portable NT DACL floor rather
    than POSIX mode bits (which are not meaningful there).

    Parameters
    ----------
    path : str or pathlib.Path
        Key-file path to check.

    Returns
    -------
    tuple[bool, str]
        ``(True, "ok")`` when the key file is safe, otherwise ``(False, reason)``.
    """
    from synapse_channel.core.secure_path import SecurePathError, assert_owner_only_file_path

    target = Path(path)
    try:
        info = os.lstat(target)
    except FileNotFoundError:
        return False, f"key file does not exist: {target}"
    # Same structural checks on every OS so callers get stable reasons
    # (directory / symlink → "not a regular file") before ACL/mode floors.
    # Prefer ``is_symlink()`` over mode bits alone: Windows lstat can report a
    # reparse point with FILE attributes that still look regular to S_ISREG.
    if target.is_symlink() or not stat.S_ISREG(info.st_mode):
        return False, f"key file is not a regular file: {target}"
    import sys

    if sys.platform.startswith("win"):
        try:
            assert_owner_only_file_path(target, purpose="key file")
        except SecurePathError as exc:
            return False, f"key file must be owner-only (chmod 600): {target} ({exc})"
        if info.st_size != KEY_BYTES:
            return False, f"key file must hold exactly {KEY_BYTES} bytes: {target}"
        return True, "ok"
    return _validate_key_stat(info, target)


def load_key_file(path: str | Path) -> bytes:
    """Load a raw :data:`KEY_BYTES` key after ownership and mode checks.

    Parameters
    ----------
    path : str or pathlib.Path
        Owner-only key-file path (must not be a symlink).

    Returns
    -------
    bytes
        Exactly :data:`KEY_BYTES` key material.

    Raises
    ------
    ValueError
        When the path is missing, is a symlink, fails the permission check, or
        does not hold exactly :data:`KEY_BYTES` bytes.
    """
    target = Path(path)
    from synapse_channel.core.secure_path import SecurePathError, read_owner_only_file_bytes

    try:
        return read_owner_only_file_bytes(
            target,
            purpose="key file",
            expected_size=KEY_BYTES,
        )
    except SecurePathError as exc:
        message = str(exc)
        if "does not exist" in message:
            raise ValueError(f"key file does not exist: {target}") from exc
        if "symlink" in message or "cannot open" in message:
            raise ValueError(f"key file must not be a symlink: {target}") from exc
        if "exactly" in message and "bytes" in message:
            raise ValueError(f"key file must hold exactly {KEY_BYTES} bytes: {target}") from exc
        if "owner-only" in message or "accessible by other" in message or "owned by" in message:
            raise ValueError(f"key file must be owner-only (chmod 600): {target} ({exc})") from exc
        if "not a regular" in message:
            raise ValueError(f"key file is not a regular file: {target}") from exc
        raise ValueError(f"key file must be owner-only (chmod 600): {target} ({exc})") from exc


def write_owner_only(path: Path, payload: bytes) -> None:
    """Atomically write ``payload`` to ``path`` with owner-only permissions."""
    from synapse_channel.core.secure_path import apply_owner_only_file

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
        apply_owner_only_file(path)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
