# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the long-running process commands (hub/worker/team/supervisor)

from __future__ import annotations

import argparse
from typing import Any

import pytest

from synapse_channel import cli_processes


def test_cmd_team_returns_runner_code(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_processes, "run_team", lambda **kwargs: 4)
    ns = argparse.Namespace(
        port=8876, no_workers=False, fast_model=None, reason_model=None, prefix=""
    )
    assert cli_processes._cmd_team(ns) == 4


def test_cmd_team_threads_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(cli_processes, "run_team", lambda **kwargs: captured.update(kwargs) or 0)
    ns = argparse.Namespace(
        port=8876, no_workers=False, fast_model=None, reason_model=None, prefix="proj/"
    )
    assert cli_processes._cmd_team(ns) == 0
    assert captured["prefix"] == "proj/"


def test_cmd_supervisor_runs_and_handles_interrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_processes, "_run", lambda coro: coro.close())
    ns = argparse.Namespace(
        uri="ws://h", name="SUPERVISOR", idle_seconds=300.0, interval=30.0, token=None
    )
    assert cli_processes._cmd_supervisor(ns) == 0

    def interrupt(coro: Any) -> None:
        coro.close()
        raise KeyboardInterrupt

    monkeypatch.setattr(cli_processes, "_run", interrupt)
    assert cli_processes._cmd_supervisor(ns) == 0
