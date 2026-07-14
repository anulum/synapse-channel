# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — outbound MCP tool CLI (list and call allowlisted tools)
"""``synapse mcp-tools`` and ``synapse mcp-call`` — call external MCP tools.

These commands let a Synapse operator list and invoke tools on an external MCP
server named in a deny-by-default allowlist config. A server or tool that is not
allowlisted is refused before the server is contacted. They are the outbound
counterpart to ``synapse mcp``, which serves the hub to MCP clients.
"""

from __future__ import annotations

import argparse
import asyncio
import json

from synapse_channel.core.error_boundaries import cli_exit_code_for_error
from synapse_channel.core.mcp_outbound import (
    McpAccessError,
    McpConfigError,
    McpToolError,
    OutboundMcpClient,
    load_outbound_config,
)
from synapse_channel.terminal_text import terminal_text


def _build_client(config_path: str) -> OutboundMcpClient:
    """Load the allowlist config and build an outbound client."""
    return OutboundMcpClient(load_outbound_config(config_path))


def _parse_arguments(pairs: list[str], args_json: str) -> dict[str, object]:
    """Build a tool argument object from ``--arg k=v`` pairs and ``--args-json``.

    Each ``k=v`` value is JSON-decoded when possible (so ``count=5`` is the number
    five) and kept as a string otherwise. ``--args-json`` provides a full object
    that the individual pairs then override.
    """
    arguments: dict[str, object] = {}
    if args_json:
        loaded = json.loads(args_json)
        if not isinstance(loaded, dict):
            raise McpConfigError("--args-json must be a JSON object")
        arguments.update(loaded)
    for pair in pairs:
        key, separator, value = pair.partition("=")
        if not separator:
            raise McpConfigError(f"--arg must be key=value, got {pair!r}")
        try:
            arguments[key] = json.loads(value)
        except json.JSONDecodeError:
            arguments[key] = value
    return arguments


def _cmd_mcp_tools(args: argparse.Namespace) -> int:
    """List the allowlisted tools an MCP server advertises."""
    try:
        client = _build_client(args.config)
        tools = asyncio.run(client.list_tools(args.server))
    except (McpConfigError, McpAccessError, McpToolError) as exc:
        print(f"mcp-tools error: {terminal_text(exc)}")
        return cli_exit_code_for_error(exc, default=2)
    except RuntimeError as exc:
        print(f"mcp-tools error: {terminal_text(exc)}")
        return 2
    if args.json:
        print(json.dumps(tools, indent=2, sort_keys=True))
    else:
        print(f"{terminal_text(args.server)}: {len(tools)} allowed tool(s)")
        for tool in tools:
            print(f"  {terminal_text(tool['name'])}: {terminal_text(tool['description'])}")
    return 0


def _cmd_mcp_call(args: argparse.Namespace) -> int:
    """Call one allowlisted MCP tool and print its result."""
    try:
        arguments = _parse_arguments(args.arg, args.args_json)
        client = _build_client(args.config)
        result = asyncio.run(client.call_tool(args.server, args.tool, arguments))
    except (McpConfigError, McpAccessError, McpToolError, json.JSONDecodeError) as exc:
        print(f"mcp-call error: {terminal_text(exc)}")
        return cli_exit_code_for_error(exc, default=2)
    except RuntimeError as exc:
        print(f"mcp-call error: {terminal_text(exc)}")
        return 2
    print(terminal_text(result))
    return 0


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``mcp-tools`` and ``mcp-call`` subcommands."""
    tools = subparsers.add_parser(
        "mcp-tools", help="List the allowlisted tools of an external MCP server."
    )
    tools.add_argument("server", help="Server name from the allowlist config.")
    tools.add_argument("--config", required=True, help="Outbound MCP allowlist JSON config.")
    tools.add_argument("--json", action="store_true", help="Emit the tool list as JSON.")
    tools.set_defaults(func=_cmd_mcp_tools)

    call = subparsers.add_parser(
        "mcp-call", help="Call one allowlisted tool on an external MCP server."
    )
    call.add_argument("server", help="Server name from the allowlist config.")
    call.add_argument("tool", help="Tool name to call (must be allowlisted).")
    call.add_argument("--config", required=True, help="Outbound MCP allowlist JSON config.")
    call.add_argument(
        "--arg", action="append", default=[], metavar="KEY=VALUE", help="A tool argument."
    )
    call.add_argument("--args-json", default="", help="A full JSON object of tool arguments.")
    call.set_defaults(func=_cmd_mcp_call)
