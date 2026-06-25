# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the long-running process commands (hub/worker/team/supervisor)

from __future__ import annotations

import argparse
from collections.abc import Coroutine
from typing import Any

import pytest

from hub_e2e_helpers import _free_port
from synapse_channel import cli, cli_processes
from synapse_channel.client.launcher import plan_team


def test_parser_routes_to_team_with_prefix() -> None:
    args = cli.build_parser().parse_args(["team", "--port", "8877", "--prefix", "proj/"])
    assert args.func is cli_processes._cmd_team
    assert args.port == 8877
    assert args.prefix == "proj/"


def test_team_plan_threads_prefix_without_spawning() -> None:
    specs = plan_team(
        8876,
        fast_model="fast-model",
        reason_model="reason-model",
        prefix="proj/",
    )
    assert [label for label, _argv in specs] == ["hub", "proj/FAST", "proj/REASON"]
    assert "proj/FAST" in specs[1][1]
    assert "proj/REASON" in specs[2][1]


def test_cmd_team_threads_arguments_to_launcher() -> None:
    captured: dict[str, Any] = {}
    ns = argparse.Namespace(
        port=8877,
        no_workers=True,
        fast_model="fast",
        reason_model="reason",
        prefix="proj/",
    )

    def launch(**kwargs: Any) -> int:
        captured.update(kwargs)
        return 7

    assert cli_processes._cmd_team(ns, launcher=launch) == 7
    assert captured == {
        "port": 8877,
        "no_workers": True,
        "fast_model": "fast",
        "reason_model": "reason",
        "prefix": "proj/",
    }


def test_cmd_supervisor_runs_real_unreachable_path() -> None:
    ns = argparse.Namespace(
        uri=f"ws://127.0.0.1:{_free_port()}",
        name="SUPERVISOR",
        idle_seconds=300.0,
        interval=30.0,
        token=None,
        ready_timeout=0.1,
    )
    assert cli_processes._cmd_supervisor(ns) == 0


def test_cmd_supervisor_handles_interrupt(capsys: pytest.CaptureFixture[str]) -> None:
    ns = argparse.Namespace(
        uri=f"ws://127.0.0.1:{_free_port()}",
        name="SUPERVISOR",
        idle_seconds=300.0,
        interval=30.0,
        token=None,
        ready_timeout=0.1,
    )

    def interrupt(coro: Coroutine[Any, Any, None]) -> None:
        coro.close()
        raise KeyboardInterrupt

    assert cli_processes._cmd_supervisor(ns, runner=interrupt) == 0
    assert "supervisor stopped by user" in capsys.readouterr().out
