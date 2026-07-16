# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — installed five-minute golden demo facade
"""Public entry points for the installed five-minute coordination demo."""

from __future__ import annotations

import asyncio
import tempfile
from dataclasses import dataclass
from pathlib import Path

from synapse_channel.demo_artifacts import DemoArtifacts, write_demo_artifacts
from synapse_channel.demo_runtime import (
    DemoInbox as _RuntimeDemoInbox,
)
from synapse_channel.demo_runtime import (
    _await_listening as _runtime_await_listening,
)
from synapse_channel.demo_runtime import (
    _free_port as _runtime_free_port,
)
from synapse_channel.demo_scenario import GoldenDemoResult, _run_golden_scenario

DemoInbox = _RuntimeDemoInbox
"""Compatibility export for the demo inbox helper."""

_await_listening = _runtime_await_listening
_free_port = _runtime_free_port


@dataclass(frozen=True)
class InstalledDemoRun:
    """Golden demo result plus the evidence artifacts written for the operator.

    Attributes
    ----------
    result : GoldenDemoResult
        Structured result from the real coordination scenario.
    artifacts : DemoArtifacts
        JSON and HTML paths written for operator inspection.
    """

    result: GoldenDemoResult
    artifacts: DemoArtifacts


async def run_coordination_demo(
    port: int,
    *,
    workspace: Path | None = None,
) -> GoldenDemoResult:
    """Run the complete golden demo on ``port`` using a real Git workspace.

    Parameters
    ----------
    port : int
        Local TCP port for the disposable in-process hub.
    workspace : pathlib.Path or None, optional
        Empty directory used for the demo Git repository. ``None`` creates and
        removes a temporary workspace automatically.

    Returns
    -------
    GoldenDemoResult
        Structured safety, verification, and dashboard evidence.
    """
    if workspace is not None:
        workspace.mkdir(parents=True, exist_ok=True)
        return await _run_golden_scenario(port, workspace)
    with tempfile.TemporaryDirectory(prefix="synapse-golden-workspace-") as temp_dir:
        return await _run_golden_scenario(port, Path(temp_dir))


def run_installed_demo(output_dir: Path | None = None) -> InstalledDemoRun:
    """Run the installed golden demo and persist its evidence artifacts.

    Parameters
    ----------
    output_dir : pathlib.Path or None, optional
        Directory for JSON evidence and the rendered dashboard. ``None`` creates
        a persistent temporary directory and lets the CLI print its location.

    Returns
    -------
    InstalledDemoRun
        Structured result and artifact paths.
    """
    target = (
        output_dir
        if output_dir is not None
        else Path(tempfile.mkdtemp(prefix="synapse-golden-demo-"))
    )
    result = asyncio.run(run_coordination_demo(_free_port()))
    return InstalledDemoRun(result=result, artifacts=write_demo_artifacts(result, target))
