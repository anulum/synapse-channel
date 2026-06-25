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

from synapse_channel import cli, cli_processes


def test_run_executes_coroutine() -> None:
    marker: list[bool] = []

    async def noop() -> None:
        marker.append(True)

    cli_processes._run(noop())
    assert marker == [True]


def test_main_routes_to_team(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_processes, "run_team", lambda **kwargs: 9)
    assert cli.main(["team", "--no-workers"]) == 9


def test_main_routes_to_hub(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_processes, "_run", lambda coro: coro.close())
    assert cli.main(["hub", "--port", "9000"]) == 0


def test_main_resolves_token_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYNAPSE_TOKEN", "env-tok")
    captured: dict[str, Any] = {}

    def fake(args: argparse.Namespace) -> int:
        captured["token"] = args.token
        return 0

    monkeypatch.setattr(cli_processes, "_cmd_worker", fake)
    assert cli.main(["worker"]) == 0
    assert captured["token"] == "env-tok"
