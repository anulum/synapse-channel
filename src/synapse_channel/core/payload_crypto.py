# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — end-to-end payload encryption envelopes
"""Client-side encrypted payload envelopes for selected channel messages.

The hub routes these envelopes as ordinary message metadata: it sees sender,
target, channel id, key id, recipient names, nonce, ciphertext, and a base64
copy of the authenticated associated data, but it never receives plaintext in
the ``payload`` field. Endpoints load a local 32-byte payload key and decrypt
only after verifying that the visible route metadata matches the envelope AAD.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import stat
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict, cast

from synapse_channel.core.at_rest import KEY_BYTES, NONCE_BYTES, require_aes_gcm
from synapse_channel.core.errors import SynapseError

PAYLOAD_ENVELOPE_VERSION = 1
"""Version of the encrypted payload envelope."""

PAYLOAD_PLACEHOLDER = "<encrypted payload>"
"""Plain envelope payload used when the body is encrypted."""


class PayloadCryptoError(SynapseError, ValueError):
    """Raised when an encrypted payload envelope cannot be used safely."""

    code = "payload_crypto"


@dataclass(frozen=True)
class PayloadContext:
    """Visible routing metadata bound into encrypted payload AAD.

    Parameters
    ----------
    message_type : str
        Hub message type carrying the envelope, such as ``"chat"``.
    sender : str
        Sender identity visible on the hub envelope.
    target : str, optional
        Visible target selector. Defaults to ``"all"``.
    channel : str, optional
        Private-channel id, when the message is channel-scoped.
    task_id : str, optional
        Task id associated with the encrypted payload, when applicable.
    """

    message_type: str
    sender: str
    target: str = "all"
    channel: str = ""
    task_id: str = ""


class PayloadEnvelope(TypedDict):
    """JSON-serialisable encrypted payload envelope."""

    version: int
    key_id: str
    recipients: list[str]
    ciphertext: str
    nonce: str
    aad: str


def load_payload_key(path: str | Path) -> bytes:
    """Read a 32-byte payload key after regular-file permission checks.

    Parameters
    ----------
    path : str or pathlib.Path
        Key-file path holding exactly 32 raw bytes. The file must be owned by
        the current user, owner-only, regular, and not a symlink.

    Returns
    -------
    bytes
        The raw 32-byte payload key.

    Raises
    ------
    PayloadCryptoError
        When the key file is absent, unsafe, or the wrong length.
    """
    target = Path(path)
    # Symlink refusal is portable: O_NOFOLLOW is POSIX-only, so check the leaf
    # path first on every platform (Windows open would otherwise follow).
    if target.is_symlink():
        raise PayloadCryptoError(f"payload key file must not be a symlink: {target}")
    try:
        fd = os.open(target, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError as exc:
        raise PayloadCryptoError(f"payload key file does not exist: {target}") from exc
    except OSError as exc:
        raise PayloadCryptoError(f"payload key file must not be a symlink: {target}") from exc
    try:
        if os.name == "nt":
            # Windows has no meaningful st_mode owner-only bits; prove the NT
            # DACL floor before reading key material (same floor as at-rest keys).
            from synapse_channel.core.secure_path import (
                SecurePathError,
                assert_owner_only_file_path,
            )

            try:
                assert_owner_only_file_path(target, purpose="payload key file")
            except SecurePathError as exc:
                raise PayloadCryptoError(
                    f"payload key file must be owner-only (chmod 600): {target} ({exc})"
                ) from exc
            key = target.read_bytes()
        else:
            info = os.fstat(fd)
            _validate_key_stat(info, target)
            key = os.read(fd, KEY_BYTES)
    finally:
        os.close(fd)
    if len(key) != KEY_BYTES:
        raise PayloadCryptoError(f"payload key file must hold exactly {KEY_BYTES} bytes: {target}")
    return key


def payload_key_fingerprint(key: bytes) -> str:
    """Return a short display fingerprint for a payload key.

    Parameters
    ----------
    key : bytes
        Raw 32-byte payload key.

    Returns
    -------
    str
        The first 16 hexadecimal characters of the key's SHA-256 digest.
    """
    _require_key(key)
    return hashlib.sha256(key).hexdigest()[:16]


def encrypt_payload(
    plaintext: str,
    key: bytes,
    *,
    key_id: str,
    recipients: Sequence[str],
    context: PayloadContext,
) -> PayloadEnvelope:
    """Encrypt one text payload into a route-bound JSON envelope.

    Parameters
    ----------
    plaintext : str
        UTF-8 text to encrypt.
    key : bytes
        Raw 32-byte AES-256-GCM key.
    key_id : str
        Operator-chosen key identifier recorded as visible metadata.
    recipients : collections.abc.Sequence[str]
        Intended recipient identities. The sorted unique list is bound into AAD.
    context : PayloadContext
        Visible route metadata that must match again at decryption time.

    Returns
    -------
    PayloadEnvelope
        JSON-serialisable encrypted envelope.
    """
    _require_key(key)
    key_name = str(key_id or "").strip()
    if not key_name:
        raise PayloadCryptoError("payload key id is required")
    normalised_recipients = _normalise_recipients(recipients)
    aad = _aad_bytes(context, normalised_recipients)
    nonce = secrets.token_bytes(NONCE_BYTES)
    cipher = require_aes_gcm()(key)
    ciphertext = cipher.encrypt(nonce, plaintext.encode("utf-8"), aad)
    return {
        "version": PAYLOAD_ENVELOPE_VERSION,
        "key_id": key_name,
        "recipients": normalised_recipients,
        "ciphertext": _b64encode(ciphertext),
        "nonce": _b64encode(nonce),
        "aad": _b64encode(aad),
    }


def decrypt_payload(envelope: Mapping[str, object], key: bytes, *, context: PayloadContext) -> str:
    """Decrypt one route-bound encrypted payload envelope.

    Parameters
    ----------
    envelope : collections.abc.Mapping[str, object]
        Envelope generated by :func:`encrypt_payload`.
    key : bytes
        Raw 32-byte AES-256-GCM key.
    context : PayloadContext
        Visible route metadata from the received hub envelope.

    Returns
    -------
    str
        Decrypted UTF-8 payload text.

    Raises
    ------
    PayloadCryptoError
        When the envelope is malformed, the route metadata differs, or
        authentication/decryption fails.
    """
    _require_key(key)
    parsed = _parse_envelope(envelope)
    recipients = parsed["recipients"]
    expected_aad = _aad_bytes(context, recipients)
    envelope_aad = _b64decode(parsed["aad"], field="aad")
    if not secrets.compare_digest(envelope_aad, expected_aad):
        raise PayloadCryptoError("routing metadata does not match encrypted payload aad")
    try:
        plaintext = require_aes_gcm()(key).decrypt(
            _b64decode(parsed["nonce"], field="nonce"),
            _b64decode(parsed["ciphertext"], field="ciphertext"),
            envelope_aad,
        )
    except _invalid_tag_type() as exc:
        raise PayloadCryptoError("encrypted payload authentication failed") from exc
    try:
        return plaintext.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PayloadCryptoError("encrypted payload is not valid UTF-8") from exc


def _require_key(key: bytes) -> None:
    """Raise when ``key`` is not exactly one AES-256-GCM key."""
    if len(key) != KEY_BYTES:
        raise PayloadCryptoError(f"payload key must be {KEY_BYTES} bytes, got {len(key)}")


def _validate_key_stat(info: os.stat_result, target: Path) -> None:
    """Validate key-file stat data."""
    if not stat.S_ISREG(info.st_mode):
        raise PayloadCryptoError(f"payload key file is not a regular file: {target}")
    if info.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise PayloadCryptoError(f"payload key file must be owner-only (chmod 600): {target}")
    if hasattr(os, "geteuid") and info.st_uid != os.geteuid():
        raise PayloadCryptoError(f"payload key file must be owned by the current user: {target}")
    if info.st_size != KEY_BYTES:
        raise PayloadCryptoError(f"payload key file must hold exactly {KEY_BYTES} bytes: {target}")


def _normalise_recipients(recipients: Sequence[str]) -> list[str]:
    """Return sorted unique non-empty recipient names."""
    return sorted({str(recipient).strip() for recipient in recipients if str(recipient).strip()})


def _aad_bytes(context: PayloadContext, recipients: Sequence[str]) -> bytes:
    """Return canonical JSON AAD bytes for route metadata and recipients."""
    payload = {
        "channel": context.channel,
        "message_type": context.message_type,
        "recipients": list(recipients),
        "sender": context.sender,
        "target": context.target,
        "task_id": context.task_id,
        "version": PAYLOAD_ENVELOPE_VERSION,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _parse_envelope(envelope: Mapping[str, object]) -> PayloadEnvelope:
    """Validate and return a typed encrypted payload envelope."""
    if envelope.get("version") != PAYLOAD_ENVELOPE_VERSION:
        raise PayloadCryptoError("unsupported encrypted payload version")
    required = ("key_id", "ciphertext", "nonce", "aad")
    for field in required:
        if not isinstance(envelope.get(field), str) or not str(envelope.get(field)):
            raise PayloadCryptoError(f"encrypted payload field '{field}' is required")
    raw_recipients = envelope.get("recipients")
    if not isinstance(raw_recipients, list) or not all(
        isinstance(recipient, str) for recipient in raw_recipients
    ):
        raise PayloadCryptoError("encrypted payload recipients must be a list of strings")
    return cast(PayloadEnvelope, dict(envelope))


def _b64encode(payload: bytes) -> str:
    """Return URL-safe base64 text for ``payload``."""
    return base64.urlsafe_b64encode(payload).decode("ascii")


def _b64decode(value: str, *, field: str) -> bytes:
    """Decode URL-safe base64 text from one envelope field."""
    try:
        return base64.urlsafe_b64decode(value.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise PayloadCryptoError(f"encrypted payload field '{field}' is not valid base64") from exc


def _invalid_tag_type() -> type[Exception]:
    """Return the optional cryptography InvalidTag class."""
    try:
        from cryptography.exceptions import InvalidTag
    except ImportError:  # pragma: no cover - cryptography absence is covered via require_aes_gcm.
        return ValueError
    return InvalidTag
