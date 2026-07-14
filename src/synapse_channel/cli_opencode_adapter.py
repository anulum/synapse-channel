# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — OpenCode adapter installer and native claim-hook CLI
"""Install, inspect, remove, and execute the OpenCode integration bridge."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any

from synapse_channel.claim_state import fetch_state_snapshot
from synapse_channel.cli_claim_hook_common import (
    add_claim_hook_arguments,
    hook_timeout,
    normalise_ready_timeout,
    resolve_synapse_binary,
)
from synapse_channel.client.agent import default_hub_uri
from synapse_channel.file_claim_guard import GuardVerdict
from synapse_channel.opencode_adapter import (
    DEFAULT_MCP_TIMEOUT_MS,
    OpenCodeAdapterError,
    build_mcp_entry,
    parse_config,
    plan_config_install,
    plan_config_uninstall,
    plan_plugin_install,
    plan_plugin_uninstall,
    plugin_is_owned,
    resolve_opencode_paths,
)
from synapse_channel.opencode_adapter_files import (
    OpenCodeAdapterFileError,
    read_text_snapshot,
    remove_snapshot,
    write_text_snapshot,
)
from synapse_channel.opencode_claim_guard import MAX_HOOK_EVENT_BYTES, evaluate_hook_event
from synapse_channel.opencode_plugin import render_opencode_plugin


async def _evaluate(
    raw: str,
    *,
    identity: str,
    uri: str,
    token: str | None,
    timeout: float,
) -> GuardVerdict:
    return await evaluate_hook_event(
        raw,
        identity=identity,
        uri=uri,
        token=token,
        timeout=timeout,
        state_fetcher=fetch_state_snapshot,
    )


def _cmd_opencode_claim_hook(args: argparse.Namespace) -> int:
    """Emit exactly one explicit allow/deny verdict for the native plugin."""
    try:
        encoded = sys.stdin.buffer.read(MAX_HOOK_EVENT_BYTES + 1)
        if len(encoded) > MAX_HOOK_EVENT_BYTES:
            raise ValueError("OpenCode hook input exceeds its bounded limit.")
        raw = encoded.decode("utf-8", errors="strict")
        verdict = asyncio.run(
            _evaluate(
                raw,
                identity=args.identity,
                uri=args.uri,
                token=args.token,
                timeout=normalise_ready_timeout(float(args.ready_timeout)),
            )
        )
    except Exception:
        verdict = GuardVerdict(False, "Synapse claim verification failed closed.")
    payload: dict[str, object] = {"allowed": verdict.allowed}
    if not verdict.allowed:
        payload["reason"] = verdict.reason
    print(json.dumps(payload, ensure_ascii=False))
    return 0


def _roots(args: argparse.Namespace) -> tuple[Path, Path, Path | None]:
    home = Path(args.home).expanduser() if args.home else Path.home()
    project = Path(args.project).expanduser() if args.project else Path.cwd()
    config_root = Path(args.config_root).expanduser() if args.config_root else None
    return home, project, config_root


def _private_token_file(raw: str | None) -> str | None:
    if raw is None:
        return None
    path = Path(raw).expanduser().resolve(strict=True)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
        raise OpenCodeAdapterError("OpenCode token file must be a user-owned regular file.")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise OpenCodeAdapterError("OpenCode token file must not be accessible by group or others.")
    return str(path)


def _render_assets(args: argparse.Namespace) -> tuple[dict[str, Any], str]:
    if args.token and not args.token_file:
        raise OpenCodeAdapterError(
            "OpenCode persistent config never embeds --token; use an owner-only --token-file."
        )
    synapse_bin = resolve_synapse_binary(args.synapse_bin)
    token_file = _private_token_file(args.token_file)
    timeout = normalise_ready_timeout(float(args.ready_timeout))
    hook_argv = [
        synapse_bin,
        "adapters",
        "opencode-claim-hook",
        "--identity",
        args.identity,
        "--uri",
        args.uri,
        "--ready-timeout",
        str(timeout),
    ]
    if token_file:
        hook_argv.extend(["--token-file", token_file])
    plugin = render_opencode_plugin(
        hook_argv=hook_argv,
        timeout_seconds=float(hook_timeout(timeout)),
    )
    entry = build_mcp_entry(
        synapse_bin=synapse_bin,
        identity=args.identity,
        uri=args.uri,
        token_file=token_file,
        timeout_ms=args.mcp_timeout_ms,
    )
    return entry, plugin


def _cmd_install(args: argparse.Namespace) -> int:
    """Install or update the owned plugin and MCP entry."""
    try:
        home, project, config_root = _roots(args)
        paths = resolve_opencode_paths(
            scope=args.scope, project=project, home=home, config_root=config_root
        )
        entry, rendered_plugin = _render_assets(args)
        config_snapshot = read_text_snapshot(paths.config)
        plugin_snapshot = read_text_snapshot(paths.plugin)
        config = plan_config_install(config_snapshot.text, entry)
        plugin = plan_plugin_install(plugin_snapshot.text, rendered_plugin)
        if args.dry_run:
            print(f"would write OpenCode plugin: {paths.plugin}")
            print(f"would write OpenCode MCP config: {paths.config}")
            return 0
        if plugin != plugin_snapshot.text:
            write_text_snapshot(paths.plugin, plugin, plugin_snapshot)
        if config != config_snapshot.text:
            write_text_snapshot(paths.config, config, config_snapshot)
    except (OSError, ValueError, OpenCodeAdapterError, OpenCodeAdapterFileError) as exc:
        print(f"cannot install OpenCode adapter: {exc}", file=sys.stderr)
        return 2
    print(f"installed OpenCode claim guard: {paths.plugin}")
    print(f"installed OpenCode Synapse MCP entry: {paths.config}")
    return 0


def _cmd_uninstall(args: argparse.Namespace) -> int:
    """Remove only files and config entries carrying Synapse ownership markers."""
    try:
        home, project, config_root = _roots(args)
        paths = resolve_opencode_paths(
            scope=args.scope, project=project, home=home, config_root=config_root
        )
        config_snapshot = read_text_snapshot(paths.config)
        plugin_snapshot = read_text_snapshot(paths.plugin)
        config = plan_config_uninstall(config_snapshot.text)
        plan_plugin_uninstall(plugin_snapshot.text)
        if config_snapshot.existed:
            if config is None:
                remove_snapshot(paths.config, config_snapshot)
            elif config != config_snapshot.text:
                write_text_snapshot(paths.config, config, config_snapshot)
        if plugin_snapshot.existed:
            remove_snapshot(paths.plugin, plugin_snapshot)
    except (OSError, ValueError, OpenCodeAdapterError, OpenCodeAdapterFileError) as exc:
        print(f"cannot uninstall OpenCode adapter: {exc}", file=sys.stderr)
        return 2
    print(f"removed Synapse-owned OpenCode adapter content from {paths.config.parent}")
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    """Report owned config and plugin presence without changing either file."""
    try:
        home, project, config_root = _roots(args)
        paths = resolve_opencode_paths(
            scope=args.scope, project=project, home=home, config_root=config_root
        )
        config_snapshot = read_text_snapshot(paths.config)
        plugin_snapshot = read_text_snapshot(paths.plugin)
        config = parse_config(config_snapshot.text)
        mcp = config.get("mcp")
        if mcp is not None and not isinstance(mcp, dict):
            raise OpenCodeAdapterError("OpenCode config field 'mcp' must be an object.")
        entry = mcp.get("synapse") if isinstance(mcp, dict) else None
        config_owned = (
            isinstance(entry, dict)
            and isinstance(entry.get("environment"), dict)
            and entry["environment"].get("SYNAPSE_ADAPTER_OWNER") == "synapse-channel"
        )
        plugin_owned = plugin_snapshot.existed and plugin_is_owned(plugin_snapshot.text)
    except (OSError, ValueError, json.JSONDecodeError, OpenCodeAdapterFileError) as exc:
        print(f"cannot inspect OpenCode adapter: {exc}", file=sys.stderr)
        return 2
    print(f"config: {'installed' if config_owned else 'absent'} ({paths.config})")
    print(f"plugin: {'installed' if plugin_owned else 'absent'} ({paths.plugin})")
    return 0 if config_owned == plugin_owned else 1


def _cmd_print_config(args: argparse.Namespace) -> int:
    """Print the exact mergeable MCP entry or native plugin source."""
    try:
        entry, plugin = _render_assets(args)
    except (OSError, ValueError, OpenCodeAdapterError) as exc:
        print(f"cannot render OpenCode adapter: {exc}", file=sys.stderr)
        return 2
    if args.asset == "plugin":
        print(plugin, end="")
    else:
        print(json.dumps({"mcp": {"synapse": entry}}, indent=2, ensure_ascii=False))
    return 0


def _add_path_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--scope", choices=("project", "global"), default="project")
    parser.add_argument("--project", default=None, help="Project root for project scope.")
    parser.add_argument("--home", default=None, help="Home root for global scope.")
    parser.add_argument("--config-root", default=None, help="Override XDG_CONFIG_HOME.")


def _add_render_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--identity", required=True, help="Exact Synapse claim owner.")
    parser.add_argument("--uri", default=default_hub_uri(), help="Authoritative hub URI.")
    parser.add_argument("--token", default=None, help="Runtime-only token; never persisted.")
    parser.add_argument("--token-file", default=None, help="Owner-only hub token file path.")
    parser.add_argument("--synapse-bin", default=None, help="Synapse executable to resolve.")
    parser.add_argument("--ready-timeout", type=float, default=2.0)
    parser.add_argument("--mcp-timeout-ms", type=int, default=DEFAULT_MCP_TIMEOUT_MS)


def add_opencode_claim_hook_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Register ``adapters opencode-claim-hook`` for the native plugin."""
    parser = subparsers.add_parser(
        "opencode-claim-hook",
        help="Guard OpenCode edit/write/apply_patch calls with live claims.",
    )
    add_claim_hook_arguments(parser)
    parser.set_defaults(func=_cmd_opencode_claim_hook)


def add_opencode_adapter_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Register the ``adapters opencode`` lifecycle command group."""
    parser = subparsers.add_parser("opencode", help="Manage the OpenCode integration bridge.")
    group = parser.add_subparsers(dest="opencode_command", required=True)

    installer = group.add_parser("install", help="Install or update owned OpenCode assets.")
    _add_path_arguments(installer)
    _add_render_arguments(installer)
    installer.add_argument("--dry-run", action="store_true")
    installer.set_defaults(func=_cmd_install)

    remover = group.add_parser("uninstall", help="Remove only Synapse-owned OpenCode assets.")
    _add_path_arguments(remover)
    remover.set_defaults(func=_cmd_uninstall)

    status = group.add_parser("status", help="Inspect OpenCode adapter ownership and parity.")
    _add_path_arguments(status)
    status.set_defaults(func=_cmd_status)

    printer = group.add_parser("print-config", help="Print an MCP fragment or plugin source.")
    _add_render_arguments(printer)
    printer.add_argument("--asset", choices=("config", "plugin"), default="config")
    printer.set_defaults(func=_cmd_print_config)
