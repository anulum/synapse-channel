# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — quickstart coding fleet CLI command
"""Parser and command handler for ``synapse quickstart-coding``."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path

from synapse_channel.quickstart_coding import run_quickstart_coding

QuickstartRunner = Callable[..., int]
"""Callable that executes the quickstart coding flow."""


def _cmd_quickstart_coding(
    args: argparse.Namespace,
    *,
    runner: QuickstartRunner = run_quickstart_coding,
) -> int:
    """Run the coding-fleet quickstart command.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed CLI arguments with ``path``, ``force``, and ``keep`` attributes.
    runner : QuickstartRunner, optional
        Injectable runtime used by tests.

    Returns
    -------
    int
        ``0`` on success; ``2`` when the workspace is refused as unsafe to write.
    """
    path = Path(args.path) if args.path is not None else None
    try:
        return runner(path, force=args.force, keep=args.keep)
    except FileExistsError as exc:
        print(f"synapse quickstart-coding: {exc}", file=sys.stderr)
        return 2


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``synapse quickstart-coding`` command."""
    parser = subparsers.add_parser(
        "quickstart-coding",
        help="Create a coding-fleet workspace and run the no-collision demo.",
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=None,
        help="Optional workspace directory to create and keep.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Refresh generated files in an existing workspace without deleting unrelated files.",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="Keep the generated temporary workspace when no path is supplied.",
    )
    parser.set_defaults(func=_cmd_quickstart_coding)
