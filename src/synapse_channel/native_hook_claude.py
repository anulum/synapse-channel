# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — turn a session-send hook call into a native-message record
"""Map one ``PostToolUse`` hook call of a session-send tool to a record.

A coding-agent client can run a command after each tool call and hand it the
call as JSON. For the tool that sends a message to another session, that JSON
holds the sender's native session identifier, the address, the exact text and
what the channel answered. This module turns it into the record the hub
stores, or says why there is nothing to record. It reads no file and opens no
connection.

The record is always the sender's own ``outcome`` record: the hook runs after
the send, so the outcome is known. ``queued`` is written only when the channel
reported success; a reported failure is ``refused``; anything else is
``uncertain``. A message the hook cannot attribute to a seat is still mapped,
with the seat left empty, so that the caller can keep it until the session is
registered.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from synapse_channel.core.native_message import MAX_NATIVE_TEXT_BYTES, MAX_TOOL_RESULT_BYTES
from synapse_channel.native_seat_register import Registration, seat_of_name, seat_of_session

SEND_TOOL = "SendMessage"
"""Name of the tool whose calls are recorded."""

CHANNEL = "claude_cross_session"
"""Channel name of the records this module builds."""

_REF_SUFFIX = re.compile(r"^(?P<name>.*\S)\s+\[(?P<ref>[0-9A-Za-z]+)\]$")
_MAX_ADDRESS_CHARS = 512
_MAX_NAME_CHARS = 256
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True, slots=True)
class HookRecord:
    """What one hook call gives: a record, or the reason there is none."""

    record: dict[str, Any] | None
    skip_reason: str
    call_id: str
    native_session: str


def recipient_name(address: str) -> str:
    """Return the session name of a send address, without a trailing ``[ref]``."""
    stripped = address.strip()
    match = _REF_SUFFIX.match(stripped)
    return match.group("name") if match else stripped


def _name(value: object, limit: int) -> str | None:
    """Return a bounded single-line value, or None when it is not usable."""
    if not isinstance(value, str):
        return None
    text = _CONTROL.sub(" ", value).strip()
    return text[:limit] if text else None


def _outcome(response: object) -> str:
    if isinstance(response, dict):
        if response.get("success") is True:
            return "queued"
        if response.get("success") is False:
            return "refused"
    return "uncertain"


def _bounded_result(response: object) -> object:
    """Return the channel's answer, or a marked prefix when it is too large."""
    try:
        serialised = json.dumps(response, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        return {"unserialisable": True}
    if len(serialised.encode("utf-8", errors="surrogatepass")) <= MAX_TOOL_RESULT_BYTES:
        return response
    return {"truncated_json_prefix": serialised[: MAX_TOOL_RESULT_BYTES // 4]}


def _send_call(hook_input: object) -> tuple[str, str, str, str, object, str]:
    """Return call id, session, text, address, the channel's answer and a skip reason.

    The skip reason is empty when the input is a session-send call with a text.
    """
    if not isinstance(hook_input, dict):
        return "", "", "", "", None, "hook input is not an object"
    call_id = _name(hook_input.get("tool_use_id"), _MAX_NAME_CHARS) or ""
    session = _name(hook_input.get("session_id"), _MAX_NAME_CHARS) or ""
    response = hook_input.get("tool_response")
    if hook_input.get("tool_name") != SEND_TOOL:
        return call_id, session, "", "", response, "not a session-send tool call"
    tool_input = hook_input.get("tool_input")
    call = tool_input if isinstance(tool_input, dict) else {}
    text = call.get("message")
    if not isinstance(text, str) or not text:
        return call_id, session, "", "", response, "the call carries no message text"
    to = call.get("to")
    return call_id, session, text, to if isinstance(to, str) else "", response, ""


def record_from_hook(
    hook_input: object,
    registrations: tuple[Registration, ...],
    *,
    captured_at: datetime,
) -> HookRecord:
    """Return the sender's record of one hook call.

    Parameters
    ----------
    hook_input : object
        The decoded hook JSON.
    registrations : tuple of Registration
        The local seat register.
    captured_at : datetime
        When the hook received the call; becomes ``sent_at``.

    Returns
    -------
    HookRecord
        ``record`` is None with a ``skip_reason`` when the call is not a send
        with a text. Otherwise the record; its ``sender_seat`` is None when
        the sending session is not registered.
    """
    call_id, session, text, address, response, skip = _send_call(hook_input)
    if skip:
        return HookRecord(None, skip, call_id, session)
    encoded = text.encode("utf-8", errors="surrogatepass")
    sender = seat_of_session(registrations, session) if session else None
    recipient = seat_of_name(registrations, recipient_name(address)) if address else None
    message_id = response.get("msg_id") if isinstance(response, dict) else None
    moment = captured_at.astimezone(timezone.utc)
    record: dict[str, Any] = {
        "channel": CHANNEL,
        "direction": "sent",
        "phase": "outcome",
        "outcome": _outcome(response),
        "sender_seat": sender.seat if sender else None,
        "recipient_seat": recipient.seat if recipient else None,
        "sender_native_session": session or None,
        "recipient_native_session": recipient.native_session if recipient else None,
        "recipient_address": _name(address, _MAX_ADDRESS_CHARS),
        "execution_host": None,
        "native_message_id": _name(message_id, _MAX_NAME_CHARS),
        "native_call_id": call_id or None,
        "source_msg_seq": None,
        "sent_at": moment.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "text_sha256": hashlib.sha256(encoded).hexdigest(),
        "text_bytes": len(encoded),
        "text": text if len(encoded) <= MAX_NATIVE_TEXT_BYTES else None,
        "tool_result": _bounded_result(response),
    }
    return HookRecord(record, "", call_id, session)
