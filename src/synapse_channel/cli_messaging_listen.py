# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — messaging CLI listen command
"""Streaming listen command for the ``synapse`` CLI."""

from __future__ import annotations

import argparse
import asyncio
from typing import Any

from synapse_channel.cli_messaging_types import AgentFactory, AsyncRunner, ListenRunner
from synapse_channel.client.agent import SynapseAgent
from synapse_channel.connect_failures import describe_connect_failure
from synapse_channel.core.payload_crypto import (
    PayloadContext,
    PayloadCryptoError,
    decrypt_payload,
    load_payload_key,
)
from synapse_channel.core.protocol import MessageType, is_recipient
from synapse_channel.terminal_text import terminal_chat_line, terminal_text


async def _listen(
    *,
    uri: str,
    name: str,
    agent_factory: AgentFactory = SynapseAgent,
    token: str | None = None,
    for_name: str | None = None,
    ready_timeout: float = 5.0,
    max_messages: int | None = None,
    decrypt_key_file: str | None = None,
) -> int:
    """Stream chat and presence updates to stdout until the connection ends.

    Parameters
    ----------
    uri, name : str
        Hub URI and the listener's display name.
    agent_factory : AgentFactory, optional
        Factory for the client agent; injectable for testing.
    for_name : str or None, optional
        When set, show only chats addressed to that name (or broadcast) and
        suppress presence updates — a focused per-agent inbox view.
    ready_timeout : float, optional
        Seconds to wait for the hub connection readiness event.
    max_messages : int or None, optional
        Stop after printing this many messages; ``None`` listens until the
        connection ends.
    decrypt_key_file : str or None, optional
        Local 32-byte payload key used to decrypt encrypted chat envelopes.

    Returns
    -------
    int
        ``0`` when the listener stops intentionally (message cap or a clean
        client-side close after a successful session start with
        ``max_messages=0``). ``1`` when the hub was unreachable, refused the
        name (including post-welcome close ``4009`` name conflict), or the
        connection died while listening for any other hub-initiated reason.
        There is no silent dead listener: a lost socket always exits nonzero
        with the close code and reason when the hub supplied them.
    """
    try:
        decrypt_key = load_payload_key(decrypt_key_file) if decrypt_key_file else None
    except (OSError, PayloadCryptoError) as exc:
        print(f"decryption key failed: {exc}")
        return 1
    printed = 0
    intentional_stop = False

    async def show(data: dict[str, Any]) -> None:
        nonlocal printed, intentional_stop
        msg_type = data.get("type")
        did_print = False
        if msg_type == MessageType.CHAT:
            if for_name and not is_recipient(str(data.get("target", "all")), for_name):
                return
            print(terminal_chat_line(data.get("sender"), _render_chat_payload(data, decrypt_key)))
            did_print = True
        elif msg_type == MessageType.PRESENCE_UPDATE and not for_name:
            online = ", ".join(
                terminal_text(agent_name) for agent_name in data.get("online_agents", [])
            )
            print(f"[presence] {terminal_text(data.get('event'))} -> online: {online}")
            did_print = True
        if did_print and max_messages is not None:
            printed += 1
            if printed >= max_messages:
                intentional_stop = True
                agent.running = False
                if agent.connection is not None:
                    await agent.connection.close()

    agent = agent_factory(name, show, uri=uri, verbose=True, token=token)
    conn_task = asyncio.create_task(agent.connect())
    try:
        if not await agent.wait_until_ready(timeout=ready_timeout):
            print(
                describe_connect_failure(
                    name,
                    uri,
                    close_code=agent.last_close_code,
                    close_reason=agent.last_close_reason,
                )
            )
            return 1
        # Finite zero-cap probe: connected successfully, then stop without listening.
        if max_messages == 0:
            intentional_stop = True
            agent.running = False
            if agent.connection is not None:
                await agent.connection.close()
            return 0
        # Name conflicts (4009) often land after welcome. Wait only for a hub
        # close *code* so an intentional client stop cannot look like a refusal.
        if await _hub_close_code_after_ready(agent):
            print(
                describe_connect_failure(
                    name,
                    uri,
                    close_code=agent.last_close_code,
                    close_reason=agent.last_close_reason,
                )
            )
            return 1
        await conn_task
        if intentional_stop:
            return 0
        # Hub-initiated death while listening: never exit 0 without a reason.
        print(
            describe_connect_failure(
                name,
                uri,
                close_code=agent.last_close_code,
                close_reason=agent.last_close_reason,
            )
        )
        return 1
    finally:
        agent.running = False
        if not conn_task.done():
            conn_task.cancel()
            try:
                await conn_task
            except (asyncio.CancelledError, Exception):
                pass


async def _hub_close_code_after_ready(
    agent: Any,
    *,
    grace_seconds: float = 0.25,
) -> bool:
    """Return whether the hub recorded a close code shortly after welcome.

    Unlike :func:`closed_after_ready`, this ignores ``running`` alone so a
    client-side stop (message cap) cannot be misread as a post-welcome refusal.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(0.0, grace_seconds)
    while loop.time() < deadline:
        if agent.last_close_code is not None:
            return True
        await asyncio.sleep(0.02)
    return agent.last_close_code is not None


def _cmd_listen(
    args: argparse.Namespace,
    *,
    listen_runner: ListenRunner = _listen,
    async_runner: AsyncRunner = asyncio.run,
) -> int:
    """Dispatch the ``listen`` subcommand."""
    try:
        return async_runner(
            listen_runner(
                uri=args.uri,
                name=args.name,
                token=args.token,
                for_name=args.for_name,
                ready_timeout=args.ready_timeout,
                decrypt_key_file=getattr(args, "decrypt_key_file", None),
            )
        )
    except KeyboardInterrupt:
        print(f"\n[{args.name}] stopped listening.")
        return 0


def _render_chat_payload(data: dict[str, Any], decrypt_key: bytes | None) -> str:
    """Return plaintext, decrypted plaintext, or an encrypted placeholder."""
    encrypted = data.get("encrypted")
    if not isinstance(encrypted, dict):
        return str(data.get("payload") or "")
    if decrypt_key is None:
        return str(data.get("payload") or "<encrypted payload>")
    try:
        return decrypt_payload(
            encrypted,
            decrypt_key,
            context=PayloadContext(
                message_type=str(data.get("type") or MessageType.CHAT),
                sender=str(data.get("sender") or ""),
                target=str(data.get("target") or "all"),
                channel=str(data.get("channel") or ""),
                task_id=str(data.get("task_id") or ""),
            ),
        )
    except PayloadCryptoError as exc:
        return f"<encrypted payload: {exc}>"
