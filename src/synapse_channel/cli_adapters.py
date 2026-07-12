# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — `synapse adapters` CLI: detect coding tools and wire them to the hub
"""``synapse adapters`` — detect installed coding tools and wire them to the hub.

``list`` detects the tools on the machine and reports where each adapter would be
written and whether one is installed; it writes nothing. ``install`` writes the
thin claim-aware adapter into each detected (or named) tool's native config, idempotently
and clearly marked; ``--dry-run`` prints the planned writes instead. ``uninstall``
removes only the Synapse-written content, leaving the tool's own configuration intact.

The policy — which tools exist, where their files live, and how a block is added or
removed — is the pure :mod:`synapse_channel.adapters` module. This file is the thin
I/O shell: it resolves paths under an injectable home/project, reads and writes files,
and renders the outcome. The filesystem roots and the ``PATH`` probe are injectable so
the commands are testable against a temporary tree.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from collections.abc import Callable, Mapping
from pathlib import Path

from synapse_channel.adapters import (
    CATALOGUE,
    AdapterTool,
    contains_block,
    detect_installed,
    plan_install,
    plan_uninstall,
    render_block,
    resolve_target,
    tool_for,
)
from synapse_channel.cli_claude_claim_hook import add_parser as add_claude_claim_hook_parser
from synapse_channel.cli_codex_claim_hook import add_parser as add_codex_claim_hook_parser
from synapse_channel.cli_gemini_claim_hook import add_parser as add_gemini_claim_hook_parser
from synapse_channel.cli_grok_claim_hook import add_parser as add_grok_claim_hook_parser
from synapse_channel.cli_kimi_claim_hook import add_parser as add_kimi_claim_hook_parser
from synapse_channel.client.agent import default_hub_uri
from synapse_channel.kimi_hook_config_file import (
    KimiHookConfigFileError,
    install_hook_config,
    resolve_kimi_config_path,
    uninstall_hook_config,
)
from synapse_channel.kimi_hook_installer import (
    KimiHookInstallerError,
)

_KIMI_KEYS = frozenset({"kimi", "kimi-project"})
"""Adapter keys whose tool uses KIMI's ``[[hooks]]`` claim guard."""

Which = Callable[[str], str | None]
Environment = Mapping[str, str]


def _roots(args: argparse.Namespace) -> tuple[Path, Path]:
    """Return the (home, project) roots, honouring the optional overrides."""
    home = Path(args.home).expanduser() if args.home else Path.home()
    project = Path(args.project).expanduser() if args.project else Path.cwd()
    return home, project


def _target_environment(args: argparse.Namespace) -> Environment:
    """Return provider path variables, isolated when ``--home`` is explicit."""
    return {} if args.home else os.environ


def _is_installed(tool: AdapterTool, *, home: Path, project: Path, environ: Environment) -> bool:
    """Return whether a Synapse adapter block is present for ``tool``."""
    target = resolve_target(tool, home=home, project=project, environ=environ)
    if not target.is_file():
        return False
    return contains_block(target.read_text(encoding="utf-8"))


def _selected(args: argparse.Namespace) -> list[AdapterTool]:
    """Resolve the named tools, or all of them when none are named."""
    if not args.tools:
        return list(CATALOGUE)
    return [tool_for(key) for key in args.tools]


def _cmd_list(args: argparse.Namespace, *, which: Which = shutil.which) -> int:
    """Detect tools and report adapter status; writes nothing."""
    home, project = _roots(args)
    environ = _target_environment(args)
    try:
        tools = _selected(args)
    except KeyError as exc:
        print(f"unknown tool {exc}", file=sys.stderr)
        return 2
    print(f"{'tool':<16} {'detected':<9} {'adapter':<11} target")
    for tool in tools:
        detected = detect_installed(tool, home=home, which=which, environ=environ)
        installed = _is_installed(tool, home=home, project=project, environ=environ)
        target = resolve_target(tool, home=home, project=project, environ=environ)
        print(
            f"{tool.key:<16} {'yes' if detected else 'no':<9} "
            f"{'installed' if installed else '-':<11} {target}"
        )
    return 0


