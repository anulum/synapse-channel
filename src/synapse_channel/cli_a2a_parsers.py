# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — A2A CLI parser registration
"""Parser registration for Agent2Agent bridge commands."""

from __future__ import annotations

import argparse

from synapse_channel.cli_a2a_card import _cmd_a2a_card
from synapse_channel.cli_a2a_interop import add_parsers as add_interop_parsers
from synapse_channel.cli_a2a_serve import _cmd_a2a_serve
from synapse_channel.client.agent import default_hub_uri


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register A2A bridge subcommands."""
    add_interop_parsers(subparsers)
    card = subparsers.add_parser(
        "a2a-card",
        help="Print an A2A Agent Card projected from the live SYNAPSE capability manifest.",
    )
    card.add_argument("--uri", default=default_hub_uri())
    card.add_argument("--name", default="A2A-BRIDGE")
    card.add_argument("--token", default=None, help="Shared-secret token for a secured hub.")
    card.add_argument(
        "--endpoint-url",
        required=True,
        help="Absolute URL of the A2A bridge endpoint advertised in the Agent Card.",
    )
    card.add_argument("--bridge-name", default="SYNAPSE CHANNEL")
    card.add_argument("--description", default=None)
    card.add_argument(
        "--documentation-url",
        default="https://anulum.github.io/synapse-channel",
    )
    card.add_argument(
        "--bearer-auth",
        action="store_true",
        help="Declare HTTP Bearer authentication for the advertised A2A endpoint.",
    )
    card.set_defaults(func=_cmd_a2a_card)

    serve = subparsers.add_parser(
        "a2a-serve",
        help="Run the stdlib HTTP+JSON A2A bridge for discovery, messages, and tasks.",
    )
    serve.add_argument("--uri", default=default_hub_uri())
    serve.add_argument("--name", default="A2A-BRIDGE")
    serve.add_argument("--token", default=None, help="Shared-secret token for a secured hub.")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8877)
    serve.add_argument(
        "--endpoint-url",
        required=True,
        help="Absolute URL of this A2A bridge endpoint as clients will reach it.",
    )
    serve.add_argument(
        "--target",
        default="all",
        help="Default SYNAPSE target for A2A messages without metadata.target.",
    )
    serve.add_argument("--bridge-name", default="SYNAPSE CHANNEL")
    serve.add_argument("--description", default=None)
    serve.add_argument(
        "--documentation-url",
        default="https://anulum.github.io/synapse-channel",
    )
    serve.add_argument(
        "--bearer-auth",
        action="store_true",
        help="Declare HTTP Bearer authentication for the advertised A2A endpoint.",
    )
    serve.add_argument(
        "--a2a-token",
        default=None,
        help="Bearer token required by protected A2A bridge routes.",
    )
    serve.add_argument(
        "--insecure-off-loopback",
        action="store_true",
        help="Allow a non-loopback A2A bind without bearer authentication.",
    )
    serve.add_argument(
        "--state-file",
        default=None,
        help="Optional JSON state file for persisted A2A tasks and push configs.",
    )
    serve.add_argument(
        "--task-timeout",
        type=float,
        default=300.0,
        help="Seconds before an open A2A task is marked failed while awaiting a SYNAPSE reply.",
    )
    serve.add_argument(
        "--subscribe-timeout",
        type=float,
        default=0.0,
        help="Seconds a task subscription waits for one queued lifecycle update.",
    )
    serve.set_defaults(func=_cmd_a2a_serve)
