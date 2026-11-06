# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — Ed25519 signatures that bind a frame to one connection
"""Sign and verify a frame with the identity key that proved its connection.

A frame signature lives in its own ``frame_signature`` field, apart from the
registration and event ``signature`` and the HMAC ``auth`` field, so each can
be required on its own or together. The envelope names the hub, the
connection's session nonce and a sequence number that must grow on every
frame of that connection. A frame copied to another connection, another hub
or the same connection later is therefore refused without any clock.

The signed bytes are a fixed domain prefix followed by the canonical JSON of
the whole frame as sent, with only the signature value left out. Any change
to any field, including the message type, breaks the signature.

Choosing the key, checking that it is still trusted, and remembering the last
sequence of a connection belong to the caller.
"""

from __future__ import annotations

import base64
import binascii
import copy
import json
from collections.abc import Mapping
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
        Ed25519PublicKey,
    )

FRAME_SIGNATURE_FIELD = "frame_signature"
"""Frame field that carries the signature envelope."""

FRAME_SIGNATURE_VERSION = 1
"""Envelope version written and accepted."""

FRAME_SIGNATURE_DOMAIN = b"synapse-channel:frame-signature:v1\x00"
"""Prefix of the signed bytes; separates frame signatures from every other signature."""

_ENVELOPE_KEYS = frozenset(
    {"version", "algorithm", "key_id", "hub_id", "session_nonce", "seq", "value"}
)


class FrameSignatureResult(str, Enum):
    """Stable outcomes of a frame-signature check."""

    VALID = "valid"
    MISSING = "frame_signature_required"
    MALFORMED = "frame_signature_malformed"
    WRONG_KEY = "frame_signature_wrong_key"
    WRONG_HUB = "frame_signature_wrong_hub"
    WRONG_SESSION = "frame_signature_wrong_session"
    REPLAYED = "frame_signature_replayed"
    BAD_SIGNATURE = "frame_signature_invalid"


def frame_signing_bytes(frame: Mapping[str, Any]) -> bytes:
    """Return the bytes a frame signature covers.

    Parameters
    ----------
    frame : Mapping[str, Any]
        The frame, with or without a complete ``frame_signature`` envelope.

    Returns
    -------
    bytes
        :data:`FRAME_SIGNATURE_DOMAIN` followed by the canonical JSON of the
        frame without ``frame_signature.value``.
    """
    unsigned = dict(frame)
    envelope = unsigned.get(FRAME_SIGNATURE_FIELD)
    if isinstance(envelope, Mapping):
        unsigned[FRAME_SIGNATURE_FIELD] = {k: v for k, v in envelope.items() if k != "value"}
    canonical = json.dumps(unsigned, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return FRAME_SIGNATURE_DOMAIN + canonical.encode("utf-8")


def sign_session_frame(
    frame: Mapping[str, Any],
    *,
    key_id: str,
    private_key: Ed25519PrivateKey,
    hub_id: str,
    session_nonce: str,
    seq: int,
) -> dict[str, Any]:
    """Return a copy of ``frame`` signed for one connection.

    Parameters
    ----------
    frame : Mapping[str, Any]
        The frame to send; it must not already carry ``frame_signature``.
    key_id : str
        Identifier of the identity key that proved the connection.
    private_key : Ed25519PrivateKey
        That key.
    hub_id, session_nonce : str
        The hub and session named in the connection's welcome.
    seq : int
        Sequence of this frame on the connection; at least one and greater
        than every earlier signed frame on it.

    Returns
    -------
    dict[str, Any]
        The signed frame.
    """
    signed = copy.deepcopy(dict(frame))
    signed[FRAME_SIGNATURE_FIELD] = {
        "version": FRAME_SIGNATURE_VERSION,
        "algorithm": "ed25519",
        "key_id": key_id,
        "hub_id": hub_id,
        "session_nonce": session_nonce,
        "seq": seq,
    }
    value = private_key.sign(frame_signing_bytes(signed))
    signed[FRAME_SIGNATURE_FIELD]["value"] = base64.b64encode(value).decode("ascii")
    return signed


def _envelope(frame: Mapping[str, Any]) -> Mapping[str, Any] | FrameSignatureResult:
    """Return a well-formed envelope, or why the frame has none."""
    envelope = frame.get(FRAME_SIGNATURE_FIELD)
    if envelope is None:
        return FrameSignatureResult.MISSING
    if (
        not isinstance(envelope, Mapping)
        or set(envelope) != _ENVELOPE_KEYS
        or envelope["version"] != FRAME_SIGNATURE_VERSION
        or envelope["algorithm"] != "ed25519"
        or not all(
            isinstance(envelope[name], str) and envelope[name]
            for name in ("key_id", "hub_id", "session_nonce", "value")
        )
        or type(envelope["seq"]) is not int
        or envelope["seq"] < 1
    ):
        return FrameSignatureResult.MALFORMED
    return envelope


def verify_session_frame(
    frame: Mapping[str, Any],
    *,
    public_key: Ed25519PublicKey,
    key_id: str,
    hub_id: str,
    session_nonce: str,
    last_seq: int,
) -> tuple[FrameSignatureResult, int]:
    """Check a received frame against the connection it arrived on.

    Parameters
    ----------
    frame : Mapping[str, Any]
        The frame exactly as received, before any field is changed.
    public_key : Ed25519PublicKey
        The current public key for ``key_id``.
    key_id : str
        The key that proved the connection.
    hub_id, session_nonce : str
        This hub and the connection's session nonce.
    last_seq : int
        Highest sequence already accepted on the connection.

    Returns
    -------
    tuple[FrameSignatureResult, int]
        The outcome and, when valid, the frame's sequence (else ``last_seq``).
    """
    from cryptography.exceptions import InvalidSignature

    envelope = _envelope(frame)
    if isinstance(envelope, FrameSignatureResult):
        return envelope, last_seq
    if envelope["key_id"] != key_id:
        return FrameSignatureResult.WRONG_KEY, last_seq
    if envelope["hub_id"] != hub_id:
        return FrameSignatureResult.WRONG_HUB, last_seq
    if envelope["session_nonce"] != session_nonce:
        return FrameSignatureResult.WRONG_SESSION, last_seq
    seq = int(envelope["seq"])
    if seq <= last_seq:
        return FrameSignatureResult.REPLAYED, last_seq
    try:
        value = base64.b64decode(str(envelope["value"]), validate=True)
        public_key.verify(value, frame_signing_bytes(frame))
    except (binascii.Error, InvalidSignature):
        return FrameSignatureResult.BAD_SIGNATURE, last_seq
    return FrameSignatureResult.VALID, seq
