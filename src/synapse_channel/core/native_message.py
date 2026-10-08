# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — bounded wire schema for records of native vendor messages
"""Validate the record of a message that travelled over a vendor's own channel.

Some agent clients can reach another session without the hub: a Claude Code
session messages another Claude Code session, a Codex client queues a message
into a running thread. The hub does not carry such a message, so it cannot
store it. A ``native_message_record`` frame lets the side that sent or received
it leave a durable record. The record enters no mailbox, produces no delivery
receipt and wakes nobody.

This module holds the closed vocabularies, the field bounds, the parser and the
two pure rules a handler applies: which seat a record must be written by, and
which idempotency key a recorder derives for it. It imports nothing from the
hub.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Final

from synapse_channel.core.errors import SynapseError

__all__ = [
    "MAX_NATIVE_TEXT_BYTES",
    "NATIVE_CHANNELS",
    "NATIVE_DIRECTIONS",
    "NATIVE_OUTCOMES",
    "NATIVE_PHASES",
    "NativeMessageError",
    "native_idempotency_key",
    "own_side_seat",
    "parse_native_message_record",
]

NATIVE_CHANNELS: Final = frozenset({"claude_cross_session", "codex_queue", "acp", "server"})
"""Vendor channels a record may name."""

NATIVE_DIRECTIONS: Final = frozenset({"sent", "received"})
"""Side of the message the recorder was on."""

NATIVE_PHASES: Final = frozenset({"attempt", "outcome"})
"""``attempt`` is written before a native send, ``outcome`` after it.

