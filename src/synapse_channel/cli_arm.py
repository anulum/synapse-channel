# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — persistent wake-listener CLI command
"""Persistent provider-neutral wake listener behind ``synapse arm``."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
from collections.abc import Awaitable, Callable, Coroutine
from pathlib import Path
from typing import Any

from synapse_channel.cli_messaging import AgentFactory, _wait
from synapse_channel.client.agent import SynapseAgent, default_hub_uri
from synapse_channel.core.wake_capability import WAKE_PASSIVE
from synapse_channel.mailbox_cursor import cursor_path
from synapse_channel.shell_integration import has_active_tmux_provider
from synapse_channel.waiter_identity import waiter_name, waiter_owner

WaitRunner = Callable[..., Awaitable[int]]
SleepRunner = Callable[[float], Awaitable[None]]
ArmRunner = Callable[..., Coroutine[Any, Any, int]]
AsyncRunner = Callable[[Coroutine[Any, Any, int]], int]
PidProbe = Callable[[int], bool]

OWNER_CHECK_INTERVAL_SECONDS = 5.0


def _legacy_project_scoped_terminal_sidecar(connect_name: str, for_name: str) -> str | None:
    """Return the terminal identity for an old broad project-sidecar arm, if any."""
    owner = waiter_owner(connect_name)
    if owner != connect_name and owner.startswith(f"{for_name}/terminal-"):
        return owner
    return None


def pid_alive(pid: int) -> bool:
    """Return whether a process with ``pid`` exists (signal 0 probe).

    ``PermissionError`` means the process exists but belongs to another user, so
    it counts as alive; only ``ProcessLookupError`` (and a non-positive pid)
    reports death.
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


async def _watch_owner(
    owner_pid: int,
    *,
    interval: float = OWNER_CHECK_INTERVAL_SECONDS,
    probe: PidProbe = pid_alive,
    sleep_runner: SleepRunner = asyncio.sleep,
) -> None:
    """Return once the owner process is gone; poll it every ``interval`` seconds.

    A detached waiter (``nohup … & disown``) survives its spawning shell by
    construction, so parent-death signals cannot reach it — polling the recorded
    owner pid is the one mechanism that works regardless of reparenting.
    """
    while probe(owner_pid):
        await sleep_runner(interval)


async def _arm(
    *,
    uri: str,
    name: str,
    for_name: str,
    directed_only: bool = True,
    roles: tuple[str, ...] = (),
    wake_jitter: float = 0.0,
    reconnect_delay: float = 1.0,
    max_wakes: int | None = None,
    agent_factory: AgentFactory = SynapseAgent,
    wait_runner: WaitRunner = _wait,
    sleep_runner: SleepRunner = asyncio.sleep,
    token: str | None = None,
    owner_pid: int | None = None,
    owner_probe: PidProbe = pid_alive,
    owner_check_interval: float = OWNER_CHECK_INTERVAL_SECONDS,
    mailbox: bool = False,
    mailbox_cursor_path: Path | None = None,
    wake_capability: str = WAKE_PASSIVE,
) -> int:
    """Keep a directed waiter armed until interrupted, displaced, or orphaned.

    With ``owner_pid`` set the waiter is leashed to that process: an owner
    watchdog polls it and, the moment it is gone, the armed wait is cancelled
    and the loop returns — a waiter for a closed terminal can wake nobody, and
    leaving it connected inflates the hub roster with phantom presence.

    A wait that reports a takeover (exit ``4``) also ends the loop: a newer
    waiter owns the name now, and re-arming would take it back and leave the
    two stealing the identity from each other until the hub quarantines it.

    ``roles`` are the full ``<project>/<role>`` names this waiter also answers to,
    threaded into every re-armed wait so a message addressed to a role it holds
    wakes it across reconnects, not only a message to its instance name.

    With ``mailbox`` set, each re-armed wait resumes from a per-identity cursor
    (``mailbox_cursor_path``) and asks the hub to replay the directed messages that
    landed while it was disconnected, so a message that arrived in a reconnect or
    re-arm gap wakes the waiter on the next connect instead of waiting unread — and
    the shared cursor keeps a re-arm from being replayed the same backlog twice.

    ``wake_capability`` is forwarded into each one-shot wait; the arm command
    defaults to ``passive`` because a socket wake alone does not force a provider pane.
    """
    if owner_pid is not None and not owner_probe(owner_pid):
        print(f"[{name}] owner pid {owner_pid} is already gone; not arming.")
        return 0
    wakes_seen = 0
    while max_wakes is None or wakes_seen < max_wakes:
        wait_task = asyncio.ensure_future(
            wait_runner(
                uri=uri,
                name=name,
                for_name=for_name,
                timeout=0.0,
                directed_only=directed_only,
                roles=roles,
                wake_jitter=wake_jitter,
                agent_factory=agent_factory,
                token=token,
                mailbox=mailbox,
                mailbox_cursor_path=mailbox_cursor_path,
                wake_capability=wake_capability,
            )
        )
        if owner_pid is None:
            code = await wait_task
        else:
            watchdog = asyncio.ensure_future(
                _watch_owner(
                    owner_pid,
                    interval=owner_check_interval,
                    probe=owner_probe,
                    sleep_runner=sleep_runner,
                )
            )
            done, _ = await asyncio.wait({wait_task, watchdog}, return_when=asyncio.FIRST_COMPLETED)
            if wait_task not in done:
                wait_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await wait_task
                print(f"[{name}] owner pid {owner_pid} exited; disarming.")
                return 0
            watchdog.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await watchdog
            code = wait_task.result()
        if code == 0:
            wakes_seen += 1
            continue
        if code == 4:
            print(f"[{name}] a newer waiter holds this name; disarming.")
            return 0
        if reconnect_delay > 0:
            await sleep_runner(reconnect_delay)
    return 0


