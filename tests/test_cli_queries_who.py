# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the read-only hub query commands (who/state/board/manifest/health)

from __future__ import annotations

import argparse

import pytest

from hub_e2e_helpers import _free_port, close_agents, connect_agent, running_hub
from synapse_channel import cli_queries
from synapse_channel.core.hub import SynapseHub


async def test_who_lists_project_agents(capsys: pytest.CaptureFixture[str]) -> None:
    async with running_hub(SynapseHub()) as (_, uri):
        quantum_one = await connect_agent("quantum/agent-1", uri)
        quantum_two = await connect_agent("quantum/agent-2", uri)
        other = await connect_agent("other/agent-3", uri)
        try:
            code = await cli_queries._who(uri=uri, name="U", project="quantum")
        finally:
            await close_agents(quantum_one, quantum_two, other)

    assert code == 0
    out = capsys.readouterr().out
    assert "Online in quantum (2)" in out
    assert "quantum/agent-1" in out
    assert "other/agent-3" not in out


async def test_who_lists_all_without_project(capsys: pytest.CaptureFixture[str]) -> None:
    async with running_hub(SynapseHub()) as (_, uri):
        b_handle = await connect_agent("b", uri)
        try:
            code = await cli_queries._who(uri=uri, name="a")
        finally:
            await close_agents(b_handle)

    assert code == 0
    assert "Online (2)" in capsys.readouterr().out


async def test_who_reports_unreachable(capsys: pytest.CaptureFixture[str]) -> None:
    code = await cli_queries._who(uri=f"ws://127.0.0.1:{_free_port()}", name="U", ready_timeout=0.1)
    assert code == 1
    assert "Could not reach hub" in capsys.readouterr().out


async def test_query_hub_returns_quietly_when_no_matching_snapshot() -> None:
    rendered: list[str] = []
    async with running_hub(SynapseHub()) as (_, uri):
        code = await cli_queries._query_hub(
            uri=uri,
            name="U",
            token=None,
            response_type="not_a_real_snapshot_type",
            request=lambda agent: agent.request_who(),
            render=lambda value: rendered.append(str(value)),
            attempts=1,
        )
    assert code == 0
    assert rendered == []


def test_cmd_who_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("synapse_channel.cli_queries.asyncio.run", lambda coro: coro.close() or 0)
    ns = argparse.Namespace(uri="ws://h", name="U", project=None, token=None)
    assert cli_queries._cmd_who(ns) == 0
