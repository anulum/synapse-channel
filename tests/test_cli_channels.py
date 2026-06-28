# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the private-channel CLI

from __future__ import annotations

from typing import Any, cast

import pytest

from hub_e2e_helpers import running_hub
from synapse_channel import cli, cli_channels
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.protocol import MessageType


def test_parser_registers_channel_subcommands() -> None:
    parser = cli.build_parser()
    create = parser.parse_args(["channel", "create", "ops", "--name", "A", "--label", "Ops"])
    assert create.func is cli_channels._cmd_channel
    assert create.channel_command == "create"
    assert create.channel == "ops"
    assert create.label == "Ops"
    listing = parser.parse_args(["channel", "list", "--name", "A"])
    assert listing.channel_command == "list"


def test_send_parser_accepts_channel_flag() -> None:
    args = cli.build_parser().parse_args(["send", "hi", "--channel", "ops", "--name", "A"])
    assert args.channel == "ops"


def test_print_reply_renders_results_and_lists(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli_channels._print_reply({"type": MessageType.CHANNEL_RESULT, "ok": False}) == 1
    assert "failed" in capsys.readouterr().out

    ok = cli_channels._print_reply(
        {"type": MessageType.CHANNEL_RESULT, "ok": True, "payload": "joined", "members": ["A", "B"]}
    )
    out = capsys.readouterr().out
    assert ok == 0
    assert "joined" in out
    assert "members: A, B" in out

    assert cli_channels._print_reply({"type": MessageType.CHANNEL_LIST, "channels": []}) == 0
    assert "(none)" in capsys.readouterr().out


async def test_channel_cli_create_then_list_against_real_hub(
    capsys: pytest.CaptureFixture[str],
) -> None:
    async with running_hub(SynapseHub()) as (_hub, uri):
        created = await cli_channels._run_channel_command(
            uri=uri,
            name="alice",
            token=None,
            command="create",
            channel="release",
            label="Release",
            ready_timeout=2.0,
            response_timeout=2.0,
        )
        assert created == 0
        assert "created channel 'release'" in capsys.readouterr().out

        listed = await cli_channels._run_channel_command(
            uri=uri,
            name="alice",
            token=None,
            command="list",
            channel="",
            label="",
            ready_timeout=2.0,
            response_timeout=2.0,
        )
        assert listed == 0
        assert "release" in capsys.readouterr().out


def test_print_reply_ok_without_members(capsys: pytest.CaptureFixture[str]) -> None:
    code = cli_channels._print_reply(
        {"type": MessageType.CHANNEL_RESULT, "ok": True, "payload": "left 'ops'", "members": []}
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "left 'ops'" in out
    assert "members:" not in out


async def test_channel_cli_join_and_leave_against_real_hub(
    capsys: pytest.CaptureFixture[str],
) -> None:
    async with running_hub(SynapseHub()) as (_hub, uri):
        await cli_channels._run_channel_command(
            uri=uri,
            name="alice",
            token=None,
            command="create",
            channel="ops",
            label="",
            ready_timeout=2.0,
            response_timeout=2.0,
        )
        capsys.readouterr()
        joined = await cli_channels._run_channel_command(
            uri=uri,
            name="bob",
            token=None,
            command="join",
            channel="ops",
            label="",
            ready_timeout=2.0,
            response_timeout=2.0,
        )
        assert joined == 0
        assert "joined 'ops'" in capsys.readouterr().out
        left = await cli_channels._run_channel_command(
            uri=uri,
            name="bob",
            token=None,
            command="leave",
            channel="ops",
            label="",
            ready_timeout=2.0,
            response_timeout=2.0,
        )
        assert left == 0
        assert "left 'ops'" in capsys.readouterr().out


async def test_cmd_channel_dispatches_through_real_hub(capsys: pytest.CaptureFixture[str]) -> None:
    import argparse

    async with running_hub(SynapseHub()) as (_hub, uri):
        args = argparse.Namespace(
            channel_command="create",
            channel="dispatch",
            label="",
            uri=uri,
            name="alice",
            token=None,
            ready_timeout=2.0,
            response_timeout=2.0,
        )
        # _cmd_channel wraps the runner in asyncio.run; call it off the loop.
        import asyncio

        code = await asyncio.to_thread(cli_channels._cmd_channel, args)
        assert code == 0
        assert "created channel 'dispatch'" in capsys.readouterr().out


async def test_channel_cli_reports_no_reply_from_silent_hub(
    capsys: pytest.CaptureFixture[str],
) -> None:
    class _SilentAgent:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self.running = True
            self.last_close_code: int | None = None
            self.last_close_reason = ""

        async def connect(self) -> None:
            while self.running:
                import asyncio

                await asyncio.sleep(0.01)

        async def wait_until_ready(self, timeout: float = 5.0) -> bool:
            return True

        async def request_channels(self) -> None:
            return None

    code = await cli_channels._run_channel_command(
        uri="ws://localhost:1",
        name="alice",
        token=None,
        command="list",
        channel="",
        label="",
        ready_timeout=0.2,
        response_timeout=0.1,
        agent_factory=cast(Any, _SilentAgent),
    )
    assert code == 1
    assert "did not answer" in capsys.readouterr().out


async def test_channel_cli_reports_unreachable_hub(capsys: pytest.CaptureFixture[str]) -> None:
    from hub_e2e_helpers import _free_port

    code = await cli_channels._run_channel_command(
        uri=f"ws://127.0.0.1:{_free_port()}",
        name="alice",
        token=None,
        command="list",
        channel="",
        label="",
        ready_timeout=0.1,
        response_timeout=0.2,
    )
    assert code == 1
    assert "Could not reach hub" in capsys.readouterr().out
