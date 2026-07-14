# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — messaging CLI send command
"""One-shot chat send command for the ``synapse`` CLI."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
from typing import Any

from synapse_channel.cli_messaging_types import AgentFactory
from synapse_channel.client.agent import SynapseAgent
from synapse_channel.connect_failures import closed_after_ready, describe_connect_failure
from synapse_channel.core.dead_letters import is_directed_target
from synapse_channel.core.payload_crypto import (
    PAYLOAD_PLACEHOLDER,
    PayloadContext,
    PayloadCryptoError,
    encrypt_payload,
    load_payload_key,
    payload_key_fingerprint,
)
from synapse_channel.core.protocol import MessageType
from synapse_channel.terminal_text import terminal_chat_line, terminal_text
from synapse_channel.waiter_identity import waiter_owner


def _one_shot_sender_name(name: str) -> str:
    """Return the sender identity used by one-shot ``send`` connections.

    Parameters
    ----------
    name : str
        Identity supplied through ``synapse send --name``.

    Returns
    -------
    str
        ``name`` unchanged unless it looks like the common waiter identity
        ``<agent>-rx``. In that case the one-shot command sends as ``<agent>`` so
        it does not collide with the persistent wake socket.
    """
    return waiter_owner(name)


async def _send(
    *,
    uri: str,
    name: str,
    target: str,
    message: str,
    wait_seconds: float,
    channel: str = "",
    priority: bool = False,
    require_recipient: bool = False,
    receipt_timeout: float = 2.0,
    encrypt_key_file: str | None = None,
    encrypt_key_id: str = "",
    encrypt_recipients: list[str] | None = None,
    agent_factory: AgentFactory = SynapseAgent,
    token: str | None = None,
    ready_timeout: float = 5.0,
) -> int:
    """Send one chat message and optionally print replies for a window.

    Parameters
    ----------
    uri, name, target, message : str
        Hub URI, sender name, recipient, and message body.
    wait_seconds : float
        Seconds to keep listening for replies after sending (``0`` to skip).
    priority : bool, optional
        Mark the message as priority so it wakes even directed-only waiters.
    require_recipient : bool, optional
        Print the positive hub delivery receipt as well as returning ``1`` for a
        negative one. Directed sends request a receipt by default so a stale-only
        or offline target still fails visibly without this flag.
    receipt_timeout : float, optional
        Seconds to wait for a directed delivery receipt. A receiptless older hub
        remains compatible unless ``require_recipient`` explicitly requires one.
    encrypt_key_file : str or None, optional
        Local 32-byte payload key file used to encrypt ``message`` before send.
    encrypt_key_id : str, optional
        Visible key id carried in the encrypted payload envelope.
    encrypt_recipients : list[str] or None, optional
        Intended recipient identities bound into encrypted payload AAD.
    agent_factory : AgentFactory, optional
        Factory for the client agent; injectable for testing.
    token : str or None, optional
        Shared-secret token for a secured hub.
    ready_timeout : float, optional
        Seconds to wait for the hub connection readiness event.

    Returns
    -------
    int
        ``0`` on success, ``1`` when the hub could not be reached.
    """
    sender_name = _one_shot_sender_name(name)
    replies: list[dict[str, Any]] = []
    receipts: list[dict[str, Any]] = []

    async def collect(data: dict[str, Any]) -> None:
        if data.get("type") == MessageType.CHAT and data.get("sender") != sender_name:
            replies.append(data)
        elif data.get("type") == MessageType.DELIVERY_RECEIPT and data.get("target") == sender_name:
            receipts.append(data)

    agent = agent_factory(sender_name, collect, uri=uri, verbose=False, token=token)
    conn_task = asyncio.create_task(agent.connect())
    try:
        if not await agent.wait_until_ready(timeout=ready_timeout):
            print(
                describe_connect_failure(
                    sender_name,
                    uri,
                    close_code=agent.last_close_code,
                    close_reason=agent.last_close_reason,
                )
            )
            return 1
        # The hub accepts the welcome and only then closes a socket whose name
        # conflicts with a live identity (close code 4009), so a ready connection
        # can already be doomed. Detect that close before sending, otherwise the
        # message is written into a dying socket and silently lost — which reads as
        # "messages between terminals don't arrive".
        if await closed_after_ready(agent):
            print(
                describe_connect_failure(
                    sender_name,
                    uri,
                    close_code=agent.last_close_code,
                    close_reason=agent.last_close_reason,
                )
            )
            return 1
        extra: dict[str, Any] = {}
        if priority:
            extra["priority"] = True
        request_receipt = require_recipient or (not channel and is_directed_target(target))
        if request_receipt:
            extra["receipt_requested"] = True
        if channel:
            extra["channel"] = channel
        outbound_payload = message
        if encrypt_key_file:
            try:
                key = load_payload_key(encrypt_key_file)
                recipients = encrypt_recipients or ([] if target == "all" else [target])
                key_id = encrypt_key_id or f"payload:{payload_key_fingerprint(key)}"
                extra["encrypted"] = encrypt_payload(
                    message,
                    key,
                    key_id=key_id,
                    recipients=recipients,
                    context=PayloadContext(
                        message_type=MessageType.CHAT,
                        sender=sender_name,
                        target=target,
                        channel=channel,
                    ),
                )
                outbound_payload = PAYLOAD_PLACEHOLDER
            except (OSError, PayloadCryptoError, RuntimeError) as exc:
                print(f"encryption failed: {exc}")
                return 1
        await agent.send_message(MessageType.CHAT, target=target, payload=outbound_payload, **extra)
        if request_receipt:
            receipt = await _wait_for_delivery_receipt(receipts, timeout=receipt_timeout)
            if receipt is None:
                if require_recipient:
                    print(f"delivery failed: no receipt from hub for {target}")
                    return 1
            else:
                if require_recipient or not bool(receipt.get("delivered")):
                    print(terminal_text(receipt.get("payload") or "delivery receipt received"))
                if not bool(receipt.get("delivered")):
                    return 1
        if wait_seconds > 0:
            await asyncio.sleep(wait_seconds)
            for reply in replies:
                print(terminal_chat_line(reply.get("sender"), reply.get("payload")))
        return 0
    finally:
        agent.running = False
        conn_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await conn_task


async def _wait_for_delivery_receipt(
    receipts: list[dict[str, Any]], *, timeout: float
) -> dict[str, Any] | None:
    """Return the first collected delivery receipt before ``timeout`` expires."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(float(timeout), 0.0)
    while loop.time() <= deadline:
        if receipts:
            return receipts[-1]
        await asyncio.sleep(0.01)
    return None


def _cmd_send(args: argparse.Namespace) -> int:
    """Dispatch the ``send`` subcommand."""
    return asyncio.run(
        _send(
            uri=args.uri,
            name=args.name,
            target=args.target,
            message=args.message,
            wait_seconds=args.wait_seconds,
            channel=getattr(args, "channel", ""),
            priority=args.priority,
            require_recipient=getattr(args, "require_recipient", False),
            receipt_timeout=getattr(args, "receipt_timeout", 2.0),
            encrypt_key_file=getattr(args, "encrypt_key_file", None),
            encrypt_key_id=getattr(args, "encrypt_key_id", ""),
            encrypt_recipients=getattr(args, "encrypt_recipients", None),
            token=args.token,
            ready_timeout=args.ready_timeout,
        )
    )
