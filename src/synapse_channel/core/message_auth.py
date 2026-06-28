# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — per-message authentication primitives
"""Per-message HMAC authentication for selected Synapse frames."""

from __future__ import annotations

import copy
import hmac
import json
import time
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
from typing import Any

from synapse_channel.core.protocol import RESOURCE_TYPE_ALIASES, MessageType

AUTH_ALGORITHM = "hmac-sha256"
"""Authentication algorithm value carried in signed frame metadata."""

DEFAULT_MESSAGE_AUTH_WINDOW_SECONDS = 10.0
"""Default signed-frame past timestamp window in seconds."""

DEFAULT_MESSAGE_AUTH_FUTURE_SKEW_SECONDS = 1.0
"""Default signed-frame future clock-skew allowance in seconds."""

DEFAULT_SIGNED_MESSAGE_TYPES = (
    frozenset(
        {
            MessageType.CLAIM,
            MessageType.RELEASE,
            MessageType.TASK_UPDATE,
            MessageType.HANDOFF,
            MessageType.CHECKPOINT,
        }
    )
    | RESOURCE_TYPE_ALIASES
)
"""Inbound frame types covered by the first per-message-authentication tranche."""


class VerificationResult(str, Enum):
    """Stable per-message authentication verification result strings.

    Subclasses ``(str, Enum)`` rather than ``enum.StrEnum`` so the module imports
    on Python 3.10, where ``StrEnum`` does not exist; members remain ``str`` and
    their ``.value`` is the stable wire string.
    """

    OK = "ok"
    MISSING = "missing"
    EXPIRED = "expired"
    UNKNOWN_KEY = "unknown_key"
    REVOKED_KEY = "revoked_key"
    BAD_AUTHENTICATION = "bad_authentication"
    SENDER_MISMATCH = "sender_mismatch"
    SEQUENCE_MISMATCH = "sequence_mismatch"
    REPLAYED = "replayed"


@dataclass(frozen=True)
class MessageAuthKey:
    """One HMAC key used to sign and verify Synapse frames.

    Parameters
    ----------
    key_id : str
        Public key identifier carried as ``auth.kid``.
    secret : bytes
        Shared secret used as the HMAC-SHA256 key.
    senders : frozenset[str], optional
        Sender names allowed to use this key. An empty set rejects every sender.
    revoked : bool, optional
        When ``True``, frames naming this key id fail with ``revoked_key``.
    """

    key_id: str
    secret: bytes
    senders: frozenset[str] = frozenset()
    revoked: bool = False


class MessageReplayCache:
    """Bounded in-memory replay cache for authenticated frame nonces.

    Parameters
    ----------
    window_seconds : float
        Timestamp age retained for replay detection.
    max_entries : int
        Maximum nonce/sequence records retained after timestamp eviction.

    Notes
    -----
    This cache is deliberately in-memory. A hub restart clears it; the timestamp
    window still rejects stale signed frames after restart, while the durable
    idempotency journal remains responsible for replaying already-applied
    mutating command responses.
    """

    def __init__(
        self,
        *,
        window_seconds: float,
        max_entries: int,
        future_skew_seconds: float = DEFAULT_MESSAGE_AUTH_FUTURE_SKEW_SECONDS,
    ) -> None:
        self.window_seconds = max(float(window_seconds), 0.001)
        self.future_skew_seconds = max(float(future_skew_seconds), 0.0)
        self.max_entries = max(int(max_entries), 1)
        self._entries: OrderedDict[tuple[str, str, str], tuple[str, str, str, float]] = (
            OrderedDict()
        )

    def remember(
        self,
        key_id: str,
        sender: str,
        nonce: str,
        sequence: int,
        *,
        timestamp: float,
        now: float,
    ) -> bool:
        """Record a nonce and return whether it was new.

        Parameters
        ----------
        key_id : str
            Key id from the authenticated frame.
        sender : str
            Hub-resolved sender name.
        nonce : str
            Client-generated nonce.
        sequence : int
            Client sequence number for the frame. The value is signed metadata,
            not replay-cache identity.
        timestamp : float
            Authentication timestamp carried in the frame.
        now : float
            Current wall-clock time used for eviction.

        Returns
        -------
        bool
            ``True`` when the nonce was admitted, ``False`` when the nonce
            already exists or the live replay window is at capacity.
        """
        self._evict(now)
        cache_key = (key_id, sender, nonce)
        if cache_key in self._entries:
            return False
        if len(self._entries) >= self.max_entries:
            return False
        self._entries[cache_key] = (key_id, sender, nonce, float(timestamp))
        self._entries.move_to_end(cache_key)
        return True

    def _evict(self, now: float) -> None:
        """Drop entries outside the timestamp window."""
        cutoff = float(now) - self.window_seconds
        expired = [
            cache_key
            for cache_key, (_, _, _, timestamp) in self._entries.items()
            if timestamp < cutoff
        ]
        for cache_key in expired:
            self._entries.pop(cache_key, None)


