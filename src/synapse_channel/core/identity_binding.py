# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — connection-identity binding: prove a socket is the identity it claims
"""Connection-identity binding — prove a socket is the identity it registers as.

A shared connect token authenticates that a socket knows the secret; it does not
prove *which* identity the socket is, so any token holder can register under any
sender name (and so squat a ``-rx`` mailbox sidecar or a role). This module resolves
the connection credential to an audit subject: the registering socket signs its first
frame with an Ed25519 identity key, and the hub verifies that signature against an
operator-managed trust bundle before it binds the name — step 1 of the enforcement
path in :doc:`../../docs/identity-and-acl`.

Key material is deliberately **separate** from the federation Ed25519 keys and the
signed-event trust bundle: an identity credential proves *who a connection is*, not
*that a durable record is authentic*, so mixing them would let one grant stand in for
the other. The signature envelope and its verification reuse the signed-event
primitives (:func:`~synapse_channel.core.message_auth.verify_event_signature`), which
already bind a key id to its permitted senders, carry a replay nonce and freshness
window, and honour key expiry and revocation — so credential rotation, revocation, and
freshness come for free through that layer rather than a parallel one.

Enforcement is opt-in on the hub (``--require-identity-binding``): with it off — the
default open/loopback posture — registration is unchanged, so a single-user dev hub
keeps working with no keys at all.
"""

from __future__ import annotations

import base64
import json
import os
import tempfile
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from synapse_channel.core.errors import SynapseError
from synapse_channel.core.message_auth import (
    DEFAULT_MESSAGE_AUTH_FUTURE_SKEW_SECONDS,
    DEFAULT_MESSAGE_AUTH_WINDOW_SECONDS,
    EventSignatureKey,
    EventSignatureTrustBundle,
    MessageReplayCache,
    SignedEventVerificationResult,
    verify_event_signature,
)

_ED25519_RAW_PUBLIC_KEY_BYTES = 32
DEFAULT_IDENTITY_REPLAY_CAPACITY = 4096
"""Bounded nonce records the identity trust bundle retains for replay detection."""


class IdentityBindingError(SynapseError, ValueError):
    """Raised when an identity trust bundle file is malformed."""

    code = "identity_binding"


def _string_set(value: object, key_id: str, field: str) -> frozenset[str]:
    """Return a frame field as a frozenset of non-empty strings, or raise on a non-list."""
    if value is None:
        return frozenset()
    if not isinstance(value, list):
        raise IdentityBindingError(f"identity key {key_id!r} field {field!r} must be a list")
    return frozenset(str(item).strip() for item in value if str(item).strip())


def _optional_expiry(value: object, key_id: str) -> float | None:
    """Return a key-level expiry as a finite float, or ``None`` when unset.

    A blank or ``null`` value means no expiry; anything else must be a finite number
    (never a bool), so a malformed expiry is an error rather than a silently ignored
    or infinite one.
    """
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise IdentityBindingError(f"identity key {key_id!r} expires_at must be a number")
    expiry = float(value)
    if expiry != expiry or expiry in (float("inf"), float("-inf")):
        raise IdentityBindingError(f"identity key {key_id!r} expires_at must be finite")
    return expiry


def _parse_key(entry: object, index: int) -> EventSignatureKey:
    """Parse one identity trust entry into an :class:`EventSignatureKey`."""
    if not isinstance(entry, Mapping):
        raise IdentityBindingError(f"identity key {index} must be an object")
    key_id = str(entry.get("key_id", "")).strip()
    if not key_id:
        raise IdentityBindingError(f"identity key {index} needs a non-empty key_id")
    try:
        public_key = base64.b64decode(str(entry.get("public_key", "")).strip(), validate=True)
    except (ValueError, TypeError) as exc:
        raise IdentityBindingError(
            f"identity key {key_id!r} has an invalid base64 public_key"
        ) from exc
    if len(public_key) != _ED25519_RAW_PUBLIC_KEY_BYTES:
        raise IdentityBindingError(
            f"identity key {key_id!r} public_key must be {_ED25519_RAW_PUBLIC_KEY_BYTES} "
            "raw Ed25519 bytes"
        )
    senders = _string_set(entry.get("senders"), key_id, "senders")
    if not senders:
        raise IdentityBindingError(
            f"identity key {key_id!r} needs at least one sender it may prove"
        )
    raw_revoked = entry.get("revoked", False)
    if not isinstance(raw_revoked, bool):
        raise IdentityBindingError(f"identity key {key_id!r} revoked must be a boolean")
    return EventSignatureKey(
        key_id=key_id,
        public_key=public_key,
        senders=senders,
        projects=_string_set(entry.get("projects"), key_id, "projects"),
        expires_at=_optional_expiry(entry.get("expires_at"), key_id),
        revoked=raw_revoked,
    )


