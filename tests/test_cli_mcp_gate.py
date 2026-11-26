# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — tests for the ``synapse mcp-gate`` command
"""``serve`` starts only on a valid configuration.

``new-bearer`` delivers the secret only where asked.
"""

from __future__ import annotations

import json
import stat
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
            _args(
                "new-bearer",
                "--config",
                str(config),
                "--seat",
                "PROJ/bot",
                "--print-secret",
            )
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
    assert "does not list: synapse_task_update" in capsys.readouterr().err
    grant_file(tmp_path / "grants.json", {"PROJ/bot": MESSAGING_TOOLS})
    assert cli_mcp_gate._cmd_serve(_args("serve", "--config", str(config))) == 2
    assert "bearer record of PROJ/bot is unusable" in capsys.readouterr().err


def test_new_bearer_prints_the_secret_once_and_stores_its_digest(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = write_config(tmp_path)
    argv = ["new-bearer", "--config", str(config), "--seat", "PROJ/bot", "--print-secret"]
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


def test_new_bearer_refuses_when_no_destination_is_named(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """With neither option the command refuses and stores no bearer."""
    config = write_config(tmp_path)
    record = tmp_path / "bot.bearer"
    assert (
        cli_mcp_gate._cmd_new_bearer(
            _args("new-bearer", "--config", str(config), "--seat", "PROJ/bot")
        )
        == 2
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "--secret-out" in captured.err
    assert "--print-secret" in captured.err
    assert not record.exists()


def test_new_bearer_writes_an_owner_only_file_and_prints_only_its_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``--secret-out`` creates a 0600 file and stdout names that path alone."""
    config = write_config(tmp_path)
    secret_out = tmp_path / "bearer.secret"
    assert (
        cli_mcp_gate._cmd_new_bearer(
            _args(
                "new-bearer",
                "--config",
                str(config),
                "--seat",
                "PROJ/bot",
                "--secret-out",
                str(secret_out),
            )
        )
        == 0
    )
    captured = capsys.readouterr()
    secret = secret_out.read_text(encoding="utf-8").strip()
    assert captured.out.strip() == str(secret_out)
    assert secret not in captured.out
    assert secret not in captured.err
    assert stat.S_IMODE(secret_out.stat().st_mode) == 0o600
    assert load_bearer(tmp_path / "bot.bearer").sha256 == bearer_digest(secret.encode())


def test_new_bearer_prints_the_secret_once_when_asked(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``--print-secret`` prints the bearer once."""
    config = write_config(tmp_path)
    assert (
        cli_mcp_gate._cmd_new_bearer(
            _args(
                "new-bearer",
                "--config",
                str(config),
                "--seat",
                "PROJ/bot",
                "--print-secret",
            )
        )
        == 0
    )
    captured = capsys.readouterr()
    secret = captured.out.strip()
    assert secret
    assert captured.out.count(secret) == 1
    assert "not shown again" in captured.err
    assert load_bearer(tmp_path / "bot.bearer").sha256 == bearer_digest(secret.encode())


def test_new_bearer_refuses_both_destinations(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Both options together are refused and store nothing."""
    config = write_config(tmp_path)
    secret_out = tmp_path / "bearer.secret"
    with pytest.raises(SystemExit) as exit_info:
        _args(
            "new-bearer",
            "--config",
            str(config),
            "--seat",
            "PROJ/bot",
            "--secret-out",
            str(secret_out),
            "--print-secret",
        )
    assert exit_info.value.code == 2
    assert "not allowed with argument" in capsys.readouterr().err
    assert not (tmp_path / "bot.bearer").exists()
    assert not secret_out.exists()


def test_new_bearer_refuses_an_existing_secret_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An existing ``--secret-out`` path is refused and left unchanged."""
    config = write_config(tmp_path)
    secret_out = tmp_path / "bearer.secret"
    secret_out.write_text("keep\n", encoding="utf-8")
    before = secret_out.read_bytes()
    assert (
        cli_mcp_gate._cmd_new_bearer(
            _args(
                "new-bearer",
                "--config",
                str(config),
                "--seat",
                "PROJ/bot",
                "--secret-out",
                str(secret_out),
            )
        )
        != 0
    )
    assert secret_out.read_bytes() == before
    assert not (tmp_path / "bot.bearer").exists()
    capsys.readouterr()


def test_new_bearer_rotation_leaves_the_old_record_when_the_secret_cannot_be_written(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An unwritable ``--secret-out`` does not rotate the bearer."""
    config = write_config(tmp_path)
    record_path = tmp_path / "bot.bearer"
    assert (
        cli_mcp_gate._cmd_new_bearer(
            _args(
                "new-bearer",
                "--config",
                str(config),
                "--seat",
                "PROJ/bot",
                "--print-secret",
            )
        )
        == 0
    )
    capsys.readouterr()
    before = record_path.read_bytes()
    secret_out = tmp_path / "missing" / "rotated.secret"
    assert (
        cli_mcp_gate._cmd_new_bearer(
            _args(
                "new-bearer",
                "--config",
                str(config),
                "--seat",
                "PROJ/bot",
                "--replace",
                "--secret-out",
                str(secret_out),
            )
        )
        == 2
    )
    assert record_path.read_bytes() == before
    assert not secret_out.exists()
    capsys.readouterr()


def test_new_bearer_removes_the_secret_file_when_the_record_is_not_stored(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A secret file written before a refused record does not stay behind."""
    config = write_config(tmp_path)
    record_path = tmp_path / "bot.bearer"
    assert (
        cli_mcp_gate._cmd_new_bearer(
            _args(
                "new-bearer",
                "--config",
                str(config),
                "--seat",
                "PROJ/bot",
                "--print-secret",
            )
        )
        == 0
    )
    capsys.readouterr()
    before = record_path.read_bytes()
    secret_out = tmp_path / "rotated.secret"
    assert (
        cli_mcp_gate._cmd_new_bearer(
            _args(
                "new-bearer",
                "--config",
                str(config),
                "--seat",
                "PROJ/bot",
                "--secret-out",
                str(secret_out),
            )
        )
        == 2
    )
    assert record_path.read_bytes() == before
    assert not secret_out.exists()
    assert "use --replace" in capsys.readouterr().err


def test_new_bearer_refuses_a_seat_the_configuration_does_not_name(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = write_config(tmp_path)
    assert (
        cli_mcp_gate._cmd_new_bearer(
            _args(
                "new-bearer",
                "--config",
                str(config),
                "--seat",
                "PROJ/other",
                "--print-secret",
            )
        )
        == 2
    )
    assert "no seat PROJ/other" in capsys.readouterr().err
    owner_file(config, json.dumps({"schema": "wrong"}))
    assert cli_mcp_gate._cmd_serve(_args("serve", "--config", str(config))) == 2
