# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — argparse registration for read-only hub query commands
"""Parser registration for read-only hub query CLI subcommands."""

from __future__ import annotations

import argparse

from synapse_channel.cli_query_commands import (
    _cmd_board,
    _cmd_health,
    _cmd_manifest,
    _cmd_state,
    _cmd_who,
)
from synapse_channel.client.agent import DEFAULT_HUB_URI


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``who``, ``health``, ``state``, ``board``, and ``manifest`` subparsers."""
    who = subparsers.add_parser(
        "who", help="List the agents currently online (optionally one project's)."
    )
    who.add_argument("--uri", default=DEFAULT_HUB_URI)
    who.add_argument("--name", default="USER")
    who.add_argument(
        "--project",
        default=None,
        help="Show only agents in this project (matches 'project' or 'project/...').",
    )
    who.add_argument(
        "--me",
        action="store_true",
        help="Show this identity's presence and -rx waiter status instead of the full roster.",
    )
    who.add_argument("--token", default=None, help="Shared-secret token for a secured hub.")
    who.add_argument(
        "--ready-timeout", type=float, default=5.0, help="Seconds to await hub readiness."
    )
    who.set_defaults(func=_cmd_who)

    health = subparsers.add_parser("health", help="Probe the hub; exit 0 if reachable, 1 if not.")
    health.add_argument("--uri", default=DEFAULT_HUB_URI)
    health.add_argument("--name", default="HEALTH")
    health.add_argument("--token", default=None, help="Shared-secret token for a secured hub.")
    health.add_argument(
        "--ready-timeout", type=float, default=5.0, help="Seconds to await hub readiness."
    )
    health.set_defaults(func=_cmd_health)

    state = subparsers.add_parser(
        "state", help="Print active claims and their checkpoints (a resume view)."
    )
    state.add_argument("--uri", default=DEFAULT_HUB_URI)
    state.add_argument("--name", default="USER")
    state.add_argument(
        "--owner",
        default=None,
        help="Show only claims owned by this name or project (matches 'owner' or 'owner/...').",
    )
    state.add_argument("--token", default=None, help="Shared-secret token for a secured hub.")
    state.add_argument(
        "--ready-timeout", type=float, default=5.0, help="Seconds to await hub readiness."
    )
    state.set_defaults(func=_cmd_state)

    board = subparsers.add_parser("board", help="Print the hub's shared task/progress board.")
    board.add_argument("--uri", default=DEFAULT_HUB_URI)
    board.add_argument("--name", default="USER")
    board.add_argument("--token", default=None, help="Shared-secret token for a secured hub.")
    board.add_argument(
        "--ready-timeout", type=float, default=5.0, help="Seconds to await hub readiness."
    )
    board.set_defaults(func=_cmd_board)

    manifest = subparsers.add_parser("manifest", help="Print the capability manifest of agents.")
    manifest.add_argument("--uri", default=DEFAULT_HUB_URI)
    manifest.add_argument("--name", default="USER")
    manifest.add_argument("--token", default=None, help="Shared-secret token for a secured hub.")
    manifest.add_argument(
        "--ready-timeout", type=float, default=5.0, help="Seconds to await hub readiness."
    )
    manifest.set_defaults(func=_cmd_manifest)
