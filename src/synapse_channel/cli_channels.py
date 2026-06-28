# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — private-channel membership CLI
"""Command-line surface for private channels: create, join, leave, list.

A private channel is an audience-scoped recipient set. Once members have joined,
``synapse send --channel <id>`` delivers a message only to that channel's online
members. These commands manage membership; they are a local coordination
convenience, not a cryptographic boundary (see ``docs/private-channels``).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
from collections.abc import Callable
from typing import Any

from synapse_channel.client.agent import DEFAULT_HUB_URI, SynapseAgent
from synapse_channel.connect_failures import describe_connect_failure
from synapse_channel.core.protocol import MessageType

AgentFactory = Callable[..., SynapseAgent]

_RESULT_TYPES = {MessageType.CHANNEL_RESULT, MessageType.CHANNEL_LIST}


async def _send_channel_op(agent: SynapseAgent, command: str, channel: str, label: str) -> None:
    """Send the channel operation named by ``command`` on a connected agent."""
    if command == "create":
        await agent.channel_create(channel, label=label)
    elif command == "join":
        await agent.channel_join(channel)
    elif command == "leave":
        await agent.channel_leave(channel)
    else:
        await agent.request_channels()


async def _run_channel_command(
    *,
    uri: str,
    name: str,
    token: str | None,
    command: str,
    channel: str,
    label: str,
    ready_timeout: float,
    response_timeout: float,
    agent_factory: AgentFactory = SynapseAgent,
) -> int:
    """Connect, run one channel operation, print the hub reply, and return a code.

    Parameters
    ----------
    uri, name : str
        Hub URI and the requesting agent identity.
    token : str or None
        Shared-secret token for a secured hub.
    command : str
        One of ``create``, ``join``, ``leave``, or ``list``.
    channel, label : str
        Channel id and display label (label is used only by ``create``).
    ready_timeout, response_timeout : float
        Seconds to await hub readiness and the reply.
    agent_factory : AgentFactory, optional
        Client factory; injectable for testing.

    Returns
    -------
    int
        ``0`` on a successful operation, ``1`` otherwise.
    """
    reply: dict[str, Any] = {}

    async def collect(data: dict[str, Any]) -> None:
        if str(data.get("type", "")) in _RESULT_TYPES:
            reply.update(data)

    agent = agent_factory(name, collect, uri=uri, verbose=False, token=token)
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
        await _send_channel_op(agent, command, channel, label)
        deadline = asyncio.get_running_loop().time() + max(0.0, response_timeout)
        while not reply and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.025)
        if not reply:
            print(f"[{name}] Hub did not answer the channel operation.")
            return 1
        return _print_reply(reply)
    finally:
        agent.running = False
        conn_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await conn_task


def _print_reply(reply: dict[str, Any]) -> int:
    """Print a channel-operation reply and return its exit code."""
    if str(reply.get("type", "")) == MessageType.CHANNEL_LIST:
        channels = reply.get("channels", [])
        names = channels if isinstance(channels, list) else []
        print("channels: " + (", ".join(str(c) for c in names) if names else "(none)"))
        return 0
    ok = bool(reply.get("ok"))
    print(str(reply.get("payload") or ("ok" if ok else "failed")))
    if ok:
        members = reply.get("members", [])
        if isinstance(members, list) and members:
            print("members: " + ", ".join(str(m) for m in members))
    return 0 if ok else 1


def _cmd_channel(args: argparse.Namespace) -> int:
    """Dispatch ``synapse channel`` subcommands."""
    return asyncio.run(
        _run_channel_command(
            uri=args.uri,
            name=args.name,
            token=args.token,
            command=args.channel_command,
            channel=getattr(args, "channel", ""),
            label=getattr(args, "label", ""),
            ready_timeout=args.ready_timeout,
            response_timeout=args.response_timeout,
        )
    )


def _add_common(parser: argparse.ArgumentParser) -> None:
    """Add shared connection options to a channel subcommand."""
    parser.add_argument("--name", required=True, help="Requesting agent identity.")
    parser.add_argument("--uri", default=DEFAULT_HUB_URI, help="Synapse hub URI.")
    parser.add_argument("--token", default=None, help="Shared-secret token for a secured hub.")
    parser.add_argument("--ready-timeout", type=float, default=5.0, help="Hub readiness timeout.")
    parser.add_argument("--response-timeout", type=float, default=3.0, help="Reply timeout.")


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``channel`` subparser group."""
    channel = subparsers.add_parser("channel", help="Manage private channel membership.")
    nested = channel.add_subparsers(dest="channel_command", required=True)

    create = nested.add_parser("create", help="Create a private channel you own.")
    create.add_argument("channel", help="Channel id.")
    create.add_argument("--label", default="", help="Display label.")
    _add_common(create)
    create.set_defaults(func=_cmd_channel)

    join = nested.add_parser("join", help="Join a private channel.")
    join.add_argument("channel", help="Channel id.")
    _add_common(join)
    join.set_defaults(func=_cmd_channel)

    leave = nested.add_parser("leave", help="Leave a private channel.")
    leave.add_argument("channel", help="Channel id.")
    _add_common(leave)
    leave.set_defaults(func=_cmd_channel)

    listing = nested.add_parser("list", help="List the channels you belong to.")
    _add_common(listing)
    listing.set_defaults(func=_cmd_channel)
