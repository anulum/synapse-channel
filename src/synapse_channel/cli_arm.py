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

from synapse_channel.cli_messaging import AgentFactory, _wait
from synapse_channel.client.agent import DEFAULT_HUB_URI, SynapseAgent


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
    token: str | None = None,
) -> int:
    """Keep a directed waiter armed until interrupted."""
    wakes_seen = 0
    while max_wakes is None or wakes_seen < max_wakes:
        code = await _wait(
            uri=uri,
            name=name,
            for_name=for_name,
            timeout=0.0,
            directed_only=directed_only,
            wake_jitter=wake_jitter,
            agent_factory=agent_factory,
            token=token,
        )
        if code == 0:
            wakes_seen += 1
            continue
        if reconnect_delay > 0:
            await asyncio.sleep(reconnect_delay)
    return 0


def _cmd_arm(args: argparse.Namespace) -> int:
    """Dispatch the persistent ``arm`` subcommand."""
    for_name = args.for_name or args.name
    connect_name = args.name if args.name != for_name else f"{args.name}-rx"
    try:
        return asyncio.run(
            _arm(
                uri=args.uri,
                name=connect_name,
                for_name=for_name,
                directed_only=args.directed_only,
                wake_jitter=args.wake_jitter,
                reconnect_delay=args.reconnect_delay,
                max_wakes=args.max_wakes,
                token=args.token,
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
    arm.add_argument("--uri", default=DEFAULT_HUB_URI)
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
    arm.set_defaults(func=_cmd_arm)
