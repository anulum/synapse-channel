# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — smoke tests that keep the runnable examples honest

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def _load(name: str) -> ModuleType:
    """Load an example module by file path (the examples are not a package)."""
    spec = importlib.util.spec_from_file_location(name, EXAMPLES / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_coordination_demo_drives_the_full_plane() -> None:
    demo = _load("coordination_demo")
    log = await demo.run_demo(demo._free_port())
    # The dependent task starts blocked, then unblocks once its dependency is done.
    assert any("ready set: ['BUILD']" in line for line in log)
    assert any("ready set: ['TEST']" in line for line in log)
    # The overlapping file-scope claim is refused, and the task is handed off.
    assert any("refused" in line for line in log)
    assert any("handed TEST off to PLANNER" in line for line in log)


async def test_llm_team_demo_offline_reply() -> None:
    demo = _load("llm_team_demo")
    reply = await demo.run_demo(demo._free_port(), provider="rule")
    assert reply == "message received via Synapse. I am active on-channel."
