# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — `synapse seat-hold`: run an agent only while its seat is held
"""``synapse seat-hold``: run an agent only while ``<identity>-seat`` is held, never by takeover.

See :mod:`synapse_channel.seat_hold` for the fence. The command prints one
line when the seat cannot start and otherwise exits with the agent's status.
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

from synapse_channel.client.agent import default_hub_uri
from synapse_channel.seat_hold import hold_seat

AsyncRunner = Callable[[Coroutine[Any, Any, int]], int]
"""Runs the holder's coroutine to completion; injectable for tests."""

USAGE_EXIT_CODE = 2
"""Exit code for a command line without an agent command, or with an unreadable token file."""


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``seat-hold``.

    Parameters
    ----------
    subparsers : argparse._SubParsersAction
        The top-level subcommand registry.
    """
    parser = subparsers.add_parser(
        "seat-hold",
        help="Run an agent only while <identity>-seat is held; never evict a live holder.",
    )
    parser.add_argument("--identity", required=True, help="The seat's Synapse identity.")
    parser.add_argument(
        "--nonce",
        required=True,
        help="This start's fresh token (8-64 letters, digits, '_' or '-'); names the marker.",
    )
    parser.add_argument(
        "--marker-dir",
        default=None,
        help="Directory for the private readiness marker; the runtime directory by default.",
    )
    parser.add_argument("--uri", default=default_hub_uri())
    parser.add_argument(
        "--token-file",
        default=None,
        help="File holding the shared-secret token for a secured hub.",
    )
    parser.add_argument("command", nargs=argparse.REMAINDER, help="The agent command, after '--'.")
    parser.set_defaults(func=_cmd_seat_hold)


def _cmd_seat_hold(args: argparse.Namespace, *, runner: AsyncRunner = asyncio.run) -> int:
    """Run ``seat-hold`` for parsed ``args``; return the seat's exit status."""
    command = list(args.command)
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        print("synapse seat-hold: give the agent command after '--'")
        return USAGE_EXIT_CODE
    try:
        token = (
            Path(args.token_file).read_text(encoding="utf-8").strip() if args.token_file else None
        )
    except OSError as exc:
        reason = exc.strerror or type(exc).__name__
        print(f"synapse seat-hold: cannot read the token file {args.token_file}: {reason}")
        return USAGE_EXIT_CODE
    return runner(
        hold_seat(
            args.identity,
            args.nonce,
            command,
            uri=args.uri,
            token=token or None,
            marker_dir=Path(args.marker_dir) if args.marker_dir else None,
        )
    )