def _cmd_arm(
    args: argparse.Namespace,
    *,
    arm_runner: ArmRunner = _arm,
    async_runner: AsyncRunner = asyncio.run,
) -> int:
    """Announce the resolved binding, then keep a directed waiter armed.

    The identity to wake on is resolved explicit-first: ``--for`` (falling back
    to ``--name``) always beats the ambient ``SYN_IDENTITY`` — the borrowed-shell
    value behind the 2026-07-10 directed-delivery P0 — and that binding is stated
    on the first printed line so a wrong one shows immediately instead of after a
    night of silently missed messages; a session environment naming a different
    identity is flagged explicitly. Two cases deliberately yield instead of
    arming: a legacy broad project-sidecar wait (which would wake on every
    project message) and an identity already served by a live tmux provider
    (whose pane bridge is the real waker). The surviving case delegates to
    ``arm_runner`` to hold the waiter and re-arm it after each wake.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed ``arm`` arguments from :func:`add_parser` — ``name``/``for_name``,
        ``uri``, ``directed_only``, ``role``, mailbox and reconnect options,
        ``token`` and ``owner_pid``.
    arm_runner : ArmRunner, optional
        Coroutine factory that holds and re-arms the directed waiter; defaults to
        :func:`_arm`.
    async_runner : AsyncRunner, optional
        Callable that drives the ``arm_runner`` coroutine to completion; defaults
        to :func:`asyncio.run`.

    Returns
    -------
    int
        A process exit code: ``0`` for every clean stop — keyboard interrupt,
        owner-pid exit, a newer waiter taking the name, or a deliberate
        legacy/provider yield.
    """
    for_name = args.for_name or args.name
    connect_name = args.name if args.name != for_name else waiter_name(args.name)
    legacy_terminal = _legacy_project_scoped_terminal_sidecar(connect_name, for_name)
    if legacy_terminal is not None:
        print(
            f"[{connect_name}] legacy broad project wait for {for_name} would wake "
            f"on every project message; re-arm for exact identity {legacy_terminal} instead."
        )
        return 0
    provider_identities = (for_name, waiter_owner(connect_name))

    # Provider-aware early yield: an active tmux provider (worker-session +
    # agent-tmux) already owns the -rx pane bridge for this identity. A passive
    # arm would be superseded every few seconds and churn the hub roster. Leave
    # waking to the pane bridge; passive arms are for headless/non-tmux cases.
    live_provider_identity = next(
        (identity for identity in provider_identities if has_active_tmux_provider(identity)),
        None,
    )
    if live_provider_identity is not None:
        print(
            f"[{connect_name}] active tmux provider detected for {live_provider_identity}; "
            "pane_bridge / agent-tmux is the live waker. "
            "Yielding plain passive arm to avoid supersession/name collision."
        )
        return 0

    roles = tuple(r.strip() for r in (getattr(args, "role", None) or ()) if r.strip())
    # State the binding OUT LOUD before holding a socket for hours: an operator
    # (or an agent harness) reading the first line knows exactly whose messages
    # this waiter wakes on — a wrong binding is visible immediately, not after
    # a night of silently missed messages. When the session env names a
    # DIFFERENT identity, say so: ambient env never overrides an explicit
    # name, but the mismatch is the classic sign of arming from a borrowed
    # shell (2026-07-10 P0), so it deserves one clear line.
    print(f"[{connect_name}] waiting for messages to {for_name}")
    ambient_identity = os.environ.get("SYN_IDENTITY", "").strip()
    if ambient_identity and ambient_identity != for_name:
        print(
            f"[{connect_name}] note: session SYN_IDENTITY={ambient_identity} differs from "
            f"the armed identity {for_name}; this waiter wakes ONLY for {for_name}."
        )
    # In mailbox mode the cursor is keyed by the identity the waiter waits on (for_name),
    # not the -rx connection name, so every re-arm of the same identity shares one cursor.
    mailbox = bool(getattr(args, "mailbox", False))
    mailbox_cursor_path = cursor_path(for_name) if mailbox else None
    try:
        return async_runner(
            arm_runner(
                uri=args.uri,
                name=connect_name,
                for_name=for_name,
                directed_only=args.directed_only,
                roles=roles,
                wake_jitter=args.wake_jitter,
                reconnect_delay=args.reconnect_delay,
                max_wakes=args.max_wakes,
                token=args.token,
                owner_pid=args.owner_pid,
                mailbox=mailbox,
                mailbox_cursor_path=mailbox_cursor_path,
                wake_capability=getattr(args, "wake_capability", WAKE_PASSIVE),
            )
        )
    except KeyboardInterrupt:
        print(f"\n[{connect_name}] stopped arming for {for_name}.")
        return 0


