# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — tests for the ``synapse mcp-gate`` command
"""``serve`` starts only on a valid configuration; ``new-bearer`` prints the secret once."""

from __future__ import annotations

import json
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

import pytest

from mcp_gate_helpers import owner_file
from synapse_channel import cli, cli_mcp_gate
from synapse_channel.mcp.gate_config import bearer_digest, load_bearer
from test_mcp_gate_config import MESSAGING_TOOLS, grant_file, write_config


def _args(*argv: str) -> Any:
    return cli.build_parser().parse_args(["mcp-gate", *argv])


def _provision(tmp_path: Path, tools: list[str]) -> Path:
    config = write_config(tmp_path)
    grant_file(tmp_path / "grants.json", {"PROJ/bot": tools})
    return config


def test_serve_runs_the_gate_on_a_valid_configuration(tmp_path: Path) -> None:
    config = _provision(tmp_path, MESSAGING_TOOLS)
    assert (
        cli_mcp_gate._cmd_new_bearer(
            _args("new-bearer", "--config", str(config), "--seat", "PROJ/bot")
        )
        == 0
    )
    started: list[Coroutine[Any, Any, None]] = []

    def runner(coroutine: Coroutine[Any, Any, None]) -> None:
        started.append(coroutine)
        coroutine.close()

    assert cli_mcp_gate._cmd_serve(_args("serve", "--config", str(config)), runner=runner) == 0
    assert len(started) == 1

    def interrupted(coroutine: Coroutine[Any, Any, None]) -> None:
        coroutine.close()
        raise KeyboardInterrupt

    assert cli_mcp_gate._cmd_serve(_args("serve", "--config", str(config)), runner=interrupted) == 0


def test_serve_refuses_a_seat_with_work_changing_grants_or_no_bearer(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _provision(tmp_path, [*MESSAGING_TOOLS, "synapse_task_update"])
    assert cli_mcp_gate._cmd_serve(_args("serve", "--config", str(config))) == 2
    assert "may not hold: synapse_task_update" in capsys.readouterr().err
    grant_file(tmp_path / "grants.json", {"PROJ/bot": MESSAGING_TOOLS})
    assert cli_mcp_gate._cmd_serve(_args("serve", "--config", str(config))) == 2
    assert "bearer record of PROJ/bot is unusable" in capsys.readouterr().err


def test_new_bearer_prints_the_secret_once_and_stores_its_digest(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = write_config(tmp_path)
    argv = ["new-bearer", "--config", str(config), "--seat", "PROJ/bot"]
    assert cli_mcp_gate._cmd_new_bearer(_args(*argv)) == 0
    output = capsys.readouterr()
    secret = output.out.strip()
    assert "not shown again" in output.err
    assert load_bearer(tmp_path / "bot.bearer").sha256 == bearer_digest(secret.encode())
    assert secret not in (tmp_path / "bot.bearer").read_text(encoding="ascii")
    assert cli_mcp_gate._cmd_new_bearer(_args(*argv)) == 2
    assert "use --replace" in capsys.readouterr().err
    assert cli_mcp_gate._cmd_new_bearer(_args(*argv, "--replace")) == 0
    assert capsys.readouterr().out.strip() != secret


def test_new_bearer_refuses_a_seat_the_configuration_does_not_name(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = write_config(tmp_path)
    assert (
        cli_mcp_gate._cmd_new_bearer(
            _args("new-bearer", "--config", str(config), "--seat", "PROJ/other")
        )
        == 2
    )
    assert "no seat PROJ/other" in capsys.readouterr().err
    owner_file(config, json.dumps({"schema": "wrong"}))
    assert cli_mcp_gate._cmd_serve(_args("serve", "--config", str(config))) == 2
