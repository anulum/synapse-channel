# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — versioned compact relay-envelope codec
"""Encode and decode the versioned compact relay envelope.

The relay log is a token-thrifty observation surface, but it must not discard
protocol evidence. Version 2 keeps the fixed envelope fields under short keys,
retains structured JSON payloads, and stores every non-core field under one
compact extension mapping. The decoder also reads historical version-1 rows,
which carried string payloads and no extensions. Both versions intentionally
normalise timestamps to millisecond precision.
"""

from __future__ import annotations

import time
from typing import Any

from synapse_channel.core.numeric_coercion import safe_float, safe_int

LITE_VERSION = 2
"""Schema version stamped into every newly encoded lite envelope.

Version 2 preserves JSON payload types and carries every non-core envelope field
inside the compact ``x`` extension mapping. :func:`decode_lite` remains able to
read version-1 rows, whose payload was stringified and whose extensions were not
recorded.
"""

LITE_KEYS: dict[str, str] = {
    "msg_id": "i",
    "type": "ty",
    "sender": "s",
    "target": "to",
    "payload": "p",
    "timestamp": "t",
    "hub_id": "h",
    "channel": "c",
}
"""Heavy envelope field → compact relay key.

The single source of truth both halves of the codec read for the fixed envelope
fields. Non-core fields are carried under the private compact extension key so a
new protocol field cannot silently disappear from the relay log.
"""

_LITE_EXTENSIONS_KEY = "x"
_LEGACY_LITE_VERSION = 1

__all__ = ("LITE_KEYS", "LITE_VERSION", "decode_lite", "encode_lite")


def encode_lite(message: dict[str, Any]) -> dict[str, Any]:
    """Pack a full Synapse message into a short-key relay envelope.

    Parameters
    ----------
    message : dict[str, Any]
        A full message envelope as produced by
        :func:`synapse_channel.core.protocol.build_envelope`.

    Returns
    -------
    dict[str, Any]
        A mapping with compact keys: ``v`` (version), ``i`` (message id), ``ty``
        (type), ``s`` (sender), ``to`` (target), ``p`` (payload), ``t``
        (millisecond timestamp), ``h`` (hub id), ``c`` (channel), and optional
        ``x`` (all non-core fields). JSON payload types are retained. Malformed
        ``timestamp`` and ``msg_id`` fields fall back to the current time and
        zero, respectively.
    """
    ts_val = safe_float(message.get("timestamp"), default=None, allow_bool=False)
    ts_ms = safe_int(
        ts_val * 1000.0 if ts_val is not None else None,
        default=None,
        allow_bool=False,
    )
    if ts_ms is None:
        ts_ms = int(time.time() * 1000.0)

    msg_id_val = safe_int(message.get("msg_id"), default=0, allow_bool=False)
    lite: dict[str, Any] = {
        "v": LITE_VERSION,
        "i": msg_id_val,
        "ty": str(message.get("type", "chat")),
        "s": str(message.get("sender", "?")),
        "to": str(message.get("target", "all")),
        "p": message.get("payload", ""),
        "t": ts_ms,
        "h": str(message.get("hub_id", "")),
        "c": "",
    }
    channel = str(message.get("channel") or "").strip()
    if channel:
        lite["c"] = channel
    extensions = {key: value for key, value in message.items() if key not in LITE_KEYS}
    if extensions:
        lite[_LITE_EXTENSIONS_KEY] = extensions
    return lite


def decode_lite(lite: dict[str, Any]) -> dict[str, Any]:
    """Reconstruct a full message envelope from a compact relay event.

    Version-2 rows retain structured payloads and non-core fields. Legacy
    version-1 rows keep their historical string-payload semantics and have no
    extension data to reconstruct. Because both versions store timestamps as
    millisecond integers, the reconstructed ``timestamp`` is precise only to
    the millisecond. Missing or malformed short keys fall back to the same core
    defaults :func:`encode_lite` emits.

    Parameters
    ----------
    lite : dict[str, Any]
        A short-key envelope as produced by :func:`encode_lite` or a historical
        version-1 encoder.

    Returns
    -------
    dict[str, Any]
        A full envelope with the core fields and, for a version-2 row, every
        non-core field encoded under ``x``.
    """
    ts_ms = safe_int(lite.get("t"), default=0, allow_bool=False)
    msg_id_val = safe_int(lite.get("i"), default=0, allow_bool=False)

    raw_version = lite.get("v", _LEGACY_LITE_VERSION)
    version = raw_version if type(raw_version) is int else _LEGACY_LITE_VERSION
    payload = lite.get("p", "")
    if version == _LEGACY_LITE_VERSION:
        payload = str(payload)

    message: dict[str, Any] = {}
    raw_extensions = lite.get(_LITE_EXTENSIONS_KEY)
    if version == LITE_VERSION and isinstance(raw_extensions, dict):
        message.update(
            {
                key: value
                for key, value in raw_extensions.items()
                if isinstance(key, str) and key not in LITE_KEYS
            }
        )
    message.update(
        {
            "sender": str(lite.get("s", "?")),
            "target": str(lite.get("to", "all")),
            "type": str(lite.get("ty", "chat")),
            "payload": payload,
            "timestamp": ts_ms / 1000.0,
            "msg_id": msg_id_val,
            "hub_id": str(lite.get("h", "")),
        }
    )
    channel = str(lite.get("c") or "").strip()
    if channel:
        message["channel"] = channel
    return message