def add_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the persistent ``arm`` subparser."""
    arm = subparsers.add_parser(
        "arm",
        help="Keep a waiter armed and re-arm automatically after each wake or reconnect.",
    )
    arm.add_argument("--uri", default=default_hub_uri())
    arm.add_argument("--name", default="USER")
    arm.add_argument(
        "--for",
        dest="for_name",
        default=None,
        help="Whose messages to wake on (one, a group, or broadcast); defaults to --name.",
    )
    arm.add_argument(
        "--directed-only",
        action="store_true",
        default=True,
        help="Wake only on messages that name you (or a group you are in), not broadcasts.",
    )
    arm.add_argument(
        "--broadcasts",
        dest="directed_only",
        action="store_false",
        help="Also wake on routine broadcasts to all.",
    )
    arm.add_argument(
        "--role",
        action="append",
        default=None,
        metavar="PROJECT/ROLE",
        help="A <project>/<role> name you also answer to (repeatable), so a message "
        "addressed to the role wakes you across reconnects, not only your instance name.",
    )
    arm.add_argument(
        "--mailbox",
        action="store_true",
        default=False,
        help="Also wake on directed messages that arrived while disconnected (a reconnect or "
        "re-arm gap): the hub replays them on connect. The resume cursor is persisted per "
        "identity so a re-arm is not replayed the whole backlog again.",
    )
    arm.add_argument(
        "--no-mailbox",
        dest="mailbox",
        action="store_false",
        help="Do not replay the reconnect-gap backlog. The `syn-wait` alias enables mailbox by "
        "default, so use this to opt a waiter out; a bare `synapse arm` is already off.",
    )
    arm.add_argument(
        "--wake-jitter",
        type=float,
        default=8.0,
        help="Random seconds (0..N) to delay re-arming after a broadcast wake; 0 disables.",
    )
    arm.add_argument(
        "--reconnect-delay",
        type=float,
        default=1.0,
        help="Seconds to wait before reconnecting after a dropped or temporarily unreachable hub.",
    )
    arm.add_argument(
        "--max-wakes",
        type=int,
        default=None,
        help="Stop after N wakes; primarily useful for smoke tests and scripts.",
    )
    arm.add_argument("--token", default=None, help="Shared-secret token for a secured hub.")
    arm.add_argument(
        "--wake-capability",
        default=WAKE_PASSIVE,
        choices=("direct", "passive", "pane_bridge"),
        help=argparse.SUPPRESS,
    )
    arm.add_argument(
        "--owner-pid",
        type=int,
        default=None,
        help=(
            "Disarm when this process exits. A shell hook passes its shell pid so a "
            "detached waiter cannot outlive the terminal it wakes."
        ),
    )
    arm.set_defaults(func=_cmd_arm)
