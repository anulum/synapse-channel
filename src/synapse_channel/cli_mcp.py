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
from collections.abc import Callable, Sequence

from synapse_channel.client.agent import default_hub_uri
from synapse_channel.mcp.onboarding import resolve_mcp_identity
from synapse_channel.mcp.server import DEFAULT_REQUEST_TIMEOUT, serve_stdio

CliDispatcher = Callable[[list[str] | None], int]
"""Top-level CLI dispatcher used by the dedicated registry entry point."""


def _cmd_mcp(args: argparse.Namespace) -> int:
    """Run the Model Context Protocol server over stdio, bridged to the hub.

    Exposes the hub's coordination verbs to any MCP client. Requires the optional
    ``mcp`` extra; a missing extra is reported with the install hint and exit ``1``.
    """
    try:
        identity = resolve_mcp_identity(getattr(args, "name", None))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if identity.note:
        print(f"[{identity.name}] note: {identity.note}", file=sys.stderr)
    print(
        f"[{identity.name}] MCP bridge identity resolved from {identity.source}",
        file=sys.stderr,
    )
    try:
        return asyncio.run(
            serve_stdio(
                uri=args.uri,
                name=identity.name,
                token=args.token,
                request_timeout=args.request_timeout,
                ready_timeout=args.ready_timeout,
                roles=tuple(getattr(args, "role", None) or ()),
                inbox_feed=getattr(args, "inbox_feed", None),
                inbox_cursor=getattr(args, "inbox_cursor", None),
            )
        )
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(f"\n[{identity.name}] MCP server stopped.")
        return 0


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``mcp`` subparser on the top-level CLI."""
    mcp = subparsers.add_parser(
        "mcp",
        help="Run an MCP server over stdio that bridges to the hub (needs the [mcp] extra).",
    )
    mcp.add_argument("--uri", default=default_hub_uri())
    mcp.add_argument(
        "--name",
        default=None,
        help=(
            "Exact hub identity. Without it, use an agreeing SYN_PROJECT/SYN_IDENTITY; "
            "otherwise resolve <git-project>/mcp."
        ),
    )
    mcp.add_argument("--token", default=None, help="Shared-secret token for a secured hub.")
    mcp.add_argument(
        "--role",
        action="append",
        default=None,
        help="Full <project>/<role> identity this bridge answers to (repeatable).",
    )
    mcp.add_argument(
        "--inbox-feed",
        default=None,
        help="Local durable relay feed for synapse_inbox (default: $SYN_HOME/feed.ndjson).",
    )
    mcp.add_argument(
        "--inbox-cursor",
        default=None,
        help="Owner-local byte cursor for synapse_inbox (default: per resolved identity).",
    )
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


def main(
    argv: Sequence[str] | None = None,
    *,
    dispatcher: CliDispatcher | None = None,
) -> int:
    """Run the MCP face through the dedicated ``synapse-channel`` entry point.

    Parameters
    ----------
    argv : Sequence[str] or None, optional
        MCP arguments, defaulting to ``sys.argv[1:]``.
    dispatcher : CliDispatcher or None, optional
        Top-level Synapse dispatcher. Imported lazily when omitted to avoid a
        parser-registration cycle.

    Returns
    -------
    int
        Exit code from ``synapse mcp``.
    """
    if dispatcher is None:
        from synapse_channel import cli

        dispatcher = cli.main
    arguments = list(sys.argv[1:] if argv is None else argv)
    return dispatcher(["mcp", *arguments])
