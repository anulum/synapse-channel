# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — quickstart coding fleet command runtime
"""Runtime for the ``synapse quickstart-coding`` first-run command."""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path

from synapse_channel.coding_fleet import main as run_coding_fleet_demo
from synapse_channel.coding_fleet_template import create_coding_fleet

WorkspaceCreator = Callable[..., list[str]]
"""Callable that creates a coding-fleet workspace."""

DemoRunner = Callable[[], int]
"""Callable that runs the packaged coding-fleet demo."""


def _print_lines(lines: list[str]) -> None:
    """Print CLI narration lines in order."""
    for line in lines:
        print(line)


def run_quickstart_coding(
    path: Path | None,
    *,
    force: bool = False,
    keep: bool = False,
    creator: WorkspaceCreator = create_coding_fleet,
    demo_runner: DemoRunner = run_coding_fleet_demo,
) -> int:
    """Create a coding-fleet workspace and run the packaged demo.

    Parameters
    ----------
    path : Path or None
        Persistent workspace path. When ``None``, a temporary workspace is
        created and removed after the demo unless ``keep`` is true.
    force : bool, optional
        Refresh generated template files in an existing workspace.
    keep : bool, optional
        Preserve the temporary workspace created when ``path`` is ``None``.
    creator : WorkspaceCreator, optional
        Workspace writer used by the CLI and tests.
    demo_runner : DemoRunner, optional
        Packaged demo entry point used by the CLI and tests.

    Returns
    -------
    int
        Demo runner exit code.
    """
    if path is not None:
        lines = creator(path, force=force)
        _print_lines(lines)
        return demo_runner()

    workspace = Path(tempfile.mkdtemp(prefix="synapse-coding-")) / "fleet"
    print(f"temporary workspace: {workspace}")
    try:
        lines = creator(workspace, force=force)
        _print_lines(lines)
        code = demo_runner()
        if keep:
            print(f"kept temporary workspace: {workspace}")
        return code
    finally:
        if not keep:
            shutil.rmtree(workspace.parent, ignore_errors=True)
