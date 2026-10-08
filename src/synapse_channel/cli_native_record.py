# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — `synapse native-record` command
"""Record a message that travelled over a vendor's own channel.

``synapse native-record`` connects as the recording seat, sends one
``native_message_record`` frame and waits for the hub's verdict. It exits ``0``
only when the hub answered ``native_message_recorded``. A refusal, an old hub,
an unreachable hub and a missing answer all exit ``1``: a record that was not
stored is never reported as stored.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import json
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from websockets.exceptions import ConnectionClosed

from synapse_channel.client.agent import SynapseAgent, default_hub_uri
from synapse_channel.connect_failures import NAME_CONFLICT_CLOSE_CODE, describe_connect_failure
from synapse_channel.core.native_message import (
    MAX_NATIVE_TEXT_BYTES,
    NATIVE_CHANNELS,
    NATIVE_DIRECTIONS,
    NATIVE_OUTCOMES,
    NATIVE_PHASES,
    NativeMessageError,
)
from synapse_channel.core.protocol import MessageType

__all__ = ["add_parsers", "build_record"]

AgentFactory = Callable[..., SynapseAgent]

NAME_BUSY_ATTEMPTS = 5
"""Connections tried while the hub still holds the seat's name from its last record."""

NAME_BUSY_BACKOFF_SECONDS = 0.3
"""Pause between those attempts."""

_VERDICTS = frozenset(
    {MessageType.NATIVE_MESSAGE_RECORDED, MessageType.NATIVE_MESSAGE_REJECTED, MessageType.ERROR}
)
_OPTIONAL_FLAGS = (
    "sender_native_session",
    "recipient_native_session",
    "recipient_address",
    "execution_host",
    "native_message_id",
    "native_call_id",
    "source_msg_seq",
)


def build_record(args: argparse.Namespace) -> tuple[dict[str, Any], str]:
    """Build the record fields from parsed command-line arguments.

    Parameters
    ----------
    args : argparse.Namespace
        Arguments of ``synapse native-record``.

    Returns
    -------
    tuple[dict[str, Any], str]
        The record and a note for the operator. The note is non-empty when the
        text is above the inline bound and was therefore left out of the record.

    Raises
    ------
    ValueError
        When the text file is not valid UTF-8 or ``--tool-result-json`` is not
        valid JSON.
    """
    if args.text_file is not None:
        encoded = Path(args.text_file).read_bytes()
        text = encoded.decode("utf-8")
    else:
        text = str(args.text)
        encoded = text.encode("utf-8", errors="surrogatepass")
    sent = args.direction == "sent"
    record: dict[str, Any] = {
        "channel": args.channel,
        "direction": args.direction,
        "phase": args.phase,
        "outcome": args.outcome,
        "sender_seat": args.seat if sent else args.peer_seat,
        "recipient_seat": args.peer_seat if sent else args.seat,
        "sent_at": args.sent_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "text_sha256": hashlib.sha256(encoded).hexdigest(),
        "text_bytes": len(encoded),
        "text": text,
    }
    for name in _OPTIONAL_FLAGS:
        record[name] = getattr(args, name)
    if args.tool_result_json is not None:
        record["tool_result"] = json.loads(args.tool_result_json)
    note = ""
    if args.hash_only or len(encoded) > MAX_NATIVE_TEXT_BYTES:
        record["text"] = None
        if not args.hash_only:
            note = f"text is above {MAX_NATIVE_TEXT_BYTES} bytes; recorded by hash only"
    return record, note


_NO_ANSWER = "the hub did not answer the record; it is not confirmed as stored"


def _closed(name: str, uri: str, code: int | None, reason: str) -> str:
    """Describe a connection the hub closed before it gave a verdict."""
    failure = describe_connect_failure(name, uri, close_code=code, close_reason=reason)
    return f"{failure} The record is not confirmed as stored."


async def _exchange(
    *,
    uri: str,
    name: str,
    token: str | None,
    record: dict[str, Any],
    idem_key: str | None,
    agent_factory: AgentFactory,
    ready_timeout: float,
    reply_timeout: float,
) -> tuple[dict[str, Any] | None, str, int | None]:
    """Send one record as ``name``; return the verdict, or the failure and close code."""
    verdicts: list[dict[str, Any]] = []

    async def collect(data: dict[str, Any]) -> None:
        if data.get("type") in _VERDICTS:
            verdicts.append(data)

    agent = agent_factory(name, collect, uri=uri, verbose=False, token=token)
    connection_task = asyncio.create_task(agent.connect())
    try:
        if not await agent.wait_until_ready(timeout=ready_timeout):
            code = agent.last_close_code
            return None, _closed(name, uri, code, agent.last_close_reason), code
        try:
            # A hub that closes the socket here is reported below, by its close code.
            with contextlib.suppress(ConnectionClosed):
                await agent.record_native_message(record, idem_key=idem_key)
        except NativeMessageError as exc:
            return None, f"record refused before sending: {exc}", None
        except ValueError as exc:
            return None, f"record path unavailable: {exc}", None
        deadline = asyncio.get_running_loop().time() + reply_timeout
        while (
            not verdicts
            and agent.last_close_code is None
            and asyncio.get_running_loop().time() < deadline
        ):
            await asyncio.sleep(0.02)
        if verdicts:
            return verdicts[0], "", None
        if agent.last_close_code is not None:
            code = agent.last_close_code
            return None, _closed(name, uri, code, agent.last_close_reason), code
        return None, _NO_ANSWER, None
    finally:
        agent.running = False
        # Finish the close handshake first: the hub lets one connection own a name,
        # so the next record of the same seat must not meet a half-closed socket.
        connection = agent.connection
        if connection is not None:
            with contextlib.suppress(Exception):
                await connection.close()
        if not connection_task.done():
            connection_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await connection_task


