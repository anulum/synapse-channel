# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — run one session-send hook call: spool first, then the hub
"""Record one session-send hook call without ever disturbing the send.

The order is fixed: decode the hook input, write the record to the spool and
flush it to disk, and only then contact the hub. Whatever happens, the caller
gets no exception: a hook that failed would be shown to the agent as a failed
tool call although the message was sent.

A sender whose native session is not registered as a seat cannot write its own
record, because the hub accepts a record only from the seat on its own side.
The entry then waits in the spool; a later flush completes it once the session
is registered, and gives up after the spool's attempt limit.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from synapse_channel.native_hook_claude import recipient_name, record_from_hook
from synapse_channel.native_seat_register import load, seat_of_name, seat_of_session
from synapse_channel.native_spool import (
    DEFAULT_MAX_ATTEMPTS,
    FlushReport,
    SendResult,
    flush,
    set_aside,
    spool,
)

RecordSender = Callable[[dict[str, Any]], SendResult]
"""Puts one complete record on the hub as its ``sender_seat``."""

NOT_A_SEND = "not a session-send tool call"


def completing_sender(register_dir: Path, send: RecordSender) -> RecordSender:
    """Return a sender that fills in seats from the register before sending.

    A record spooled before its session was registered has no ``sender_seat``.
    The register is read again at each attempt; without a seat the attempt
    fails in transport terms, so the entry stays pending.
    """

    def attempt(record: dict[str, Any]) -> SendResult:
        registrations = load(register_dir)
        completed = dict(record)
        session = completed.get("sender_native_session")
        if not completed.get("sender_seat") and isinstance(session, str):
            sender = seat_of_session(registrations, session)
            completed["sender_seat"] = sender.seat if sender else None
        address = completed.get("recipient_address")
        if not completed.get("recipient_seat") and isinstance(address, str):
            recipient = seat_of_name(registrations, recipient_name(address))
            if recipient is not None:
                completed["recipient_seat"] = recipient.seat
                completed["recipient_native_session"] = recipient.native_session
        if not completed.get("sender_seat"):
            return SendResult(
                False,
                False,
                "the sending session is not registered as a seat",
                hub_asked=False,
            )
        result = send(completed)
        return SendResult(
            result.recorded,
            result.final,
            result.detail,
            result.audit_seq,
            completed,
            result.hub_asked,
        )

    return attempt


def run_hook(
    raw: bytes,
    *,
    spool_dir: Path,
    register_dir: Path,
    send: RecordSender,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> FlushReport | None:
    """Spool the record of one hook call, then flush the spool to the hub.

    Parameters
    ----------
    raw : bytes
        The hook input as read from standard input.
    spool_dir, register_dir : pathlib.Path
        The write-ahead spool and the local seat register.
    send : callable
        Puts one complete record on the hub.
    max_attempts : int
        Attempt limit of the spool.
    now : callable
        Source of the current UTC time.

    Returns
    -------
    FlushReport or None
        The flush counts, or None when nothing could be spooled or flushed.
        No exception leaves this function.
    """
    try:
        moment = now()
        try:
            hook_input = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            set_aside(spool_dir, "unparsed", "hook-input", raw, moment)
            return None
        mapped = record_from_hook(hook_input, load(register_dir), captured_at=moment)
        if mapped.record is None:
            if mapped.skip_reason != NOT_A_SEND:
                set_aside(spool_dir, "skipped", mapped.call_id or "call", raw, moment)
        else:
            spool(
                spool_dir,
                native_session=mapped.native_session,
                call_id=mapped.call_id,
                record=mapped.record,
                now=moment,
            )
        return flush(
            spool_dir,
            completing_sender(register_dir, send),
            max_attempts=max_attempts,
            now=now,
        )
    except Exception:  # noqa: BLE001 - a hook must never fail the send it follows
        return None