A channel whose send takes no caller idempotency key cannot be retried safely.
Its recorder writes an attempt first, so that a send whose result was lost
stays visible as an attempt without an outcome.
"""

NATIVE_OUTCOMES: Final = frozenset({"queued", "refused", "uncertain"})
"""What the native channel reported; required with ``phase: outcome`` only."""

MAX_NATIVE_TEXT_BYTES: Final = 65_536
"""Largest message text stored inline; a longer message is recorded by hash."""

MAX_NATIVE_MESSAGE_BYTES: Final = 16_777_216
"""Largest ``text_bytes`` a hash-only record may state."""

MAX_TOOL_RESULT_BYTES: Final = 4_096
"""Largest serialised ``tool_result`` accepted."""

_MAX_NAME_CHARS: Final = 256
_MAX_ADDRESS_CHARS: Final = 512
_SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")
_UTC_RE: Final = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z$")
_CONTROL_RE: Final = re.compile(r"[\x00-\x1f\x7f]")
_OPTIONAL_NAMES: Final = (
    ("sender_seat", _MAX_NAME_CHARS),
    ("recipient_seat", _MAX_NAME_CHARS),
    ("sender_native_session", _MAX_NAME_CHARS),
    ("recipient_native_session", _MAX_NAME_CHARS),
    ("recipient_address", _MAX_ADDRESS_CHARS),
    ("execution_host", _MAX_NAME_CHARS),
    ("native_message_id", _MAX_NAME_CHARS),
    ("native_call_id", _MAX_NAME_CHARS),
)


class NativeMessageError(SynapseError, ValueError):
    """A native-message record is malformed or outside its closed vocabulary.

    Attributes
    ----------
    reason : str
        Stable refusal code returned to the recorder in ``error_code``.
    """

    code = "native_message"

    def __init__(self, message: str, *, reason: str = "native_record_invalid") -> None:
        super().__init__(message)
        self.reason = reason


def _vocabulary(data: dict[str, Any], field: str, allowed: frozenset[str]) -> str:
    value = data.get(field)
    if not isinstance(value, str) or value not in allowed:
        raise NativeMessageError(f"native message {field} is not recognised")
    return value


def _optional_name(data: dict[str, Any], field: str, limit: int) -> str | None:
    value = data.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > limit:
        raise NativeMessageError(f"native message {field} must be 1 to {limit} characters")
    if _CONTROL_RE.search(value) is not None:
        raise NativeMessageError(f"native message {field} holds a control character")
    return value


def _text_fields(data: dict[str, Any]) -> tuple[str, int, str | None]:
    digest = data.get("text_sha256")
    size = data.get("text_bytes")
    text = data.get("text")
    if not isinstance(digest, str) or _SHA256_RE.fullmatch(digest) is None:
        raise NativeMessageError("native message text_sha256 must be lowercase SHA-256")
    if isinstance(size, bool) or not isinstance(size, int):
        raise NativeMessageError("native message text_bytes must be an integer")
    if size < 1 or size > MAX_NATIVE_MESSAGE_BYTES:
        raise NativeMessageError("native message text_bytes is outside the supported bound")
    if text is None:
        return digest, size, None
    if not isinstance(text, str):
        raise NativeMessageError("native message text must be a string")
    encoded = text.encode("utf-8", errors="surrogatepass")
    if len(encoded) > MAX_NATIVE_TEXT_BYTES:
        raise NativeMessageError(
            "native message text is above the inline bound; record it by hash",
            reason="native_record_text_too_large",
        )
    if len(encoded) != size or hashlib.sha256(encoded).hexdigest() != digest:
        raise NativeMessageError(
            "native message text does not match text_sha256 and text_bytes",
            reason="native_record_text_mismatch",
        )
    return digest, size, text


def _tool_result(data: dict[str, Any]) -> object:
    value = data.get("tool_result")
    if value is None:
        return None
    try:
        serialised = json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise NativeMessageError("native message tool_result must be strict JSON") from exc
    if len(serialised.encode("utf-8", errors="surrogatepass")) > MAX_TOOL_RESULT_BYTES:
        raise NativeMessageError("native message tool_result is above the supported bound")
    return value


def parse_native_message_record(data: dict[str, Any]) -> dict[str, Any]:
    """Return the bounded client fields of one native-message record.

    Parameters
    ----------
    data : dict[str, Any]
        Decoded ``native_message_record`` frame.

    Returns
    -------
    dict[str, Any]
        The record without transport fields. Optional fields that were absent
        are present with value ``None``, so every stored record has one shape.

    Raises
    ------
    NativeMessageError
        When a field is missing, mistyped, outside its vocabulary or bound, or
        when an inline ``text`` does not hash to ``text_sha256``.
    """
    channel = _vocabulary(data, "channel", NATIVE_CHANNELS)
    direction = _vocabulary(data, "direction", NATIVE_DIRECTIONS)
    phase = _vocabulary(data, "phase", NATIVE_PHASES)
    outcome = data.get("outcome")
    if phase == "outcome":
        outcome = _vocabulary(data, "outcome", NATIVE_OUTCOMES)
    elif outcome is not None:
        raise NativeMessageError("native message outcome is allowed with phase outcome only")
    names = {field: _optional_name(data, field, limit) for field, limit in _OPTIONAL_NAMES}
    sent_at = data.get("sent_at")
    if not isinstance(sent_at, str) or _UTC_RE.fullmatch(sent_at) is None:
        raise NativeMessageError("native message sent_at must be UTC as YYYY-MM-DDTHH:MM:SSZ")
    source_msg_seq = data.get("source_msg_seq")
    if source_msg_seq is not None and (
        isinstance(source_msg_seq, bool)
        or not isinstance(source_msg_seq, int)
        or source_msg_seq < 1
    ):
        raise NativeMessageError("native message source_msg_seq must be a positive integer")
    digest, size, text = _text_fields(data)
    return {
        "channel": channel,
        "direction": direction,
        "phase": phase,
        "outcome": outcome,
        **names,
        "source_msg_seq": source_msg_seq,
        "sent_at": sent_at,
        "text_sha256": digest,
        "text_bytes": size,
        "text": text,
        "tool_result": _tool_result(data),
    }


def own_side_seat(record: dict[str, Any]) -> str | None:
    """Return the seat that alone may write ``record``.

    Parameters
    ----------
    record : dict[str, Any]
        A record returned by :func:`parse_native_message_record`.

    Returns
    -------
    str or None
        ``sender_seat`` of a ``sent`` record and ``recipient_seat`` of a
        ``received`` one; ``None`` when that seat was not stated, which a
        handler refuses.
    """
    field = "sender_seat" if record["direction"] == "sent" else "recipient_seat"
    seat = record.get(field)
    return seat if isinstance(seat, str) else None


def native_idempotency_key(record: dict[str, Any]) -> str:
    """Derive the retry key of one record from what identifies the message.

    The key names the channel, the side and the phase together with the
    identifier the native channel gave the message. Without such an identifier
    it falls back to the sending session, the caller's own call identifier,
    the send time and the text digest. The same record therefore always gets
    the same key, and the two phases of one message get different keys.

    Parameters
    ----------
    record : dict[str, Any]
        A record returned by :func:`parse_native_message_record`.

    Returns
    -------
    str
        ``nm-`` followed by a lowercase SHA-256 digest.
    """
    if record.get("native_message_id") and record["phase"] == "outcome":
        identity = ["id", str(record["native_message_id"])]
    else:
        identity = [
            "call",
            str(record.get("sender_native_session") or ""),
            str(record.get("native_call_id") or ""),
            str(record["sent_at"]),
            str(record["text_sha256"]),
        ]
    basis = "\x00".join([record["channel"], record["direction"], record["phase"], *identity])
    return "nm-" + hashlib.sha256(basis.encode("utf-8", errors="surrogatepass")).hexdigest()
