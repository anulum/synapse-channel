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
from synapse_channel.core.auth import TokenAuthenticator
from synapse_channel.core.hub import SynapseHub


async def _post_board_task(uri: str, name: str, *, token: str | None = None) -> AgentHandle:
    handle = await connect_agent(name, uri, token=token)
    await handle.agent.post_task("A", title="Alpha")
    await handle.recorder.wait_for(
        lambda message: (
            message.get("type") == "ledger_task_posted"
            and message.get("task", {}).get("task_id") == "A"
        )
    )
    await handle.agent.post_progress("A", "go", kind="note")
    await handle.recorder.wait_for(
        lambda message: (
            message.get("type") == "ledger_progress_posted"
            and message.get("note", {}).get("task_id") == "A"
        )
    )
    return handle


def test_print_board_renders_tasks_ready_and_progress(
    capsys: pytest.CaptureFixture[str],
) -> None:
    board = {
        "tasks": [
            {"status": "open", "task_id": "A", "title": "Alpha", "depends_on": []},
            {"status": "blocked", "task_id": "B", "title": "Beta", "depends_on": ["A"]},
        ],
        "ready": ["A"],
        "progress": [{"author": "FAST", "kind": "note", "task_id": "A", "text": "go"}],
    }
    cli_queries._print_board(board)
    out = capsys.readouterr().out
    assert "[open] A — Alpha" in out
    assert "[blocked] B — Beta  (deps: A)" in out
    assert "Ready: A" in out
    assert "FAST [note] A: go" in out


def test_print_board_empty_ready_and_no_progress(
    capsys: pytest.CaptureFixture[str],
) -> None:
    cli_queries._print_board({"tasks": [], "ready": [], "progress": []})
    out = capsys.readouterr().out
    assert "Ready: (none)" in out
    assert "Recent progress" not in out


def test_print_board_progress_note_without_task(
    capsys: pytest.CaptureFixture[str],
) -> None:
    note = {"author": "P", "kind": "assessment", "text": "ok"}
    cli_queries._print_board({"tasks": [], "ready": [], "progress": [note]})
    assert "P [assessment] -: ok" in capsys.readouterr().out


async def test_board_prints_snapshot(capsys: pytest.CaptureFixture[str]) -> None:
    async with running_hub(SynapseHub()) as (_, uri):
        poster = await _post_board_task(uri, "FAST")
        try:
            code = await cli_queries._board(uri=uri, name="USER")
        finally:
            await close_agents(poster)

    assert code == 0
    out = capsys.readouterr().out
    assert "[open] A — Alpha" in out
    assert "FAST [note] A: go" in out


async def test_board_reports_unreachable_hub(capsys: pytest.CaptureFixture[str]) -> None:
    code = await cli_queries._board(
        uri=f"ws://127.0.0.1:{_free_port()}", name="USER", ready_timeout=0.1
    )
    assert code == 1
    assert "Could not reach hub" in capsys.readouterr().out


async def test_board_query_quiet_when_no_matching_snapshot() -> None:
    rendered: list[str] = []
    async with running_hub(SynapseHub()) as (_, uri):
        code = await cli_queries._query_hub(
            uri=uri,
            name="USER",
            token=None,
            response_type="not_a_real_snapshot_type",
            request=lambda agent: agent.request_board(),
            render=lambda value: rendered.append(str(value)),
            attempts=1,
        )
    assert code == 0
    assert rendered == []


def test_cmd_board_dispatches_real_query() -> None:
    ns = argparse.Namespace(
        uri=f"ws://127.0.0.1:{_free_port()}", name="USER", token=None, ready_timeout=0.1
    )
    assert cli_queries._cmd_board(ns) == 1


async def test_board_threads_token_to_agent() -> None:
    hub = SynapseHub(authenticator=TokenAuthenticator(["s3cret"]))
    async with running_hub(hub) as (_, uri):
        poster = await _post_board_task(uri, "FAST", token="s3cret")
        try:
            code = await cli_queries._board(uri=uri, name="U", token="s3cret")
        finally:
            await close_agents(poster)
    assert code == 0
