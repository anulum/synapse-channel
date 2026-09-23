# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — pinned pi RPC record framing and turn settlement
"""Read pi 0.87.1 RPC as bounded LF-delimited JSON records.

The protocol treats U+2028 and U+2029 as ordinary string characters. A command
response acknowledges receipt only; ``turn_end`` closes one assistant/tool
cycle and ``agent_settled`` closes the full run after retries and follow-ups.
This module never executes a tool or authorises a workspace mutation.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from typing import Any, Final, cast

from synapse_channel.core.errors import SynapseError

PI_RPC_VERSION: Final = "0.87.1"
MAX_RECORD_BYTES: Final = 1_048_576
MAX_PENDING_BYTES: Final = 2_097_152


class PiRpcError(SynapseError, ValueError):
    """The pinned host emitted an invalid or incomplete RPC record."""

    code = "pi_rpc"


class PiRpcDecoder:
    """Incrementally decode strict LF-delimited UTF-8 JSON objects."""

    def __init__(self) -> None:
        self._pending = bytearray()

    def feed(self, chunk: bytes) -> tuple[dict[str, Any], ...]:
        """Return all complete records while retaining only a bounded tail."""
        if not isinstance(chunk, bytes):
            raise PiRpcError("pi RPC chunks must be bytes")
        if len(self._pending) + len(chunk) > MAX_PENDING_BYTES:
            raise PiRpcError("pi RPC pending output exceeded its byte limit")
        self._pending.extend(chunk)
        records: list[dict[str, Any]] = []
        while True:
            newline = self._pending.find(b"\n")
            if newline < 0:
                break
            if newline > MAX_RECORD_BYTES:
                raise PiRpcError("pi RPC record exceeded its byte limit")
            raw = bytes(self._pending[:newline])
            del self._pending[: newline + 1]
            if raw.endswith(b"\r"):
                raw = raw[:-1]
            try:
                record = json.loads(raw.decode("utf-8", errors="strict"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise PiRpcError("pi RPC record is not UTF-8 JSON") from exc
            if not isinstance(record, dict) or not isinstance(record.get("type"), str):
                raise PiRpcError("pi RPC record must have a string type")
            records.append(record)
        if len(self._pending) > MAX_RECORD_BYTES:
            raise PiRpcError("pi RPC record exceeded its byte limit")
        return tuple(records)

    def finish(self) -> None:
        """Refuse an EOF that truncates a record rather than accepting it."""
        if self._pending:
            raise PiRpcError("pi RPC stream ended inside a record")


def response_for(record: Mapping[str, Any], request_id: str, command: str) -> bool | None:
    """Return matching command acceptance, or ``None`` for another event."""
    if record.get("type") != "response":
        return None
    if record.get("id") != request_id or record.get("command") != command:
        raise PiRpcError("pi RPC response correlation failed")
    accepted = record.get("success")
    if type(accepted) is not bool:
        raise PiRpcError("pi RPC response lacks a boolean success value")
    return accepted


def assistant_text(record: Mapping[str, Any]) -> tuple[str, bool] | None:
    """Extract one completed assistant message and its error status."""
    if record.get("type") != "message_end":
        return None
    message = record.get("message")
    if not isinstance(message, dict) or message.get("role") != "assistant":
        return None
    content = message.get("content")
    if not isinstance(content, list):
        raise PiRpcError("pi assistant message lacks content blocks")
    texts: list[str] = []
    for block in content:
        if not isinstance(block, dict) or not isinstance(block.get("type"), str):
            raise PiRpcError("pi assistant content block is malformed")
        if block["type"] == "text":
            value = block.get("text")
            if not isinstance(value, str):
                raise PiRpcError("pi assistant text block is malformed")
            texts.append(value)
    stop_reason = message.get("stopReason")
    if not isinstance(stop_reason, str):
        raise PiRpcError("pi assistant message lacks a stop reason")
    return "\n".join(texts), stop_reason in {"error", "aborted"}


def assistant_metrics(record: Mapping[str, Any]) -> tuple[int, int, float, str] | None:
    """Read final provider usage without treating interim stream values as final."""
    if record.get("type") != "message_end":
        return None
    message = record.get("message")
    if not isinstance(message, dict) or message.get("role") != "assistant":
        return None
    usage = message.get("usage")
    if not isinstance(usage, dict):
        raise PiRpcError("pi assistant message lacks final usage")
    input_tokens = usage.get("input")
    output_tokens = usage.get("output")
    cost = usage.get("cost")
    total = cost.get("total") if isinstance(cost, dict) else None
    if any(type(value) is not int or value < 0 for value in (input_tokens, output_tokens)):
        raise PiRpcError("pi assistant usage has invalid token counts")
    if (
        not isinstance(total, (int, float))
        or isinstance(total, bool)
        or not math.isfinite(total)
        or total < 0
    ):
        raise PiRpcError("pi assistant usage has invalid cost")
    stop_reason = message.get("stopReason")
    if not isinstance(stop_reason, str):
        raise PiRpcError("pi assistant message lacks a stop reason")
    return cast(int, input_tokens), cast(int, output_tokens), float(total), stop_reason
