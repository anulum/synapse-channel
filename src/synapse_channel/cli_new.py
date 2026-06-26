# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — workspace scaffold CLI commands
"""Parser and command handlers for ``synapse new`` workspace scaffolds."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path

from synapse_channel.coding_fleet_template import DEFAULT_WORKSPACE, create_coding_fleet

CodingFleetCreator = Callable[[Path], list[str]]
"""Callable that creates a coding-fleet workspace and returns CLI lines."""


def _cmd_new_coding_fleet(
    args: argparse.Namespace,
    *,
    creator: Callable[..., list[str]] = create_coding_fleet,
) -> int:
    """Create a runnable coding-fleet demo workspace.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed CLI arguments with ``path`` and ``force`` attributes.
    creator : Callable[..., list[str]], optional
        Injectable scaffold writer for tests.

    Returns
    -------
    int
        ``0`` on success; ``2`` when the target is refused as unsafe to write.
    """
    try:
        lines = creator(Path(args.path), force=args.force)
    except FileExistsError as exc:
        print(f"synapse new coding-fleet: {exc}", file=sys.stderr)
        return 2
    for line in lines:
        print(line)
    return 0


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``synapse new`` scaffold subcommands."""
    new = subparsers.add_parser("new", help="Create runnable Synapse demo workspaces.")
    nested = new.add_subparsers(dest="new_command", required=True)

    coding = nested.add_parser(
        "coding-fleet",
        help="Scaffold a runnable two-agent coding fleet demo workspace.",
    )
    coding.add_argument(
        "path",
        nargs="?",
        default=DEFAULT_WORKSPACE,
        help=f"Workspace directory to create (default: {DEFAULT_WORKSPACE}).",
    )
    coding.add_argument(
        "--force",
        action="store_true",
        help="Refresh template files in an existing non-empty directory without deleting "
        "unrelated files.",
    )
    coding.set_defaults(func=_cmd_new_coding_fleet)
