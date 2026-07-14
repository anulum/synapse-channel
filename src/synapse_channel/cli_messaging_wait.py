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
    is_identity_refused_close,
    is_name_owned_close,
    is_superseded_close,
    is_takeover_refused_close,
)
from synapse_channel.core.protocol import MessageType, wakes
from synapse_channel.core.wake_capability import WAKE_PANE_BRIDGE, WAKE_PASSIVE
from synapse_channel.machine_identity import machine_identity_agent_kwargs
from synapse_channel.mailbox_cursor import load_cursor, save_cursor
from synapse_channel.owner_lease import lease_agent_kwargs, lease_path
from synapse_channel.shell_integration import has_active_tmux_provider
from synapse_channel.terminal_text import terminal_chat_line
from synapse_channel.waiter_identity import (
    legacy_project_scoped_terminal_sidecar,
    waiter_name,
    waiter_owner,
)

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
    owner_lease_path: Path | None = None,
    identity_key_path: str | None = None,
    identity_key_id: str = "",
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
    owner_lease_path : pathlib.Path or None, optional
        File holding this connection name's hub ownership-lease token (see
        :mod:`synapse_channel.owner_lease`). When set, the waiter opts into the
        hub's ownership lease: it presents the stored token so a re-arm re-takes
        its own name, and persists a freshly granted one, so a stranger cannot
        squat the waiter identity in a re-arm gap. ``None`` disables lease
        participation (classic first-come semantics).
    identity_key_path : str or None, optional
        PEM file of the Ed25519 identity key signing the registration — for the
        production waiter, the auto-provisioned machine key
        (:mod:`synapse_channel.machine_identity`), so a first-use hub pins the
        waiter name to this machine. ``None`` registers unsigned.
    identity_key_id : str, optional
        Key id carried in the registration signature envelope.

    Returns
    -------
    int
        ``0`` when a message arrived, ``1`` when the hub was unreachable, ``2`` on
        timeout with nothing received, ``3`` when the connection dropped while
        waiting (so the caller knows to re-arm rather than treat it as a timeout),
        ``4`` when a newer connection took the name over or an ownership lease
        held by another identity refused the claim (the caller must *yield*,
        not re-arm — reconnecting would evict the legitimate holder, or feed a
        refusal that can never succeed without the lease token).
    """
    received: list[dict[str, Any]] = []

    def matches(data: dict[str, Any]) -> bool:
        """Return whether one frame wakes this waiter.

        The SAME predicate gates the wake collection and the agent's mailbox
        cursor advance, so the two can never drift: a frame this waiter will
        not surface is never consumed from the mailbox either.
        """
        sender = str(data.get("sender", ""))
        return (
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
        )

    async def collect(data: dict[str, Any]) -> None:
        if matches(data):
            received.append(data)

    # A re-arming waiter takes over its own name, evicting a ghost holder of
    # ``<name>-rx`` instead of failing with a name conflict. In mailbox mode it
    # resumes from the persisted cursor and declares ``for_name`` as the identity to
    # replay for, since it connects under an ``-rx`` name but waits on the bare one.
    since_seq = (
        load_cursor(mailbox_cursor_path) if mailbox and mailbox_cursor_path is not None else 0
    )
    # Highest durable seq this waiter actually SURFACED (printed). The persisted
    # resume point advances only through here — never past a frame the operator
    # was not shown (see the finally block).
    surfaced_seq = [since_seq]
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
        mailbox_advance=matches,
        wake_capability=wake_capability,
        identity_key_path=identity_key_path,
        identity_key_id=identity_key_id,
        **lease_agent_kwargs(owner_lease_path),
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
            if is_name_owned_close(agent.last_close_code, agent.last_close_reason):
                # An ownership lease held by another identity refused this
                # claim. Without the lease token a retry can never succeed;
                # yield instead of hammering the refusal.
                return 4
            if is_identity_refused_close(agent.last_close_code, agent.last_close_reason):
                # The name is pinned to (or requires) an identity key this
                # process does not hold — the same yield verdict.
                return 4
            return 1
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        while not received and (timeout <= 0 or loop.time() < deadline):
            if conn_task.done():
                break  # the socket closed (hub restart, superseded, network)
            await asyncio.sleep(poll_interval)
        if received:
            # Snapshot the burst BEFORE any await: a frame arriving during the
            # jitter sleep joins the next wake, it is not half-consumed here.
            to_surface = list(received)
            target = str(to_surface[-1].get("target", "all")).strip()
            # A broadcast woke many terminals at the same instant; jitter the exit
            # so their agents do not all re-invoke (and hit the provider API)
            # simultaneously and get rate-limited. A 1:1 directed message has no
            # herd, so it wakes now.
            reaches_many = target in ("", "all") or "*" in target or "," in target
            if reaches_many and wake_jitter > 0:
                await asyncio.sleep(jitter_func(0.0, wake_jitter))
            # Surface EVERY collected frame in arrival order. A replay burst can
            # deliver a whole backlog inside one poll window; printing only one
            # frame while the persisted cursor advanced past all of them silently
            # lost the rest (the 2026-07-10 P0 drain swallowed a backlog this way).
            for message in to_surface:
                print(terminal_chat_line(message.get("sender"), message.get("payload")))
            surfaced_seq[0] = max(
                (
                    seq
                    for seq in (frame.get("seq") for frame in to_surface)
                    if isinstance(seq, int) and not isinstance(seq, bool)
                ),
                default=surfaced_seq[0],
            )
            return 0
        if conn_task.done():
            if is_superseded_close(agent.last_close_code, agent.last_close_reason):
                # A newer waiter took this name over. The newcomer is the
                # legitimate holder (it just proved it is alive); re-arming with
                # a takeover of our own would evict it and the two waiters would
                # steal the identity from each other indefinitely.
                print(f"[{name}] superseded by a newer waiter; yielding.")
                return 4
            if is_name_owned_close(
                agent.last_close_code, agent.last_close_reason
            ) or is_identity_refused_close(agent.last_close_code, agent.last_close_reason):
                # On an open hub the welcome precedes registration, so an
                # ownership-lease or identity-pin refusal lands after
                # readiness. It is a yield verdict either way: re-arming
                # without the lease token or the pinned key would only hammer
                # a refusal that can never pass.
                print(
                    describe_connect_failure(
                        name,
                        uri,
                        close_code=agent.last_close_code,
                        close_reason=agent.last_close_reason,
                    )
                )
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
            # Persist a resume point covering exactly what this waiter SURFACED,
            # never merely what its socket saw: a matching frame that arrived too
            # late to be printed stays before the cursor and is replayed to the
            # next arm instead of being silently consumed.
            save_cursor(mailbox_cursor_path, surfaced_seq[0])


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
    legacy_terminal = legacy_project_scoped_terminal_sidecar(connect_name, for_name)
    if legacy_terminal is not None:
        print(
            f"[{connect_name}] legacy broad project wait for {for_name} would wake "
            f"on every project message; re-run for exact identity {legacy_terminal} instead."
        )
        return 0
    provider_identities = (for_name, waiter_owner(connect_name))
    wake_capability = getattr(args, "wake_capability", WAKE_PASSIVE)

    # Provider-aware early yield for stable session identity inheritance.
    # When a tmux provider (worker-session + agent-tmux wait) is active for the
    # identity (or SYN_TMUX_PROVIDER=1 explicitly set for the session), that
    # provider owns the long-lived -rx with pane_bridge. Plain passive arms
    # cause supersession churn and are not needed (the provider injects wake
    # prompt; inner agent does inbox on prompt). Yield immediately.
    #
    # Critical exception: the pane_bridge wait *is* that provider. worker-session
    # writes the provider pidfile for agent-tmux, then agent-tmux runs
    # ``synapse wait --wake-capability pane_bridge``. If this early-yield also
    # fired for pane_bridge, wait would return 0 immediately, agent-tmux would
    # treat that as a real wake, inject forever, and burn the agent pane.
    # Do not yield the bridge to itself.
    if wake_capability != WAKE_PANE_BRIDGE:
        # has_active_tmux_provider is identity-scoped: the SYN_TMUX_PROVIDER
        # session flag counts only for the session's own $SYN_IDENTITY, so an
        # explicitly named wait for a different seat is never suppressed here.
        live_provider_identity = next(
            (identity for identity in provider_identities if has_active_tmux_provider(identity)),
            None,
        )
        if live_provider_identity is not None:
            print(
                f"[{connect_name}] provider-backed session for {live_provider_identity}; "
                "agent-tmux wait is the canonical long-lived listener. "
                "Yielding plain passive to preserve identity inheritance for the session."
            )
            return 0

    machine = machine_identity_agent_kwargs()
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
            wake_capability=wake_capability,
            owner_lease_path=lease_path(connect_name),
            identity_key_path=machine.get("identity_key_path"),
            identity_key_id=str(machine.get("identity_key_id", "")),
        )
    )
