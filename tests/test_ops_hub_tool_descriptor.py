# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — a tool replaced after its intent was approved never runs
"""A tool replaced after its intent was approved never runs, and the refusal names it.

The intent an approver names by digest carries the tool's descriptor, the fingerprint
of its executable taken at planning. ``synapse ops run --hub --intent-sha256`` plans
again before it asks the hub, so a replaced executable is refused twice over:

* while the catalogue row is still qualified for the old bytes, planning refuses it;
* once an operator qualifies the row for the new bytes, the plan succeeds, but the
  intent recomposed from it carries the new descriptor, so its digest is not the
  approved one, and the run is refused before the hub is asked.

Neither path invokes the program or asks the hub. The refusal names the executable's
current fingerprint, so the operator can compare it with the declared intent.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from synapse_channel.cli import main
from synapse_channel.operations.catalogue import Catalogue
from synapse_channel.operations.runner import fingerprint_executable
from test_cli_ops_hub import ScriptedHub, _approved_digest, _command, _hub
from test_operations_runner import World, _qualified


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> World:
    """A vendor program on PATH with its qualified catalogue, grants and journal."""
    return World(tmp_path, monkeypatch)


@pytest.fixture
def hub(monkeypatch: pytest.MonkeyPatch) -> ScriptedHub:
    """A hub that records every request and is never expected to answer one here."""
    return ScriptedHub(monkeypatch)


def _requalify(world: World) -> str:
    """Qualify every catalogue row for the program's current bytes; return that fingerprint."""
    _path, fingerprint = fingerprint_executable("vendorctl")
    world.catalogue = Catalogue([_qualified(row, fingerprint) for row in world.catalogue.rows()])
    world.catalogue_file.write_text(json.dumps(world.catalogue.to_json()), encoding="utf-8")
    return fingerprint


def test_a_tool_replaced_after_approval_never_runs_and_the_hub_is_never_asked(
    world: World, hub: ScriptedHub, capsys: pytest.CaptureFixture[str]
) -> None:
    """Replaced bytes are refused at planning, and again after requalification."""
    approved = _approved_digest(world)
    world.write_program("# replaced after approval\n")
    assert main(_command(world, *_hub("--intent-sha256", approved))) == 2
    capsys.readouterr()
    _requalify(world)
    assert main(_command(world, *_hub("--intent-sha256", approved))) == 2
    assert "changed since the intent was declared" in capsys.readouterr().err
    assert hub.requests == []
    assert world.invocations() == []


def test_the_refusal_names_the_tools_current_fingerprint(
    world: World, hub: ScriptedHub, capsys: pytest.CaptureFixture[str]
) -> None:
    """After requalification the refusal carries the executable's fingerprint now."""
    approved = _approved_digest(world)
    world.write_program("# upgraded after approval\n")
    fingerprint = _requalify(world)
    assert main(_command(world, *_hub("--intent-sha256", approved))) == 2
    err = capsys.readouterr().err
    assert f"the tool's executable is now {fingerprint}" in err
    assert hub.requests == []
    assert world.invocations() == []
