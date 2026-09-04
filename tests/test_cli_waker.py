# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — active-waker CLI tests

from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from synapse_channel import cli, cli_waker
from synapse_channel.agent_tmux import AgentTmuxStatus
from synapse_channel.waker_service import WakerOperationResult, WakerStatus

IDENTITY = "repo/codex-1"


def _provider(*, pending: bool = False) -> AgentTmuxStatus:
    return AgentTmuxStatus(
        identity=IDENTITY,
        session="repo-codex-1",
        session_exists=True,
        pane_command="codex",
        pane_start_command="codex",
        agent_active=True,
        binding_valid=True,
        binding_detail="verified",
        pending_wake=pending,
    )


def _status(**changes: Any) -> WakerStatus:
    baseline = WakerStatus(
        identity=IDENTITY,
        desired_state="armed",
        generation=3,
        inhibit_reason=None,
        unit="synapse-waker@repo-codex-1.service",
        service_active="active",
        service_substate="running",
        restart_count=2,
        main_status=0,
        provider=_provider(),
        service_query_ok=True,
    )
    return replace(baseline, **changes)


def test_parser_registers_complete_waker_lifecycle(tmp_path: Path) -> None:
    parsed = cli.build_parser(command="waker").parse_args(
        [
            "waker",
            "install",
            "--identity",
            IDENTITY,
            "--session",
            "repo-codex-1",
            "--cwd",
            str(tmp_path),
            "--agent-command",
            "codex --model gpt-5",
            "--token-file",
            "/run/secrets/hub-token",
            "--start",
        ]
    )
    assert parsed.func is cli_waker._cmd_waker
    assert parsed.waker_command == "install"
    assert parsed.identity == IDENTITY
    assert parsed.start is True
    for command in ("stop", "resume", "status", "run"):
        args = ["waker", command, "--identity", IDENTITY]
        if command == "stop":
            args.extend(("--reason", "malfunction"))
        assert cli.build_parser(command="waker").parse_args(args).waker_command == command


@pytest.mark.parametrize("ok", [True, False])
def test_install_dispatches_tokens_and_reports_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any, ok: bool
) -> None:
    captured: dict[str, Any] = {}

    def installer(**kwargs: Any) -> WakerOperationResult:
        captured.update(kwargs)
        return WakerOperationResult(ok, ("install evidence",), 1)

    monkeypatch.setattr(cli_waker, "install_waker", installer)
    code = cli.main(
        [
            "waker",
            "install",
            "--identity",
            IDENTITY,
            "--session",
            "repo-codex-1",
            "--cwd",
            str(tmp_path),
            "--agent-command",
            "codex --model gpt-5",
            "--token-file",
            "/token",
        ]
    )
    assert code == (0 if ok else 1)
    assert captured["agent_command"] == ("codex", "--model", "gpt-5")
    assert captured["token_file"] == Path("/token")
    assert capsys.readouterr().out == "install evidence\n"


def test_stop_and_resume_dispatch_generation_guards(
    monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    calls: list[tuple[str, str, int | None]] = []

    def stop(
        identity: str,
        *,
        reason: str,
        expected_generation: int | None,
        command_timeout: float,
    ) -> WakerOperationResult:
        assert command_timeout == 30
        calls.append((identity, reason, expected_generation))
        return WakerOperationResult(True, ("stopped",), 5)

    def resume(
        identity: str,
        *,
        expected_generation: int | None,
        command_timeout: float,
        acknowledge_uncertain: bool,
    ) -> WakerOperationResult:
        assert command_timeout == 30 and not acknowledge_uncertain
        calls.append((identity, "resume", expected_generation))
        return WakerOperationResult(False, ("resume failed",), 6)

    monkeypatch.setattr(cli_waker, "inhibit_waker", stop)
    monkeypatch.setattr(cli_waker, "resume_waker", resume)
    assert (
        cli.main(
            [
                "waker",
                "stop",
                "--identity",
                IDENTITY,
                "--reason",
                "malfunction",
                "--expect-generation",
                "4",
            ]
        )
        == 0
    )
    assert cli.main(["waker", "resume", "--identity", IDENTITY]) == 1
    assert calls == [(IDENTITY, "malfunction", 4), (IDENTITY, "resume", None)]
    assert capsys.readouterr().out == "stopped\nresume failed\n"


def test_status_prints_execution_layers_pending_and_inhibit(
    monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    snapshot = _status(provider=_provider(pending=True), inhibit_reason="manual stop")
    monkeypatch.setattr(cli_waker, "inspect_waker", lambda _identity, **_kwargs: snapshot)
    assert cli.main(["waker", "status", "--identity", IDENTITY]) == 0
    output = capsys.readouterr().out
    assert "desired state: armed" in output
    assert "service: active/running" in output
    assert "provider: active" in output
    assert "pending wake: yes" in output
    assert "inhibit reason: manual stop" in output

    unavailable = _status(
        restart_count=None,
        main_status=None,
        service_active="unknown",
        service_substate="unknown",
        service_query_ok=False,
        provider=replace(_provider(), session_exists=False),
    )
    monkeypatch.setattr(cli_waker, "inspect_waker", lambda _identity, **_kwargs: unavailable)
    assert cli.main(["waker", "status", "--identity", IDENTITY]) == 1
    output = capsys.readouterr().out
    assert "restarts: unknown" in output
    assert "main status: unknown" in output
    assert "provider: unavailable" in output


def test_run_dispatches_and_errors_are_sanitised(
    monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    monkeypatch.setattr(cli_waker, "run_waker", lambda identity: 7 if identity == IDENTITY else 8)
    assert cli.main(["waker", "run", "--identity", IDENTITY]) == 7

    def fail(_identity: str) -> int:
        raise OSError("cannot start")

    monkeypatch.setattr(cli_waker, "run_waker", fail)
    assert cli.main(["waker", "run", "--identity", IDENTITY]) == 2
    assert "waker run: cannot start" in capsys.readouterr().err


def test_install_rejects_malformed_shell_command(capsys: Any) -> None:
    assert (
        cli.main(
            [
                "waker",
                "install",
                "--identity",
                IDENTITY,
                "--session",
                "repo-codex-1",
                "--agent-command",
                "'unterminated",
            ]
        )
        == 2
    )
    assert "No closing quotation" in capsys.readouterr().err


def test_dispatcher_rejects_unknown_internal_command() -> None:
    assert cli_waker._cmd_waker(argparse.Namespace(waker_command="unknown")) == 2
