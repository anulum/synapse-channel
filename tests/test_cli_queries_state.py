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
from synapse_channel import cli, cli_queries
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind
from synapse_channel.core.multihub_fold import fold_observed_state
from synapse_channel.core.multihub_merge import HubEvent
from synapse_channel.observed_peers import ObservedPeerSnapshot


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


def test_render_state_marks_observed_claims_as_advisory(
    capsys: pytest.CaptureFixture[str],
) -> None:
    observed = ObservedPeerSnapshot(
        hub_id="east",
        uri="ws://east",
        reachable=True,
        cursor=1,
        state=fold_observed_state(
            [
                HubEvent(
                    "east",
                    1,
                    1.0,
                    EventKind.CLAIM,
                    {"task_id": "T", "owner": "remote/agent", "paths": ["src/x.py"]},
                )
            ]
        ),
    )

    cli_queries._render_state({"active_claims": []}, observed_peers=(observed,))

    out = capsys.readouterr().out
    assert "Active claims (0)" in out
    assert "Observed claims (1; advisory, never local grants):" in out
    assert "T [observed@east] owner=remote/agent paths=src/x.py" in out


def test_state_parser_accepts_observed_peer_flags() -> None:
    args = cli.build_parser().parse_args(
        [
            "state",
            "--observed-peer",
            "east=ws://127.0.0.1:8877",
            "--observed-token",
            "secret",
            "--observed-timeout",
            "3.5",
        ]
    )

    assert args.observed_peers[0].hub_id == "east"
    assert args.observed_peers[0].uri == "ws://127.0.0.1:8877"
    assert args.observed_token == "secret"
    assert args.observed_timeout == 3.5


def test_parsers_accept_observed_pin_and_commands_refuse_a_stray_pin(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--observed-pin`` parses fail-fast and a pin for an unfetched hub exits 2."""
    pin = "sha256:" + "a" * 64
    args = cli.build_parser().parse_args(
        ["state", "--observed-peer", "east=wss://e:8877", "--observed-pin", f"east={pin}"]
    )
    assert args.observed_pins == [("east", pin)]

    with pytest.raises(SystemExit) as excinfo:
        cli.build_parser().parse_args(["who", "--observed-pin", "east=md5:aa"])
    assert excinfo.value.code == 2

    for command in ("who", "state"):
        stray = cli.build_parser().parse_args([command, "--observed-pin", f"ghost={pin}"])
        assert stray.func(stray) == 2
        assert "does not fetch" in capsys.readouterr().err
