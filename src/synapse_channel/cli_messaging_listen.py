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
from synapse_channel.core.protocol import MessageType, is_recipient


async def _listen(
    *,
    uri: str,
    name: str,
    agent_factory: AgentFactory = SynapseAgent,
    token: str | None = None,
    for_name: str | None = None,
    ready_timeout: float = 5.0,
    max_messages: int | None = None,
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

    Returns
    -------
    int
        ``0`` once the connection closes, ``1`` when the hub was unreachable.
    """
    printed = 0

    async def show(data: dict[str, Any]) -> None:
        nonlocal printed
        msg_type = data.get("type")
        did_print = False
        if msg_type == MessageType.CHAT:
            if for_name and not is_recipient(str(data.get("target", "all")), for_name):
                return
            print(f"{data.get('sender')}: {data.get('payload')}")
            did_print = True
        elif msg_type == MessageType.PRESENCE_UPDATE and not for_name:
            online = ", ".join(data.get("online_agents", []))
            print(f"[presence] {data.get('event')} -> online: {online}")
            did_print = True
        if did_print and max_messages is not None:
            printed += 1
            if printed >= max_messages:
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
        if max_messages == 0:
            return 0
        await conn_task
        return 0
    finally:
        agent.running = False
        conn_task.cancel()


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
            )
        )
    except KeyboardInterrupt:
        print(f"\n[{args.name}] stopped listening.")
        return 0