def _install_one(
    tool: AdapterTool,
    *,
    home: Path,
    project: Path,
    environ: Environment,
    identity: str,
    hub_uri: str,
    dry_run: bool,
) -> str:
    """Plan and (unless ``dry_run``) write ``tool``'s adapter, returning a status line."""
    target = resolve_target(tool, home=home, project=project, environ=environ)
    block = render_block(tool, identity=identity, hub_uri=hub_uri)
    existing = target.read_text(encoding="utf-8") if target.is_file() else None
    content = plan_install(existing, block, mode=tool.mode)
    verb = "would write" if dry_run else ("updated" if existing else "wrote")
    if not dry_run:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return f"  {tool.key:<16} {verb} {target}"


def _install_kimi_hook(
    args: argparse.Namespace, *, home: Path, environ: Environment, dry_run: bool
) -> str:
    """Install the Synapse claim hook into KIMI's config.toml, returning a status line."""
    config_path = resolve_kimi_config_path(args.kimi_config, environ=environ, home=home)
    if dry_run:
        return f"  kimi-hook        would install hook at {config_path}"
    result = install_hook_config(
        config_path,
        identity=args.identity,
        uri=args.uri,
        ready_timeout=args.ready_timeout,
        token_file=args.token_file,
        synapse_bin=args.synapse_bin,
    )
    if result.outcome == "unchanged":
        return f"  kimi-hook        already installed at {config_path}"
    return f"  kimi-hook        {result.outcome} {config_path}"


def _uninstall_kimi_hook(args: argparse.Namespace, *, home: Path, environ: Environment) -> str:
    """Remove the Synapse claim hook from KIMI's config.toml, returning a status line."""
    config_path = resolve_kimi_config_path(args.kimi_config, environ=environ, home=home)
    result = uninstall_hook_config(config_path)
    if result.outcome == "not-installed":
        return "  kimi-hook        not installed"
    if result.outcome == "removed":
        return f"  kimi-hook        cleared {config_path}"
    return f"  kimi-hook        removed {config_path}"


def _cmd_install(args: argparse.Namespace, *, which: Which = shutil.which) -> int:
    """Write the claim-aware adapter for detected (or named) tools."""
    home, project = _roots(args)
    environ = _target_environment(args)
    try:
        chosen = _selected(args)
    except KeyError as exc:
        print(f"unknown tool {exc}", file=sys.stderr)
        return 2
    if not args.tools:
        chosen = [
            tool
            for tool in chosen
            if detect_installed(tool, home=home, which=which, environ=environ)
        ]
    if not chosen:
        print("no tools detected; name a tool explicitly to install anyway")
        return 0
    if args.with_hook and not any(tool.key in _KIMI_KEYS for tool in chosen):
        print("--with-hook requires selecting kimi or kimi-project", file=sys.stderr)
        return 2
    try:
        print("dry run — no files written:" if args.dry_run else "installed adapters:")
        for tool in chosen:
            print(
                _install_one(
                    tool,
                    home=home,
                    project=project,
                    environ=environ,
                    identity=args.identity,
                    hub_uri=args.uri,
                    dry_run=args.dry_run,
                )
            )
        if args.with_hook:
            print(_install_kimi_hook(args, home=home, environ=environ, dry_run=args.dry_run))
    except (KimiHookConfigFileError, KimiHookInstallerError, OSError, ValueError) as exc:
        print(f"cannot install KIMI hook: {exc}", file=sys.stderr)
        return 2
    return 0


def _uninstall_one(tool: AdapterTool, *, home: Path, project: Path, environ: Environment) -> str:
    """Remove ``tool``'s adapter content, returning a status line."""
    target = resolve_target(tool, home=home, project=project, environ=environ)
    if not _is_installed(tool, home=home, project=project, environ=environ):
        return f"  {tool.key:<16} not installed"
    content = plan_uninstall(target.read_text(encoding="utf-8"), mode=tool.mode)
    if content is None:
        target.unlink()
        return f"  {tool.key:<16} removed {target}"
    if content.strip():
        target.write_text(content, encoding="utf-8")
    else:
        target.unlink()
    return f"  {tool.key:<16} cleared {target}"