def _cmd_native_record(
    args: argparse.Namespace, *, agent_factory: AgentFactory = SynapseAgent
) -> int:
    """Record one native message and report the hub's verdict."""
    try:
        record, note = build_record(args)
    except (OSError, ValueError) as exc:
        print(f"record refused before sending: {exc}")
        return 1
    attempts = 0
    while True:
        verdict, failure, close_code = asyncio.run(
            _exchange(
                uri=args.uri,
                name=args.seat,
                token=args.token,
                record=record,
                idem_key=args.idem_key,
                agent_factory=agent_factory,
                ready_timeout=args.ready_timeout,
                reply_timeout=args.reply_timeout,
            )
        )
        # The hub lets one connection own a name. Right after the seat's previous
        # record it may still hold the name; the retry key makes a repeat safe.
        attempts += 1
        if close_code != NAME_CONFLICT_CLOSE_CODE or attempts >= NAME_BUSY_ATTEMPTS:
            break
        time.sleep(NAME_BUSY_BACKOFF_SECONDS)
    if verdict is None:
        print(failure)
        return 1
    if args.json:
        print(json.dumps(verdict, ensure_ascii=False, sort_keys=True))
    if verdict.get("type") != MessageType.NATIVE_MESSAGE_RECORDED:
        if not args.json:
            code = verdict.get("error_code") or "refused"
            print(f"record refused by the hub ({code}): {verdict.get('payload')}")
        return 1
    if not args.json:
        print(
            f"recorded native message: seq {verdict.get('audit_seq')}, "
            f"sha256 {verdict.get('text_sha256')}, binding {verdict.get('recorder_binding')}"
        )
        if note:
            print(note)
    return 0


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``native-record`` command."""
    parser = subparsers.add_parser(
        "native-record",
        help="Record a message that travelled over a vendor's own channel (no delivery).",
    )
    parser.add_argument("--channel", required=True, choices=sorted(NATIVE_CHANNELS))
    parser.add_argument("--direction", required=True, choices=sorted(NATIVE_DIRECTIONS))
    parser.add_argument("--phase", required=True, choices=sorted(NATIVE_PHASES))
    parser.add_argument("--outcome", default=None, choices=sorted(NATIVE_OUTCOMES))
    parser.add_argument(
        "--seat",
        required=True,
        help="The recording seat: the sender of a sent, the recipient of a received message. "
        "The command connects under this name.",
    )
    parser.add_argument("--peer-seat", default=None, help="The seat on the other side, if known.")
    parser.add_argument("--sender-native-session", default=None)
    parser.add_argument("--recipient-native-session", default=None)
    parser.add_argument("--recipient-address", default=None, help="Raw native address.")
    parser.add_argument("--execution-host", default=None)
    parser.add_argument("--native-message-id", default=None)
    parser.add_argument("--native-call-id", default=None)
    parser.add_argument("--source-msg-seq", type=int, default=None)
    parser.add_argument("--sent-at", default=None, help="UTC time; defaults to now.")
    text = parser.add_mutually_exclusive_group(required=True)
    text.add_argument("--text", default=None, help="The exact message text.")
    text.add_argument("--text-file", default=None, help="UTF-8 file holding the exact text.")
    parser.add_argument(
        "--hash-only", action="store_true", help="Record digest and size without the text."
    )
    parser.add_argument("--tool-result-json", default=None, help="What the channel reported.")
    parser.add_argument("--idem-key", default=None, help="Retry key; derived when omitted.")
    parser.add_argument("--uri", default=default_hub_uri())
    parser.add_argument("--token", default=None, help="Shared-secret token for a secured hub.")
    parser.add_argument("--ready-timeout", type=float, default=5.0)
    parser.add_argument("--reply-timeout", type=float, default=10.0)
    parser.add_argument("--json", action="store_true", help="Print the hub's reply as JSON.")
    parser.set_defaults(func=_cmd_native_record)
