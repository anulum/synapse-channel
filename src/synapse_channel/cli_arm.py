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
from typing import Any

from synapse_channel.cli_messaging import AgentFactory, _wait
from synapse_channel.client.agent import SynapseAgent, default_hub_uri
from synapse_channel.waiter_identity import waiter_name

WaitRunner = Callable[..., Awaitable[int]]
SleepRunner = Callable[[float], Awaitable[None]]
ArmRunner = Callable[..., Coroutine[Any, Any, int]]
AsyncRunner = Callable[[Coroutine[Any, Any, int]], int]
PidProbe = Callable[[int], bool]

OWNER_CHECK_INTERVAL_SECONDS = 5.0


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
) -> int:
    """Keep a directed waiter armed until interrupted, displaced, or orphaned.

    With ``owner_pid`` set the waiter is leashed to that process: an owner
    watchdog polls it and, the moment it is gone, the armed wait is cancelled
    and the loop returns — a waiter for a closed terminal can wake nobody, and
    leaving it connected inflates the hub roster with phantom presence.

    A wait that reports a takeover (exit ``4``) also ends the loop: a newer
    waiter owns the name now, and re-arming would take it back and leave the
    two stealing the identity from each other until the hub quarantines it.
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
                wake_jitter=wake_jitter,
                agent_factory=agent_factory,
                token=token,
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
    """Dispatch the persistent ``arm`` subcommand."""
    for_name = args.for_name or args.name
    connect_name = args.name if args.name != for_name else waiter_name(args.name)
    try:
        return async_runner(
            arm_runner(
                uri=args.uri,
                name=connect_name,
                for_name=for_name,
                directed_only=args.directed_only,
                wake_jitter=args.wake_jitter,
                reconnect_delay=args.reconnect_delay,
                max_wakes=args.max_wakes,
                token=args.token,
                owner_pid=args.owner_pid,
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
        "--owner-pid",
        type=int,
        default=None,
        help=(
            "Disarm when this process exits. A shell hook passes its shell pid so a "
            "detached waiter cannot outlive the terminal it wakes."
        ),
    )
    arm.set_defaults(func=_cmd_arm)
