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
from pathlib import Path
from typing import Protocol, cast

from synapse_channel.demo import run_installed_demo
from synapse_channel.demo_artifacts import DemoArtifacts


class DemoRun(Protocol):
    """Result surface consumed by the demo command.

    Attributes
    ----------
    artifacts : DemoArtifacts
        Evidence paths printed after successful completion.
    """

    @property
    def artifacts(self) -> DemoArtifacts:
        """Return the evidence artifact paths."""


DemoRunner = Callable[[Path | None], DemoRun]
"""Callable that executes the installed demo and returns its artifact paths."""

_DEFAULT_DEMO_RUNNER = cast(DemoRunner, run_installed_demo)


def _cmd_demo(
    args: argparse.Namespace,
    *,
    demo_runner: DemoRunner = _DEFAULT_DEMO_RUNNER,
) -> int:
    """Run the installed first-run demo and print a concrete success marker.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed CLI arguments containing the optional artifact output directory.
    demo_runner : DemoRunner, optional
        Injectable demo runner used by tests.

    Returns
    -------
    int
        Always ``0`` when the demo completes; exceptions from the runner are
        allowed to propagate so failures are visible.
    """
    print("=== SYNAPSE CHANNEL — five-minute golden demo ===")
    run = demo_runner(args.output)
    print(f"evidence: {run.artifacts.evidence_json}")
    print(f"dashboard: {run.artifacts.dashboard_html}")
    print("success: coordination demo completed")
    return 0


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``demo`` subparser on the top-level CLI."""
    demo = subparsers.add_parser(
        "demo",
        help="Run the claim/conflict/handoff/receipt golden demo and write its dashboard.",
    )
    demo.add_argument(
        "--output",
        type=Path,
        help="Directory for golden-demo.json and golden-demo-dashboard.html.",
    )
    demo.set_defaults(func=_cmd_demo)
