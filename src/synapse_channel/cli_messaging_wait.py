# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — messaging CLI wait command
"""One-shot wake wait command for the ``synapse`` CLI."""

from __future__ import annotations

import argparse
import asyncio
import random
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

from synapse_channel.cli_messaging_types import AgentFactory, JitterFunction
from synapse_channel.client.agent import SynapseAgent
from synapse_channel.connect_failures import (
    describe_connect_failure,
    is_superseded_close,
    is_takeover_refused_close,
)
from synapse_channel.core.protocol import MessageType, wakes
from synapse_channel.core.wake_capability import WAKE_PASSIVE
from synapse_channel.mailbox_cursor import load_cursor, save_cursor
from synapse_channel.waiter_identity import waiter_name

WaitRunner = Callable[..., Coroutine[Any, Any, int]]
AsyncRunner = Callable[[Coroutine[Any, Any, int]], int]


async def _wait(
    *,
    uri: str,
    name: str,
    for_name: str,
    timeout: float,
    directed_only: bool = False,
    roles: tuple[str, ...] = (),
    wake_jitter: float = 0.0,
    agent_factory: AgentFactory = SynapseAgent,
    jitter_func: JitterFunction = random.uniform,
    token: str | None = None,
    ready_timeout: float = 5.0,
    poll_interval: float = 0.1,
    mailbox: bool = False,
    mailbox_cursor_path: Path | None = None,
    wake_capability: str = WAKE_PASSIVE,
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
    roles : tuple[str, ...], optional
        Full ``<project>/<role>`` names this waiter also answers to, so a message
        addressed to a role it holds wakes it as if addressed by name. Empty by
        default.
    wake_jitter : float, optional
        Seconds of random delay added before exiting on a *broadcast* wake (a
        message to ``all`` or a glob/list that reaches many waiters). A broadcast
        wakes every terminal at once; without jitter their agents all re-invoke in
        the same instant and the provider rate-limits the burst. Jitter spreads the
        wakes over ``[0, wake_jitter]`` so each reacts but not simultaneously. A
        one-to-one directed message wakes immediately (no herd). ``0`` disables it.
    agent_factory : AgentFactory, optional
        Factory for the client agent; injectable for testing.
    jitter_func : JitterFunction, optional
        Function used to compute broadcast wake jitter.
    token : str or None, optional
        Shared-secret token for a secured hub.
    ready_timeout : float, optional
        Seconds to wait for the hub connection readiness event.
    poll_interval : float, optional
        Seconds to wait between wake checks.
    mailbox : bool, optional
        When ``True``, the waiter runs in mailbox mode: it declares ``for_name`` as
        the identity to replay for, so a directed message that arrived while it was
        disconnected (a reconnect or re-arm gap) is replayed on connect and wakes it,
        instead of sitting unread until an unrelated wake. Off by default.
    mailbox_cursor_path : pathlib.Path or None, optional
        File holding the ``since_seq`` cursor to resume the mailbox from, read on
        entry and rewritten from the agent's advanced cursor on exit. Persisting it
        across re-arms is what stops each fresh waiter process from being replayed —
        and waking on — the whole retained backlog again. ``None`` disables
        persistence (the mailbox replays the whole window each connect).
    wake_capability : str, optional
        Receiver capability declared to the hub. A bare wait socket defaults to
        ``passive`` because receiving a frame does not prove an agent pane was woken.

    Returns
    -------
    int
        ``0`` when a message arrived, ``1`` when the hub was unreachable, ``2`` on
        timeout with nothing received, ``3`` when the connection dropped while
        waiting (so the caller knows to re-arm rather than treat it as a timeout),
        ``4`` when a newer connection took the name over (the caller must *yield*,
        not re-arm — reconnecting would evict the legitimate holder and the two
        waiters would fight over the identity indefinitely).
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
                roles=roles,
            )
        ):
            received.append(data)

    # A re-arming waiter takes over its own name, evicting a ghost holder of
    # ``<name>-rx`` instead of failing with a name conflict. In mailbox mode it
    # resumes from the persisted cursor and declares ``for_name`` as the identity to
    # replay for, since it connects under an ``-rx`` name but waits on the bare one.
    since_seq = (
        load_cursor(mailbox_cursor_path) if mailbox and mailbox_cursor_path is not None else 0
    )
    agent = agent_factory(
        name,
        collect,
        uri=uri,
        verbose=False,
        token=token,
        takeover=True,
        roles=roles,
        mailbox=mailbox,
        mailbox_since_seq=since_seq,
        mailbox_for=for_name if mailbox else "",
        wake_capability=wake_capability,
    )
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
            if is_takeover_refused_close(agent.last_close_code, agent.last_close_reason):
                # Another live connection holds this name and the hub is
                # protecting it; retrying the takeover would only feed the
                # oscillation quarantine. Yield the identity.
                return 4
            return 1
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        while not received and (timeout <= 0 or loop.time() < deadline):
            if conn_task.done():
                break  # the socket closed (hub restart, superseded, network)
            await asyncio.sleep(poll_interval)
        if received:
            message = received[-1]
            target = str(message.get("target", "all")).strip()
            # A broadcast woke many terminals at the same instant; jitter the exit
            # so their agents do not all re-invoke (and hit the provider API)
            # simultaneously and get rate-limited. A 1:1 directed message has no
            # herd, so it wakes now.
            reaches_many = target in ("", "all") or "*" in target or "," in target
            if reaches_many and wake_jitter > 0:
                await asyncio.sleep(jitter_func(0.0, wake_jitter))
            print(f"{message.get('sender')}: {message.get('payload')}")
            return 0
        if conn_task.done():
            if is_superseded_close(agent.last_close_code, agent.last_close_reason):
                # A newer waiter took this name over. The newcomer is the
                # legitimate holder (it just proved it is alive); re-arming with
                # a takeover of our own would evict it and the two waiters would
                # steal the identity from each other indefinitely.
                print(f"[{name}] superseded by a newer waiter; yielding.")
                return 4
            # The connection dropped without a message. Exit so the caller re-arms,
            # rather than looping forever on a dead socket — a timeout=0 waiter that
            # silently stayed up after a hub restart is exactly how an agent goes dark.
            print(f"[{name}] connection to {uri} closed; re-arm the waiter.")
            return 3
        return 2
    finally:
        agent.running = False
        conn_task.cancel()
        if mailbox and mailbox_cursor_path is not None:
            # Persist the cursor the agent advanced (past any replayed or live frames)
            # so the next re-arm resumes from here instead of re-replaying the backlog.
            save_cursor(mailbox_cursor_path, agent.mailbox_cursor)


def _cmd_wait(
    args: argparse.Namespace,
    *,
    wait_runner: WaitRunner = _wait,
    async_runner: AsyncRunner = asyncio.run,
) -> int:
    """Dispatch the ``wait`` subcommand.

    The waiter connects only to *receive*, so its connection name must never be the
    bare identity it waits for — otherwise it holds that name and the agent's own
    sends (which use the same identity) are refused with a name conflict. When the
    two would coincide, the connection name is suffixed with ``-rx``.
    """
    for_name = args.for_name or args.name
    connect_name = args.name if args.name != for_name else waiter_name(args.name)
    roles = tuple(r.strip() for r in (getattr(args, "role", None) or ()) if r.strip())
    return async_runner(
        wait_runner(
            uri=args.uri,
            name=connect_name,
            for_name=for_name,
            timeout=args.timeout,
            directed_only=args.directed_only,
            roles=roles,
            wake_jitter=args.wake_jitter,
            token=args.token,
            ready_timeout=args.ready_timeout,
            wake_capability=getattr(args, "wake_capability", WAKE_PASSIVE),
        )
    )
