# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — human/script messaging CLI commands (send, wait, listen)
"""The over-the-wire messaging ``synapse`` subcommands.

These commands connect to a live hub and exchange chat with it, as opposed
to the hub-lifecycle commands or the read-only query views: ``send`` connects,
delivers one message, optionally prints replies for a window, and exits;
``wait`` blocks until one message addressed to you arrives, prints it, and exits
— a one-shot wake primitive for scripts; ``listen`` streams chat and presence
until interrupted. Persistent arming lives in :mod:`synapse_channel.cli_arm` so
this module stays focused on direct messaging flows.

The send/wait/listen helpers take an injectable agent factory so the dispatch and
the client flows are unit-testable without a live hub.
"""

from __future__ import annotations

import argparse
import asyncio
import random
from collections.abc import Callable
from typing import Any

from synapse_channel.client.agent import DEFAULT_HUB_URI, SynapseAgent
from synapse_channel.core.protocol import MessageType, is_recipient, wakes

AgentFactory = Callable[..., SynapseAgent]


async def _send(
    *,
    uri: str,
    name: str,
    target: str,
    message: str,
    wait_seconds: float,
    priority: bool = False,
    agent_factory: AgentFactory = SynapseAgent,
    token: str | None = None,
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
    agent_factory : AgentFactory, optional
        Factory for the client agent; injectable for testing.

    Returns
    -------
    int
        ``0`` on success, ``1`` when the hub could not be reached.
    """
    replies: list[dict[str, Any]] = []

    async def collect(data: dict[str, Any]) -> None:
        if data.get("type") == MessageType.CHAT and data.get("sender") != name:
            replies.append(data)

    agent = agent_factory(name, collect, uri=uri, verbose=False, token=token)
    conn_task = asyncio.create_task(agent.connect())
    try:
        if not await agent.wait_until_ready(timeout=5.0):
            print(f"[{name}] Could not reach hub at {uri}.")
            return 1
        await agent.chat(message, target=target, priority=priority)
        if wait_seconds > 0:
            await asyncio.sleep(wait_seconds)
            for reply in replies:
                print(f"{reply.get('sender')}: {reply.get('payload')}")
        return 0
    finally:
        agent.running = False
        conn_task.cancel()


def _cmd_send(args: argparse.Namespace) -> int:
    """Dispatch the ``send`` subcommand."""
    return asyncio.run(
        _send(
            uri=args.uri,
            name=args.name,
            target=args.target,
            message=args.message,
            wait_seconds=args.wait_seconds,
            priority=args.priority,
            token=args.token,
        )
    )


async def _wait(
    *,
    uri: str,
    name: str,
    for_name: str,
    timeout: float,
    directed_only: bool = False,
    wake_jitter: float = 0.0,
    agent_factory: AgentFactory = SynapseAgent,
    token: str | None = None,
) -> int:
    """Block until one message addressed to ``for_name`` arrives, print it, and exit.

    This is the wake primitive: an agent runs it as a background task and the
    moment a message lands the command exits, which re-invokes the agent. The
    connection holds presence while it waits.

    Parameters
    ----------
    uri, name : str
        Hub URI and the connecting identity (keep it distinct from the sender
        name so a waiter and a one-shot ``send`` for the same project never clash).
    for_name : str
        Whose messages to wake on; a chat matches when its target addresses
        ``for_name`` — one agent, a group glob (``quantum/*``), or a broadcast.
    timeout : float
        Seconds to wait; ``0`` waits indefinitely.
    directed_only : bool, optional
        When ``True``, wake only on messages that name ``for_name`` (or a group it
        is in), not on broadcasts — broadcasts are left for a later ``syn-inbox``.
    wake_jitter : float, optional
        Seconds of random delay added before exiting on a *broadcast* wake (a
        message to ``all`` or a glob/list that reaches many waiters). A broadcast
        wakes every terminal at once; without jitter their agents all re-invoke in
        the same instant and the provider rate-limits the burst. Jitter spreads the
        wakes over ``[0, wake_jitter]`` so each reacts but not simultaneously. A
        one-to-one directed message wakes immediately (no herd). ``0`` disables it.
    agent_factory : AgentFactory, optional
        Factory for the client agent; injectable for testing.
    token : str or None, optional
        Shared-secret token for a secured hub.

    Returns
    -------
    int
        ``0`` when a message arrived, ``1`` when the hub was unreachable, ``2`` on
        timeout with nothing received, ``3`` when the connection dropped while
        waiting (so the caller knows to re-arm rather than treat it as a timeout).
    """
    received: list[dict[str, Any]] = []

    async def collect(data: dict[str, Any]) -> None:
        sender = str(data.get("sender", ""))
        if (
            data.get("type") == MessageType.CHAT
            and sender != name
            and sender != for_name  # ignore our own sends (the agent sends as for_name)
            and wakes(
                str(data.get("target", "all")),
                for_name,
                directed_only=directed_only,
                sender=sender,
                priority=bool(data.get("priority")),
            )
        ):
            received.append(data)

    # A re-arming waiter takes over its own name, evicting a ghost holder of
    # ``<name>-rx`` instead of failing with a name conflict.
    agent = agent_factory(name, collect, uri=uri, verbose=False, token=token, takeover=True)
    conn_task = asyncio.create_task(agent.connect())
    try:
        if not await agent.wait_until_ready(timeout=5.0):
            print(f"[{name}] Could not reach hub at {uri}.")
            return 1
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        while not received and (timeout <= 0 or loop.time() < deadline):
            if conn_task.done():
                break  # the socket closed (hub restart, superseded, network)
            await asyncio.sleep(0.1)
        if received:
            message = received[-1]
            target = str(message.get("target", "all")).strip()
            # A broadcast woke many terminals at the same instant; jitter the exit
            # so their agents do not all re-invoke (and hit the provider API)
            # simultaneously and get rate-limited. A 1:1 directed message has no
            # herd, so it wakes now.
            reaches_many = target in ("", "all") or "*" in target or "," in target
            if reaches_many and wake_jitter > 0:
                await asyncio.sleep(random.uniform(0.0, wake_jitter))
            print(f"{message.get('sender')}: {message.get('payload')}")
            return 0
        if conn_task.done():
            # The connection dropped without a message. Exit so the caller re-arms,
            # rather than looping forever on a dead socket — a timeout=0 waiter that
            # silently stayed up after a hub restart is exactly how an agent goes dark.
            print(f"[{name}] connection to {uri} closed; re-arm the waiter.")
            return 3
        return 2
    finally:
        agent.running = False
        conn_task.cancel()


def _cmd_wait(args: argparse.Namespace) -> int:
    """Dispatch the ``wait`` subcommand.

    The waiter connects only to *receive*, so its connection name must never be the
    bare identity it waits for — otherwise it holds that name and the agent's own
    sends (which use the same identity) are refused with a name conflict. When the
    two would coincide, the connection name is suffixed with ``-rx``.
    """
    for_name = args.for_name or args.name
    connect_name = args.name if args.name != for_name else f"{args.name}-rx"
    return asyncio.run(
        _wait(
            uri=args.uri,
            name=connect_name,
            for_name=for_name,
            timeout=args.timeout,
            directed_only=args.directed_only,
            wake_jitter=args.wake_jitter,
            token=args.token,
        )
    )


async def _listen(
    *,
    uri: str,
    name: str,
    agent_factory: AgentFactory = SynapseAgent,
    token: str | None = None,
    for_name: str | None = None,
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

    Returns
    -------
    int
        Always ``0`` once the connection closes.
    """

    async def show(data: dict[str, Any]) -> None:
        msg_type = data.get("type")
        if msg_type == MessageType.CHAT:
            if for_name and not is_recipient(str(data.get("target", "all")), for_name):
                return
            print(f"{data.get('sender')}: {data.get('payload')}")
        elif msg_type == MessageType.PRESENCE_UPDATE and not for_name:
            online = ", ".join(data.get("online_agents", []))
            print(f"[presence] {data.get('event')} -> online: {online}")

    agent = agent_factory(name, show, uri=uri, verbose=True, token=token)
    await agent.connect()
    return 0


def _cmd_listen(args: argparse.Namespace) -> int:
    """Dispatch the ``listen`` subcommand."""
    try:
        return asyncio.run(
            _listen(uri=args.uri, name=args.name, token=args.token, for_name=args.for_name)
        )
    except KeyboardInterrupt:
        print(f"\n[{args.name}] stopped listening.")
        return 0


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``send``, ``wait``, and ``listen`` subparsers."""
    send = subparsers.add_parser("send", help="Send one message and optionally await replies.")
    send.add_argument("--uri", default=DEFAULT_HUB_URI)
    send.add_argument("--name", default="USER")
    send.add_argument("--target", default="all")
    send.add_argument("--wait-seconds", type=float, default=2.0)
    send.add_argument(
        "--priority",
        action="store_true",
        help="Mark as priority so it wakes even directed-only waiters (use sparingly).",
    )
    send.add_argument("--token", default=None, help="Shared-secret token for a secured hub.")
    send.add_argument("message")
    send.set_defaults(func=_cmd_send)

    wait = subparsers.add_parser(
        "wait", help="Block until a message addressed to you arrives, then exit (a wake trigger)."
    )
    wait.add_argument("--uri", default=DEFAULT_HUB_URI)
    wait.add_argument("--name", default="USER")
    wait.add_argument(
        "--for",
        dest="for_name",
        default=None,
        help="Whose messages to wake on (one, a group, or broadcast); defaults to --name.",
    )
    wait.add_argument(
        "--timeout", type=float, default=0.0, help="Seconds to wait; 0 waits indefinitely."
    )
    wait.add_argument(
        "--directed-only",
        action="store_true",
        help="Wake only on messages that name you (or a group you are in), not broadcasts.",
    )
    wait.add_argument(
        "--wake-jitter",
        type=float,
        default=8.0,
        help="Random seconds (0..N) to delay exiting on a broadcast wake, so many "
        "terminals do not re-invoke at once and trip the provider rate limit; 0 disables.",
    )
    wait.add_argument("--token", default=None, help="Shared-secret token for a secured hub.")
    wait.set_defaults(func=_cmd_wait)

    listen = subparsers.add_parser("listen", help="Stream channel messages until interrupted.")
    listen.add_argument("--uri", default=DEFAULT_HUB_URI)
    listen.add_argument("--name", default="USER")
    listen.add_argument("--token", default=None, help="Shared-secret token for a secured hub.")
    listen.add_argument(
        "--for",
        dest="for_name",
        default=None,
        help="Show only chats addressed to this name (or broadcast) and suppress "
        "presence updates — a focused per-agent inbox.",
    )
    listen.set_defaults(func=_cmd_listen)