def load_identity_trust_bundle(
    path: str | Path,
    *,
    window_seconds: float = DEFAULT_MESSAGE_AUTH_WINDOW_SECONDS,
    future_skew_seconds: float = DEFAULT_MESSAGE_AUTH_FUTURE_SKEW_SECONDS,
    replay_capacity: int = DEFAULT_IDENTITY_REPLAY_CAPACITY,
) -> EventSignatureTrustBundle:
    """Load an identity trust bundle from a JSON key file.

    Parameters
    ----------
    path : str or pathlib.Path
        JSON file holding ``{"keys": [{"key_id", "public_key" (base64 raw Ed25519),
        "senders": [audit-subject, ...], "projects"?: [...], "expires_at"?: float,
        "revoked"?: bool}, ...]}``. ``~`` is expanded.
    window_seconds, future_skew_seconds : float, optional
        Freshness window and permitted future skew for a registration signature.
    replay_capacity : int, optional
        Bounded number of accepted nonces the bundle's replay cache retains.

    Returns
    -------
    EventSignatureTrustBundle
        The trust bundle, with its own replay cache (separate key material from
        federation and signed-event trust).

    Raises
    ------
    IdentityBindingError
        When the file is missing, not JSON, not the expected shape, or a key entry
        is malformed (bad base64, wrong key length, no senders, duplicate key id).
    """
    file = Path(path).expanduser()
    try:
        raw = file.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise IdentityBindingError(f"identity trust bundle does not exist: {file}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise IdentityBindingError(f"invalid identity trust JSON: {exc}") from exc
    if not isinstance(data, Mapping) or not isinstance(data.get("keys"), list):
        raise IdentityBindingError("identity trust bundle must be a mapping with a 'keys' list")
    keys: dict[str, EventSignatureKey] = {}
    for index, entry in enumerate(data["keys"]):
        key = _parse_key(entry, index)
        if key.key_id in keys:
            raise IdentityBindingError(f"duplicate key id {key.key_id!r} in identity trust bundle")
        keys[key.key_id] = key
    return EventSignatureTrustBundle(
        keys=keys,
        replay_cache=MessageReplayCache(
            window_seconds=window_seconds,
            max_entries=replay_capacity,
            future_skew_seconds=future_skew_seconds,
        ),
    )


def verify_registration(
    frame: Mapping[str, object],
    *,
    trust_bundle: EventSignatureTrustBundle,
    now: float,
    required_sender: str,
) -> SignedEventVerificationResult:
    """Verify a registration frame's identity signature against the trust bundle.

    A thin wrapper over
    :func:`~synapse_channel.core.message_auth.verify_event_signature` that binds only
    the sender: the ``<project>/<agent_id>`` sender already encodes the project, so
    the identity is proven when the signature verifies against a key whose permitted
    senders include the resolved sender. Returns the stable
    :class:`~synapse_channel.core.message_auth.SignedEventVerificationResult`
    (``VALID`` or the refusal reason — missing signature, unknown or revoked key,
    sender mismatch, expiry, replay).
    """
    return verify_event_signature(
        frame,
        trust_bundle=trust_bundle,
        now=now,
        required_sender=required_sender,
    )


def _load_raw_keys(file: Path) -> list[Any]:
    """Return the raw ``keys`` list of an existing bundle file, or ``[]`` when absent."""
    if not file.is_file():
        return []
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise IdentityBindingError(f"invalid identity trust JSON: {exc}") from exc
    if not isinstance(data, Mapping) or not isinstance(data.get("keys"), list):
        raise IdentityBindingError("identity trust bundle must be a mapping with a 'keys' list")
    return list(data["keys"])


def enroll_identity_key(
    path: str | Path,
    *,
    key_id: str,
    public_key_b64: str,
    senders: Iterable[str],
    expires_at: float | None = None,
) -> None:
    """Add one public key to an identity trust bundle file, creating it if absent.

    The new entry is appended to the bundle's ``keys`` list, then the whole file is
    validated (every key re-parsed) before it is written atomically, so a bad entry
    never lands and a torn write is never observed. A key id already present is an
    error — rotation replaces the file rather than silently shadowing a key.

    Raises
    ------
    IdentityBindingError
        When the file is malformed, the key id is already enrolled, or the new entry
        is invalid (bad base64, wrong key length, no senders).
    """
    file = Path(path).expanduser()
    keys = _load_raw_keys(file)
    if any(isinstance(k, Mapping) and str(k.get("key_id", "")).strip() == key_id for k in keys):
        raise IdentityBindingError(f"key id {key_id!r} already enrolled in {file}")
    entry: dict[str, Any] = {
        "key_id": key_id,
        "public_key": public_key_b64,
        "senders": list(senders),
    }
    if expires_at is not None:
        entry["expires_at"] = expires_at
    keys.append(entry)
    for index, candidate in enumerate(keys):
        _parse_key(candidate, index)
    _write_bundle(file, {"keys": keys})


def _write_bundle(file: Path, payload: Mapping[str, Any]) -> None:
    """Write a trust bundle atomically (temp file then :func:`os.replace`)."""
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    try:
        file.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=file.parent, prefix=f"{file.name}.", suffix=".tmp")
    except OSError as exc:
        raise IdentityBindingError(f"cannot write identity trust bundle {file}: {exc}") from exc
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, file)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
