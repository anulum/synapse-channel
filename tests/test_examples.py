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
    result = await demo.run_demo(demo._free_port())
    assert result.completed is True
    assert result.guard_before_handoff.allowed is False
    assert result.guard_after_handoff.allowed is True
    assert result.receipt["epistemic_status"] == "supported"
    assert any("CONFLICT REFUSED" in line for line in result.narration)
    assert any("HANDOFF" in line for line in result.narration)


async def test_llm_team_demo_offline_reply() -> None:
    demo = _load("llm_team_demo")
    reply = await demo.run_demo(demo._free_port(), provider="rule")
    assert reply == "message received via Synapse. I am active on-channel."


async def test_coding_agents_demo_prevents_collisions() -> None:
    demo = _load("coding_agents_demo")
    log = await demo.run_demo(demo._free_port())
    # One agent holds a file scope; the other's overlapping claim is refused,
    # a disjoint claim is granted, a direct message arrives, and the lease is freed.
    assert any("claimed src/app/api.py" in line for line in log)
    assert any("refused" in line for line in log)
    assert any("disjoint scope, granted" in line for line in log)
    assert any("test-dev received:" in line for line in log)
    assert any("released" in line for line in log)
