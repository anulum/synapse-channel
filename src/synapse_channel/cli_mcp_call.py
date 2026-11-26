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
import sys
from pathlib import Path

from synapse_channel.core.error_boundaries import cli_exit_code_for_error
from synapse_channel.core.mcp_outbound import (
    McpAccessError,
    McpConfigError,
    McpToolDescriptorError,
    McpToolError,
    OutboundMcpClient,
    load_outbound_config,
)
from synapse_channel.terminal_text import terminal_text


def _build_client(
    config_path: str,
    *,
    trust_bundle_path: str | None = None,
    allow_repo_config: bool = False,
) -> OutboundMcpClient:
    """Load trusted execution policy and build an outbound client."""
    return OutboundMcpClient(
        load_outbound_config(
            config_path,
            trust_bundle_path=trust_bundle_path,
            allow_repo_config=allow_repo_config,
        )
    )


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


def _notice_unpinned(client: OutboundMcpClient, server: str) -> None:
    """Tell the operator once, on standard error, that a server's tools are not pinned."""
    if not client.pinned(server):
        notice = "tool descriptors are not pinned; see 'synapse mcp-pin'"
        print(f"server {terminal_text(server)}: {notice}", file=sys.stderr)


def _cmd_mcp_tools(args: argparse.Namespace) -> int:
    """List the allowlisted tools an MCP server advertises.

    For a pinned server each tool shows its pin status. The exit code is the descriptor-refusal
    one when any pinned tool changed or is missing, after the whole table is printed, so every
    drift is seen at once.
    """
    try:
        client = _build_client(
            args.config,
            trust_bundle_path=args.config_trust_bundle,
            allow_repo_config=args.allow_repo_mcp_config,
        )
        _notice_unpinned(client, args.server)
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
            status = "" if tool.get("pin", "unpinned") == "unpinned" else f" [pin: {tool['pin']}]"
            print(f"  {terminal_text(tool['name'])}{status}: {terminal_text(tool['description'])}")
    drifted = [tool["name"] for tool in tools if tool.get("pin") in {"changed", "missing"}]
    if drifted:
        drift = McpToolDescriptorError(f"{len(drifted)} pinned tool(s) changed or missing")
        print(f"mcp-tools error: {terminal_text(drift)}")
        return cli_exit_code_for_error(drift, default=2)
    return 0


def _cmd_mcp_call(args: argparse.Namespace) -> int:
    """Call one allowlisted MCP tool and print its result."""
    try:
        arguments = _parse_arguments(args.arg, args.args_json)
        client = _build_client(
            args.config,
            trust_bundle_path=args.config_trust_bundle,
            allow_repo_config=args.allow_repo_mcp_config,
        )
        _notice_unpinned(client, args.server)
        result = asyncio.run(client.call_tool(args.server, args.tool, arguments))
    except (McpConfigError, McpAccessError, McpToolError, json.JSONDecodeError) as exc:
        print(f"mcp-call error: {terminal_text(exc)}")
        return cli_exit_code_for_error(exc, default=2)
    except RuntimeError as exc:
        print(f"mcp-call error: {terminal_text(exc)}")
        return 2
    print(terminal_text(result))
    return 0


def _cmd_mcp_pin(args: argparse.Namespace) -> int:
    """Print the canonical descriptor and pin of a server's tools, writing no config.

    The pin is the approval: reading the printed descriptor before pinning is the review the
    pin records. ``--descriptor-out`` keeps the canonical descriptors in a new file, so a later
    ``changed`` can be compared with what was approved.
    """
    try:
        client = _build_client(
            args.config,
            trust_bundle_path=args.config_trust_bundle,
            allow_repo_config=args.allow_repo_mcp_config,
        )
        described = asyncio.run(client.pin_descriptors(args.server, tuple(args.tool)))
        if args.descriptor_out:
            records = {name: json.loads(raw) for name, raw, _digest in described}
            with Path(args.descriptor_out).open("x", encoding="utf-8") as handle:
                handle.write(json.dumps(records, indent=2, sort_keys=True) + "\n")
    except (McpConfigError, McpAccessError, McpToolError, FileExistsError) as exc:
        print(f"mcp-pin error: {terminal_text(exc)}")
        return cli_exit_code_for_error(exc, default=2)
    except RuntimeError as exc:
        print(f"mcp-pin error: {terminal_text(exc)}")
        return 2
    for name, raw, digest in described:
        print(f"tool {terminal_text(name)}")
        print(f"  descriptor: {terminal_text(raw.decode('utf-8'))}")
        print(f"  sha256: {digest}")
    pins = {name: digest for name, _raw, digest in described}
    print('"tool_pins": ' + json.dumps(pins, indent=2, sort_keys=True))
    return 0


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``mcp-tools``, ``mcp-call`` and ``mcp-pin`` subcommands."""
    tools = subparsers.add_parser(
        "mcp-tools", help="List the allowlisted tools of an external MCP server."
    )
    tools.add_argument("server", help="Server name from the allowlist config.")
    _add_config_trust_arguments(tools)
    tools.add_argument("--json", action="store_true", help="Emit the tool list as JSON.")
    tools.set_defaults(func=_cmd_mcp_tools)

    call = subparsers.add_parser(
        "mcp-call", help="Call one allowlisted tool on an external MCP server."
    )
    call.add_argument("server", help="Server name from the allowlist config.")
    call.add_argument("tool", help="Tool name to call (must be allowlisted).")
    _add_config_trust_arguments(call)
    call.add_argument(
        "--arg", action="append", default=[], metavar="KEY=VALUE", help="A tool argument."
    )
    call.add_argument("--args-json", default="", help="A full JSON object of tool arguments.")
    call.set_defaults(func=_cmd_mcp_call)

    pin = subparsers.add_parser(
        "mcp-pin",
        help="Print the descriptor pins of an external MCP server's tools, writing no config.",
    )
    pin.add_argument("server", help="Server name from the allowlist config.")
    _add_config_trust_arguments(pin)
    pin.add_argument(
        "--tool",
        action="append",
        default=[],
        metavar="NAME",
        help="A tool to describe; every advertised tool when omitted.",
    )
    pin.add_argument(
        "--descriptor-out",
        default="",
        metavar="FILE",
        help="Also write the canonical descriptors to this new file (never overwritten).",
    )
    pin.set_defaults(func=_cmd_mcp_pin)


def _add_config_trust_arguments(parser: argparse.ArgumentParser) -> None:
    """Add the shared outbound MCP execution-policy trust flags."""
    parser.add_argument(
        "--config",
        required=True,
        help="Owner-only outbound MCP policy JSON outside the active repository.",
    )
    parser.add_argument(
        "--config-trust-bundle",
        default=None,
        metavar="FILE",
        help=(
            "Owner-only Ed25519 trust bundle; when supplied, the config must carry "
            "a valid signed manifest."
        ),
    )
    parser.add_argument(
        "--allow-repo-mcp-config",
        action="store_true",
        help=(
            "Explicitly accept an owner-only MCP config/trust bundle inside the active "
            "repository (unsafe when repository agents can write it)."
        ),
    )
