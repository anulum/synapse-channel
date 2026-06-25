# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the shared task-plan write commands (task declare/update/progress)

from __future__ import annotations

import argparse
import asyncio

import pytest

from hub_e2e_helpers import _free_port, running_hub
from synapse_channel import cli, cli_tasks
from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.auth import TokenAuthenticator
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.protocol import MessageType

# --- parser ------------------------------------------------------------------


def test_parser_task_declare() -> None:
    args = cli.build_parser().parse_args(
        ["task", "declare", "BUILD", "--title", "Compile", "--depends-on", "X"]
    )
    assert args.task_id == "BUILD"
    assert args.title == "Compile"
    assert args.depends_on == ["X"]
    assert args.func is cli_tasks._cmd_task_declare


def test_parser_task_update_and_progress() -> None:
    upd = cli.build_parser().parse_args(["task", "update", "BUILD", "--status", "done"])
    assert upd.task_id == "BUILD"
    assert upd.status == "done"
    assert upd.func is cli_tasks._cmd_task_update
    prog = cli.build_parser().parse_args(["task", "progress", "T", "running", "--kind", "blocker"])
    assert prog.text == "running"
    assert prog.kind == "blocker"
    assert prog.func is cli_tasks._cmd_task_progress


def test_task_bare_prints_usage(capsys: pytest.CaptureFixture[str]) -> None:
    args = cli.build_parser().parse_args(["task"])
    assert args.func is cli_tasks._cmd_task_help
    assert cli_tasks._cmd_task_help(args) == 1
    assert "synapse task" in capsys.readouterr().out


# --- declare / update / progress ---------------------------------------------


async def test_cmd_task_declare_prints_confirmation(capsys: pytest.CaptureFixture[str]) -> None:
    async with running_hub(SynapseHub()) as (_hub, uri):
        ns = argparse.Namespace(
            task_id="BUILD",
            title="Compile",
            depends_on=["X"],
            uri=uri,
            name="P",
            token=None,
        )
        code = await asyncio.to_thread(cli_tasks._cmd_task_declare, ns)

    assert code == 0
    out = capsys.readouterr().out
    assert "declared BUILD" in out
    assert "deps: X" in out


async def test_cmd_task_declare_uses_token(capsys: pytest.CaptureFixture[str]) -> None:
    token = "s3cret"
    async with running_hub(SynapseHub(authenticator=TokenAuthenticator([token]))) as (_hub, uri):
        ns = argparse.Namespace(
            task_id="BUILD",
            title="Compile",
            depends_on=[],
            uri=uri,
            name="P",
            token=token,
        )
        code = await asyncio.to_thread(cli_tasks._cmd_task_declare, ns)

    assert code == 0
    assert "declared BUILD" in capsys.readouterr().out


async def test_cmd_task_update_prints_confirmation(capsys: pytest.CaptureFixture[str]) -> None:
    async with running_hub(SynapseHub()) as (_hub, uri):
        declare = argparse.Namespace(
            task_id="BUILD",
            title="Compile",
            depends_on=[],
            uri=uri,
            name="P",
            token=None,
        )
        assert await asyncio.to_thread(cli_tasks._cmd_task_declare, declare) == 0
        capsys.readouterr()
        update = argparse.Namespace(
            task_id="BUILD",
            status="done",
            suggested_owner=None,
            uri=uri,
            name="P",
            token=None,
        )
        code = await asyncio.to_thread(cli_tasks._cmd_task_update, update)

    assert code == 0
    assert "status=done" in capsys.readouterr().out


async def test_cmd_task_progress_prints_confirmation(capsys: pytest.CaptureFixture[str]) -> None:
    async with running_hub(SynapseHub()) as (_hub, uri):
        declare = argparse.Namespace(
            task_id="TEST",
            title="Test",
            depends_on=[],
            uri=uri,
            name="P",
            token=None,
        )
        assert await asyncio.to_thread(cli_tasks._cmd_task_declare, declare) == 0
        capsys.readouterr()
        progress = argparse.Namespace(
            task_id="TEST",
            text="go",
            kind="note",
            uri=uri,
            name="P",
            token=None,
        )
        code = await asyncio.to_thread(cli_tasks._cmd_task_progress, progress)

    assert code == 0
    assert "posted note on TEST: go" in capsys.readouterr().out


async def test_task_action_returns_one_when_hub_unreachable(
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def send(agent: SynapseAgent) -> None:
        await agent.post_task("BUILD", title="Compile")

    code = await cli_tasks._task_action(
        uri=f"ws://127.0.0.1:{_free_port()}",
        name="P",
        token=None,
        confirm_type=MessageType.LEDGER_TASK_POSTED,
        send=send,
        render=lambda _message: "SHOULD-NOT-PRINT",
        attempts=1,
        ready_timeout=0.1,
    )
    assert code == 1
    out = capsys.readouterr().out
    assert "Could not reach hub" in out
    assert "SHOULD-NOT-PRINT" not in out


async def test_task_action_returns_quietly_when_no_confirmation(
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def send(agent: SynapseAgent) -> None:
        await agent.post_task("BUILD", title="Compile")

    async with running_hub(SynapseHub()) as (_hub, uri):
        code = await cli_tasks._task_action(
            uri=uri,
            name="P",
            token=None,
            confirm_type="not_a_real_confirmation_type",
            send=send,
            render=lambda _message: "SHOULD-NOT-PRINT",
            attempts=1,
        )

    assert code == 0
    assert "SHOULD-NOT-PRINT" not in capsys.readouterr().out
