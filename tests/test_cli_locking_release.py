# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the lease-serialising CLI commands (lock/release)

from __future__ import annotations

import argparse
from typing import Any

import pytest

from cli_locking_helpers import FakeAgent, _factory
from synapse_channel import cli, cli_locking


def test_parser_release() -> None:
    args = cli.build_parser().parse_args(["release", "studio-panel-enrich", "--name", "USER"])
    assert args.task_id == "studio-panel-enrich"
    assert args.name == "USER"
    assert args.func is cli_locking._cmd_release


async def test_release_granted(capsys: pytest.CaptureFixture[str]) -> None:
    holder: list[FakeAgent] = []
    granted: dict[str, Any] = {
        "type": "release_granted",
        "task_id": "studio-panel-enrich",
        "owner": "USER",
    }
    factory = _factory(holder, inbound=[granted])
    code = await cli_locking._release(
        uri="ws://h",
        name="USER",
        task_id="studio-panel-enrich",
        agent_factory=factory,
    )
    assert code == 0
    assert holder[0].releases == ["studio-panel-enrich"]
    assert "released 'studio-panel-enrich'" in capsys.readouterr().out


async def test_release_denied_for_non_owner(capsys: pytest.CaptureFixture[str]) -> None:
    holder: list[FakeAgent] = []
    denied: dict[str, Any] = {
        "type": "release_denied",
        "task_id": "studio-panel-enrich",
        "payload": "owned by SCPN-MIF-CORE, not USER",
    }
    factory = _factory(holder, inbound=[denied])
    code = await cli_locking._release(
        uri="ws://h",
        name="USER",
        task_id="studio-panel-enrich",
        agent_factory=factory,
    )
    assert code == 1
    out = capsys.readouterr().out
    assert "release refused for 'studio-panel-enrich'" in out
    assert "owned by SCPN-MIF-CORE" in out


async def test_release_ignores_noise_then_confirms(capsys: pytest.CaptureFixture[str]) -> None:
    holder: list[FakeAgent] = []
    inbound: list[dict[str, Any]] = [
        # A grant for another task is ignored (wrong task id) ...
        {"type": "release_granted", "task_id": "other", "owner": "USER"},
        # ... and a grant addressed to a different owner is ignored too ...
        {"type": "release_granted", "task_id": "t", "owner": "ELSE"},
        # ... before the grant that actually belongs to this caller.
        {"type": "release_granted", "task_id": "t", "owner": "USER"},
    ]
    factory = _factory(holder, inbound=inbound)
    code = await cli_locking._release(uri="ws://h", name="USER", task_id="t", agent_factory=factory)
    assert code == 0
    assert "released 't'" in capsys.readouterr().out


async def test_release_reports_unreachable(capsys: pytest.CaptureFixture[str]) -> None:
    holder: list[FakeAgent] = []
    factory = _factory(holder, ready=False)
    code = await cli_locking._release(uri="ws://h", name="USER", task_id="t", agent_factory=factory)
    assert code == 1
    assert "Could not reach hub" in capsys.readouterr().out


async def test_release_gives_up_without_response(capsys: pytest.CaptureFixture[str]) -> None:
    holder: list[FakeAgent] = []
    factory = _factory(holder, inbound=[])
    code = await cli_locking._release(uri="ws://h", name="USER", task_id="t", agent_factory=factory)
    assert code == 1
    assert "no response from hub" in capsys.readouterr().out


def test_cmd_release_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("synapse_channel.cli_locking.asyncio.run", lambda coro: coro.close() or 0)
    ns = argparse.Namespace(uri="ws://h", name="USER", task_id="t", token=None)
    assert cli_locking._cmd_release(ns) == 0