def _without_auth_value(frame: Mapping[str, Any]) -> dict[str, Any]:
    """Return a deep copy of ``frame`` without ``auth.value``."""
    copied = copy.deepcopy(dict(frame))
    auth = copied.get("auth")
    if isinstance(auth, dict):
        auth.pop("value", None)
    return copied


def canonical_frame(frame: Mapping[str, Any]) -> bytes:
    """Return the canonical JSON bytes covered by per-message authentication.

    Parameters
    ----------
    frame : Mapping[str, Any]
        Frame to canonicalise. ``auth.value`` is removed; other ``auth`` metadata
        remains signed.

    Returns
    -------
    bytes
        UTF-8 JSON with sorted keys and compact separators.
    """
    return json.dumps(
        _without_auth_value(frame),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def sign_frame(
    frame: Mapping[str, Any],
    *,
    key: MessageAuthKey,
    nonce: str,
    sequence: int,
    timestamp: float | None = None,
) -> dict[str, Any]:
    """Return ``frame`` with an ``auth`` HMAC-SHA256 signature attached.

    Parameters
    ----------
    frame : Mapping[str, Any]
        JSON-serialisable Synapse frame.
    key : MessageAuthKey
        HMAC key used to sign the canonical frame.
    nonce : str
        Client-generated nonce.
    sequence : int
        Positive per-client sequence number.
    timestamp : float or None, optional
        Wall-clock timestamp. ``None`` uses :func:`time.time`.

    Returns
    -------
    dict[str, Any]
        Signed frame containing ``auth.alg``, ``auth.kid``, ``auth.nonce``,
        ``auth.sequence``, ``auth.timestamp``, and ``auth.value``.
    """
    signed = copy.deepcopy(dict(frame))
    signed["auth"] = {
        "alg": AUTH_ALGORITHM,
        "kid": key.key_id,
        "nonce": str(nonce),
        "sequence": int(sequence),
        "timestamp": time.time() if timestamp is None else float(timestamp),
    }
    signed["auth"]["value"] = hmac.new(key.secret, canonical_frame(signed), sha256).hexdigest()
    return signed


def verify_frame(
    frame: Mapping[str, Any],
    *,
    keys: Mapping[str, MessageAuthKey],
    replay_cache: MessageReplayCache,
    now: float,
    required_sender: str,
) -> VerificationResult:
    """Verify one authenticated Synapse frame.

    Parameters
    ----------
    frame : Mapping[str, Any]
        Decoded Synapse frame.
    keys : Mapping[str, MessageAuthKey]
        Known HMAC keys by key id.
    replay_cache : MessageReplayCache
        Bounded cache that records accepted nonces and sequences.
    now : float
        Current wall-clock time.
    required_sender : str
        Hub-resolved sender name expected to match ``frame.sender`` and any key
        sender binding.

    Returns
    -------
    VerificationResult
        Stable result describing success or the refusal reason.
    """
    auth = frame.get("auth")
    if not isinstance(auth, Mapping):
        return VerificationResult.MISSING
    key_id = str(auth.get("kid") or "")
    key = keys.get(key_id)
    if key is None:
        return VerificationResult.UNKNOWN_KEY
    if key.revoked:
        return VerificationResult.REVOKED_KEY
    sender = str(frame.get("sender") or "")
    if sender != required_sender or not key.senders or required_sender not in key.senders:
        return VerificationResult.SENDER_MISMATCH
    if str(auth.get("alg") or "") != AUTH_ALGORITHM:
        return VerificationResult.BAD_AUTHENTICATION
    try:
        timestamp = float(auth["timestamp"])
        sequence_raw = auth["sequence"]
    except (KeyError, TypeError, ValueError):
        return VerificationResult.BAD_AUTHENTICATION
    if isinstance(sequence_raw, bool) or not isinstance(sequence_raw, int) or sequence_raw < 1:
        return VerificationResult.SEQUENCE_MISMATCH
    now_float = float(now)
    if (
        timestamp < now_float - replay_cache.window_seconds
        or timestamp > now_float + replay_cache.future_skew_seconds
    ):
        return VerificationResult.EXPIRED
    nonce = str(auth.get("nonce") or "")
    supplied = str(auth.get("value") or "")
    if not nonce or not supplied:
        return VerificationResult.BAD_AUTHENTICATION
    expected = hmac.new(key.secret, canonical_frame(frame), sha256).hexdigest()
    if not hmac.compare_digest(expected, supplied):
        return VerificationResult.BAD_AUTHENTICATION
    if not replay_cache.remember(
        key_id,
        required_sender,
        nonce,
        sequence_raw,
        timestamp=timestamp,
        now=now_float,
    ):
        return VerificationResult.REPLAYED
    return VerificationResult.OK
