# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — installed first-run demo CLI command
"""The ``synapse demo`` command used by the first 60-second experience."""

from __future__ import annotations

import argparse
from collections.abc import Callable

from synapse_channel.demo import run_installed_demo

DemoRunner = Callable[[], list[str]]
"""Callable that executes the installed demo and returns narration lines."""


def _cmd_demo(args: argparse.Namespace, *, demo_runner: DemoRunner = run_installed_demo) -> int:
    """Run the installed first-run demo and print a concrete success marker.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed CLI arguments. The command currently has no user-facing options;
        the namespace is accepted for the shared CLI dispatch contract.
    demo_runner : DemoRunner, optional
        Injectable demo runner used by tests.

    Returns
    -------
    int
        Always ``0`` when the demo completes; exceptions from the runner are
        allowed to propagate so failures are visible.
    """
    del args
    print("=== SYNAPSE CHANNEL — first-run demo ===")
    demo_runner()
    print("success: coordination demo completed")
    return 0


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``demo`` subparser on the top-level CLI."""
    demo = subparsers.add_parser(
        "demo",
        help="Run a self-contained local coordination demo and print a success marker.",
    )
    demo.set_defaults(func=_cmd_demo)