def _cmd_uninstall(args: argparse.Namespace) -> int:
    """Remove only Synapse-written adapter content for named (or all) tools."""
    home, project = _roots(args)
    environ = _target_environment(args)
    try:
        chosen = _selected(args)
    except KeyError as exc:
        print(f"unknown tool {exc}", file=sys.stderr)
        return 2
    if args.with_hook and not any(tool.key in _KIMI_KEYS for tool in chosen):
        print("--with-hook requires selecting kimi or kimi-project", file=sys.stderr)
        return 2
    try:
        print("uninstalled adapters:")
        for tool in chosen:
            print(_uninstall_one(tool, home=home, project=project, environ=environ))
        if args.with_hook:
            print(_uninstall_kimi_hook(args, home=home, environ=environ))
    except (KimiHookConfigFileError, KimiHookInstallerError, OSError, ValueError) as exc:
        print(f"cannot uninstall adapter or KIMI hook: {exc}", file=sys.stderr)
        return 2
    return 0


def _add_common(parser: argparse.ArgumentParser) -> None:
    """Add the home/project override options shared by every adapters subcommand."""
    parser.add_argument("tools", nargs="*", help="Tool keys; default is all (or all detected).")
    parser.add_argument(
        "--home",
        default=None,
        help="Override the home root and ignore KIMI_CODE_HOME (for testing).",
    )
    parser.add_argument("--project", default=None, help="Override the project root.")


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``adapters`` command group."""
    parser = subparsers.add_parser(
        "adapters",
        help="Detect coding tools and wire them to the hub with a claim-aware adapter.",
    )
    group = parser.add_subparsers(dest="adapters_command", required=True)

    add_claude_claim_hook_parser(group)
    add_codex_claim_hook_parser(group)
    add_gemini_claim_hook_parser(group)
    add_grok_claim_hook_parser(group)
    add_kimi_claim_hook_parser(group)

    lister = group.add_parser("list", help="Detect tools and report adapter status (read-only).")
    _add_common(lister)
    lister.set_defaults(func=_cmd_list)

    installer = group.add_parser("install", help="Write the claim-aware adapter into each tool.")
    _add_common(installer)
    installer.add_argument("--identity", default="your-agent", help="Identity to record.")
    installer.add_argument("--uri", default=default_hub_uri(), help="Hub URI to record.")
    installer.add_argument("--dry-run", action="store_true", help="Print planned writes only.")
    installer.add_argument(
        "--with-hook",
        action="store_true",
        help="Also install the KIMI PreToolUse hook into $KIMI_CODE_HOME/config.toml.",
    )
    installer.add_argument(
        "--token-file", default=None, help="Hub token file referenced by the KIMI hook."
    )
    installer.add_argument(
        "--ready-timeout",
        type=float,
        default=2.0,
        help="Seconds allowed for each KIMI hook state-snapshot phase (default: 2).",
    )
    installer.add_argument(
        "--synapse-bin", default=None, help="Synapse executable resolved into the KIMI hook."
    )
    installer.add_argument(
        "--kimi-config", default=None, help="Override $KIMI_CODE_HOME/config.toml."
    )
    installer.set_defaults(func=_cmd_install)

    remover = group.add_parser("uninstall", help="Remove only Synapse-written adapter content.")
    _add_common(remover)
    remover.add_argument(
        "--with-hook",
        action="store_true",
        help="Also remove the KIMI PreToolUse hook block from $KIMI_CODE_HOME/config.toml.",
    )
    remover.add_argument(
        "--kimi-config", default=None, help="Override $KIMI_CODE_HOME/config.toml."
    )
    remover.set_defaults(func=_cmd_uninstall)
