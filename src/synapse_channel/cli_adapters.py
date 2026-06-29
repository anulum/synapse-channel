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
import shutil
import sys
from collections.abc import Callable
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
from synapse_channel.client.agent import DEFAULT_HUB_URI

Which = Callable[[str], str | None]


def _roots(args: argparse.Namespace) -> tuple[Path, Path]:
    """Return the (home, project) roots, honouring the optional overrides."""
    home = Path(args.home).expanduser() if args.home else Path.home()
    project = Path(args.project).expanduser() if args.project else Path.cwd()
    return home, project


def _is_installed(tool: AdapterTool, *, home: Path, project: Path) -> bool:
    """Return whether a Synapse adapter block is present for ``tool``."""
    target = resolve_target(tool, home=home, project=project)
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
    try:
        tools = _selected(args)
    except KeyError as exc:
        print(f"unknown tool {exc}", file=sys.stderr)
        return 2
    print(f"{'tool':<16} {'detected':<9} {'adapter':<11} target")
    for tool in tools:
        detected = detect_installed(tool, home=home, which=which)
        installed = _is_installed(tool, home=home, project=project)
        target = resolve_target(tool, home=home, project=project)
        print(
            f"{tool.key:<16} {'yes' if detected else 'no':<9} "
            f"{'installed' if installed else '-':<11} {target}"
        )
    return 0


def _install_one(
    tool: AdapterTool, *, home: Path, project: Path, identity: str, hub_uri: str, dry_run: bool
) -> str:
    """Plan and (unless ``dry_run``) write ``tool``'s adapter, returning a status line."""
    target = resolve_target(tool, home=home, project=project)
    block = render_block(tool, identity=identity, hub_uri=hub_uri)
    existing = target.read_text(encoding="utf-8") if target.is_file() else None
    content = plan_install(existing, block, mode=tool.mode)
    verb = "would write" if dry_run else ("updated" if existing else "wrote")
    if not dry_run:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return f"  {tool.key:<16} {verb} {target}"


def _cmd_install(args: argparse.Namespace, *, which: Which = shutil.which) -> int:
    """Write the claim-aware adapter for detected (or named) tools."""
    home, project = _roots(args)
    try:
        chosen = _selected(args)
    except KeyError as exc:
        print(f"unknown tool {exc}", file=sys.stderr)
        return 2
    if not args.tools:
        chosen = [tool for tool in chosen if detect_installed(tool, home=home, which=which)]
    if not chosen:
        print("no tools detected; name a tool explicitly to install anyway")
        return 0
    print("dry run — no files written:" if args.dry_run else "installed adapters:")
    for tool in chosen:
        print(
            _install_one(
                tool,
                home=home,
                project=project,
                identity=args.identity,
                hub_uri=args.uri,
                dry_run=args.dry_run,
            )
        )
    return 0


def _uninstall_one(tool: AdapterTool, *, home: Path, project: Path) -> str:
    """Remove ``tool``'s adapter content, returning a status line."""
    target = resolve_target(tool, home=home, project=project)
    if not _is_installed(tool, home=home, project=project):
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
    try:
        chosen = _selected(args)
    except KeyError as exc:
        print(f"unknown tool {exc}", file=sys.stderr)
        return 2
    print("uninstalled adapters:")
    for tool in chosen:
        print(_uninstall_one(tool, home=home, project=project))
    return 0


def _add_common(parser: argparse.ArgumentParser) -> None:
    """Add the home/project override options shared by every adapters subcommand."""
    parser.add_argument("tools", nargs="*", help="Tool keys; default is all (or all detected).")
    parser.add_argument("--home", default=None, help="Override the home root (for testing).")
    parser.add_argument("--project", default=None, help="Override the project root.")


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``adapters`` command group."""
    parser = subparsers.add_parser(
        "adapters",
        help="Detect coding tools and wire them to the hub with a claim-aware adapter.",
    )
    group = parser.add_subparsers(dest="adapters_command", required=True)

    lister = group.add_parser("list", help="Detect tools and report adapter status (read-only).")
    _add_common(lister)
    lister.set_defaults(func=_cmd_list)

    installer = group.add_parser("install", help="Write the claim-aware adapter into each tool.")
    _add_common(installer)
    installer.add_argument("--identity", default="your-agent", help="Identity to record.")
    installer.add_argument("--uri", default=DEFAULT_HUB_URI, help="Hub URI to record.")
    installer.add_argument("--dry-run", action="store_true", help="Print planned writes only.")
    installer.set_defaults(func=_cmd_install)

    remover = group.add_parser("uninstall", help="Remove only Synapse-written adapter content.")
    _add_common(remover)
    remover.set_defaults(func=_cmd_uninstall)
