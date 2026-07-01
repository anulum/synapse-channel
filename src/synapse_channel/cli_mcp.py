# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — Model Context Protocol bridge CLI command (mcp)
"""The Model-Context-Protocol bridge ``synapse`` subcommand.

``mcp`` runs an MCP server over stdio that exposes the hub's coordination verbs
to any MCP client, bridging a stdio transport to a live hub. It depends on the
optional ``mcp`` extra and on the stdio bridge in :mod:`synapse_channel.mcp`, so
it is kept apart from the in-process hub-client command flows;
:func:`add_parsers` registers its subparser on the top-level CLI.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from synapse_channel.client.agent import default_hub_uri
from synapse_channel.mcp.server import DEFAULT_BRIDGE_NAME, DEFAULT_REQUEST_TIMEOUT, serve_stdio


def _cmd_mcp(args: argparse.Namespace) -> int:
    """Run the Model Context Protocol server over stdio, bridged to the hub.

    Exposes the hub's coordination verbs to any MCP client. Requires the optional
    ``mcp`` extra; a missing extra is reported with the install hint and exit ``1``.
    """
    try:
        return asyncio.run(
            serve_stdio(
                uri=args.uri,
                name=args.name,
                token=args.token,
                request_timeout=args.request_timeout,
                ready_timeout=args.ready_timeout,
            )
        )
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(f"\n[{args.name}] MCP server stopped.")
        return 0


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``mcp`` subparser on the top-level CLI."""
    mcp = subparsers.add_parser(
        "mcp",
        help="Run an MCP server over stdio that bridges to the hub (needs the [mcp] extra).",
    )
    mcp.add_argument("--uri", default=default_hub_uri())
    mcp.add_argument("--name", default=DEFAULT_BRIDGE_NAME)
    mcp.add_argument("--token", default=None, help="Shared-secret token for a secured hub.")
    mcp.add_argument(
        "--request-timeout",
        type=float,
        default=DEFAULT_REQUEST_TIMEOUT,
        help="Seconds to await a hub reply before reporting no response.",
    )
    mcp.add_argument(
        "--ready-timeout",
        type=float,
        default=5.0,
        help="Seconds to wait for the hub handshake before reporting it unreachable.",
    )
    mcp.set_defaults(func=_cmd_mcp)
