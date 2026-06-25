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

from hub_e2e_helpers import AgentHandle, _free_port, close_agents, connect_agent, running_hub
from synapse_channel import cli_queries
from synapse_channel.core.hub import SynapseHub


async def _claim(
    uri: str,
    name: str,
    task_id: str,
    *,
    paths: list[str],
    checkpoint: str = "",
    git: dict[str, str] | None = None,
) -> AgentHandle:
    handle = await connect_agent(name, uri)
    await handle.agent.claim(task_id, paths=paths, git=git)
    await handle.recorder.wait_for(
        lambda message: (
            message.get("type") == "claim_granted"
            and message.get("task_id") == task_id
            and message.get("owner") == name
        )
    )
    if checkpoint:
        await handle.agent.save_checkpoint(task_id, checkpoint)
        await handle.recorder.wait_for(
            lambda message: (
                message.get("type") == "checkpoint_saved" and message.get("task_id") == task_id
            )
        )
    return handle


async def test_state_prints_claims_filtered(capsys: pytest.CaptureFixture[str]) -> None:
    async with running_hub(SynapseHub()) as (_, uri):
        quantum = await _claim(uri, "quantum/agent-1", "T1", paths=["src"], checkpoint="cp1")
        other = await _claim(uri, "other/agent-2", "T2", paths=["docs"])
        try:
            code = await cli_queries._state(uri=uri, name="U", owner="quantum")
        finally:
            await close_agents(quantum, other)

    assert code == 0
    out = capsys.readouterr().out
    assert "Active claims (1)" in out
    assert "T1" in out
    assert "checkpoint=cp1" in out
    assert "other/agent-2" not in out


async def test_state_lists_all_without_owner(capsys: pytest.CaptureFixture[str]) -> None:
    async with running_hub(SynapseHub()) as (_, uri):
        owner = await _claim(uri, "a", "T1", paths=["src"])
        try:
            assert await cli_queries._state(uri=uri, name="U") == 0
        finally:
            await close_agents(owner)

    assert "Active claims (1)" in capsys.readouterr().out


async def test_state_reports_unreachable(capsys: pytest.CaptureFixture[str]) -> None:
    assert (
        await cli_queries._state(uri=f"ws://127.0.0.1:{_free_port()}", name="U", ready_timeout=0.1)
        == 1
    )
    assert "Could not reach hub" in capsys.readouterr().out


async def test_state_query_quiet_when_no_matching_snapshot() -> None:
    rendered: list[str] = []
    async with running_hub(SynapseHub()) as (_, uri):
        assert (
            await cli_queries._query_hub(
                uri=uri,
                name="U",
                token=None,
                response_type="not_a_real_snapshot_type",
                request=lambda agent: agent.request_state(),
                render=lambda value: rendered.append(str(value)),
                attempts=1,
            )
            == 0
        )
    assert rendered == []


def test_cmd_state_dispatches_real_query() -> None:
    ns = argparse.Namespace(
        uri=f"ws://127.0.0.1:{_free_port()}",
        name="U",
        owner=None,
        token=None,
        ready_timeout=0.1,
    )
    assert cli_queries._cmd_state(ns) == 1


async def test_state_shows_git_branch(capsys: pytest.CaptureFixture[str]) -> None:
    async with running_hub(SynapseHub()) as (_, uri):
        owner = await _claim(
            uri,
            "a",
            "T1",
            paths=["src"],
            git={"branch": "feature/x", "base": "main", "auto_release_on": "merge"},
        )
        try:
            assert await cli_queries._state(uri=uri, name="U") == 0
        finally:
            await close_agents(owner)

    assert "git=feature/x->main" in capsys.readouterr().out
