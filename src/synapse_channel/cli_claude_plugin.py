# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — optional Claude Code plugin onboarding command
"""Expose reversible plugin onboarding and the pinned host validator."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from synapse_channel.claude_plugin_install import (
    MIN_CORE_VERSION,
    PLUGIN_VERSION,
    TESTED_HOST_VERSION,
    ClaudePluginInstallError,
    apply_plugin,
    inspect_plugin,
    render_plugin,
)
from synapse_channel.client.agent import default_hub_uri


def _host_binary(explicit: str | None) -> str:
    """Resolve the host validator without invoking a model turn."""
    candidate = explicit or "claude"
    resolved = shutil.which(candidate)
    if resolved is None:
        raise ClaudePluginInstallError("Claude Code executable is unavailable")
    return str(Path(resolved).resolve())


def _validate(host: str, path: Path) -> bool:
    """Run the host's strict plugin validator on a complete plugin directory."""
    result = subprocess.run(
        [host, "plugin", "validate", "--strict", "--json", str(path)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    return result.returncode == 0


def _host_version(host: str) -> str:
    result = subprocess.run(
        [host, "--version"], capture_output=True, text=True, timeout=10, check=False
    )
    if result.returncode != 0:
        raise ClaudePluginInstallError("Claude Code version probe failed")
    return result.stdout.strip()


def _token_file_supported(binary: str) -> bool:
    """Probe the exact installed MCP command before configuring a token file."""
    result = subprocess.run(
        [binary, "mcp", "--help"], capture_output=True, text=True, timeout=10, check=False
    )
    return result.returncode == 0 and "--token-file" in result.stdout


def _core_supported(binary: str) -> bool:
    """Admit only an installed Synapse command at the plugin's minimum version."""
    result = subprocess.run(
        [binary, "--version"], capture_output=True, text=True, timeout=10, check=False
    )
    match = re.match(r"synapse-channel (\d+)\.(\d+)\.(\d+)", result.stdout.strip())
    minimum = tuple(int(part) for part in MIN_CORE_VERSION.split("."))
    return (
        result.returncode == 0 and match is not None and tuple(map(int, match.groups())) >= minimum
    )


def _command(args: argparse.Namespace) -> int:
    """Run one non-model plugin operation and print a secret-free JSON verdict."""
    root = Path(args.config_root).expanduser()
    action = args.action
    try:
        before = inspect_plugin(root)
        verdict: dict[str, object]
        if action == "inspect":
            verdict = {"state": before.state, "version": before.version, "path": str(before.path)}
        elif action == "diagnose":
            host = _host_binary(args.claude_bin)
            version = _host_version(host)
            valid = before.state == "owned" and _validate(host, before.path)
            verdict = {
                "state": before.state,
                "version": before.version,
                "host_version": version,
                "tested_host_version": TESTED_HOST_VERSION,
                "minimum_core_version": MIN_CORE_VERSION,
                "host_valid": valid,
            }
            print(json.dumps(verdict, sort_keys=True))
            return 0 if valid else 1
        elif action == "dry-run":
            if args.operation in {"install", "upgrade"}:
                render_plugin(
                    identity=args.identity,
                    uri=args.uri,
                    token_file=Path(args.token_file) if args.token_file else None,
                    synapse_bin=args.synapse_bin,
                )
            verdict = {
                "operation": args.operation,
                "state": before.state,
                "target": str(before.path),
                "plugin_version": PLUGIN_VERSION,
                "would_change": (
                    before.state == "absent"
                    if args.operation == "install"
                    else before.state == "owned"
                ),
            }
        else:
            if action in {"install", "upgrade"}:
                host = _host_binary(args.claude_bin)
                observed_host = _host_version(host)
                if TESTED_HOST_VERSION not in observed_host:
                    raise ClaudePluginInstallError(
                        f"Claude Code {observed_host} is unverified; "
                        f"tested host is {TESTED_HOST_VERSION}"
                    )
                binary = shutil.which(args.synapse_bin or "synapse")
                if binary is None or not _core_supported(binary):
                    raise ClaudePluginInstallError(
                        f"selected Synapse command must be version {MIN_CORE_VERSION} or newer"
                    )
                if args.token_file:
                    if not _token_file_supported(binary):
                        raise ClaudePluginInstallError(
                            "selected Synapse MCP command lacks --token-file support"
                        )
            result = apply_plugin(
                action,
                config_root=root,
                identity=args.identity,
                uri=args.uri,
                token_file=Path(args.token_file) if args.token_file else None,
                synapse_bin=args.synapse_bin,
                validate=(lambda path: _validate(host, path))
                if action in {"install", "upgrade"}
                else None,
            )
            verdict = {
                "action": action,
                "state": result.state,
                "version": result.version,
                "path": str(result.path),
            }
        print(json.dumps(verdict, sort_keys=True))
        return 0
    except (ClaudePluginInstallError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(f"Claude plugin operation failed: {exc}", file=sys.stderr)
        return 2


def add_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the optional Claude Code plugin command under ``adapters``."""
    parser = subparsers.add_parser(
        "claude-plugin", help="Inspect, validate and reversibly install the Claude Code plugin."
    )
    parser.add_argument(
        "action", choices=("inspect", "dry-run", "install", "upgrade", "diagnose", "uninstall")
    )
    parser.add_argument(
        "--config-root",
        default=os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude")),
        help="Claude Code profile root; defaults to CLAUDE_CONFIG_DIR or ~/.claude.",
    )
    parser.add_argument("--identity", default="", help="Exact project/seat hub identity.")
    parser.add_argument("--uri", default=default_hub_uri(), help="Hub WebSocket URI.")
    parser.add_argument("--token-file", default=None, help="Owner-only hub token file path.")
    parser.add_argument("--synapse-bin", default=None, help="Synapse executable path.")
    parser.add_argument("--claude-bin", default=None, help="Claude Code executable path.")
    parser.add_argument(
        "--operation",
        choices=("install", "upgrade", "uninstall"),
        default="install",
        help="Operation to preview with dry-run.",
    )
    parser.set_defaults(func=_command)
