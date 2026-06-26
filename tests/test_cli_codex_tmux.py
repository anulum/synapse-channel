# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for tmux-backed Codex wake CLI

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from synapse_channel import cli, cli_codex_tmux
from synapse_channel.codex_tmux import CodexTmuxConfig, CodexTmuxStatus, CodexTmuxWakeResult


def test_parser_registers_codex_tmux_start() -> None:
    args = cli.build_parser().parse_args(
        [
            "codex-tmux",
            "start",
            "--identity",
            "SYNAPSE-CHANNEL/codex-main",
            "--session",
            "synapse-codex-main",
            "--cwd",
            "/repo",
        ]
    )

    assert args.func is cli_codex_tmux._cmd_codex_tmux
    assert args.codex_tmux_command == "start"
    assert args.identity == "SYNAPSE-CHANNEL/codex-main"
    assert args.session == "synapse-codex-main"
    assert args.cwd == Path("/repo")
    assert args.codex_command == "codex"


def test_cmd_start_dispatches(capsys: Any, tmp_path: Path) -> None:
    captured: dict[str, CodexTmuxConfig] = {}

    def starter(config: CodexTmuxConfig) -> CodexTmuxWakeResult:
        captured["config"] = config
        return CodexTmuxWakeResult(injected=False, started=True, returncode=0, detail="started")

    ns = argparse.Namespace(
        codex_tmux_command="start",
        identity="SYNAPSE-CHANNEL/codex-main",
        session="synapse-codex-main",
        cwd=tmp_path,
        codex_command="codex --sandbox danger-full-access",
        tmux_bin="tmux",
        synapse_bin="synapse",
        uri="ws://localhost:8876",
        wake_jitter=0.0,
        max_wakes=1,
        token=None,
    )

    assert cli_codex_tmux._cmd_codex_tmux(ns, starter=starter) == 0
    assert captured["config"].identity == "SYNAPSE-CHANNEL/codex-main"
    assert captured["config"].codex_command == ("codex", "--sandbox", "danger-full-access")
    assert "started" in capsys.readouterr().out


def test_cmd_status_dispatches(capsys: Any, tmp_path: Path) -> None:
    def status_runner(config: CodexTmuxConfig) -> CodexTmuxStatus:
        assert config.session == "synapse-codex-main"
        return CodexTmuxStatus(
            identity=config.identity,
            session=config.session,
            session_exists=True,
            pane_command="codex",
            pane_start_command="codex",
            codex_active=True,
        )

    ns = argparse.Namespace(
        codex_tmux_command="status",
        identity="SYNAPSE-CHANNEL/codex-main",
        session="synapse-codex-main",
        cwd=tmp_path,
        codex_command="codex",
        tmux_bin="tmux",
        synapse_bin="synapse",
        uri="ws://localhost:8876",
        wake_jitter=0.0,
        max_wakes=1,
        token=None,
    )

    assert cli_codex_tmux._cmd_codex_tmux(ns, status_runner=status_runner) == 0
    out = capsys.readouterr().out
    assert "tmux session: online" in out
    assert "Codex pane: active" in out


def test_cmd_wait_dispatches(tmp_path: Path) -> None:
    captured: dict[str, Any] = {}

    def waiter(config: CodexTmuxConfig, *, max_wakes: int | None) -> int:
        captured["config"] = config
        captured["max_wakes"] = max_wakes
        return 0

    ns = argparse.Namespace(
        codex_tmux_command="wait",
        identity="SYNAPSE-CHANNEL/codex-main",
        session="synapse-codex-main",
        cwd=tmp_path,
        codex_command="codex",
        tmux_bin="tmux",
        synapse_bin="synapse",
        uri="ws://localhost:8876",
        wake_jitter=0.0,
        max_wakes=3,
        token=None,
    )

    assert cli_codex_tmux._cmd_codex_tmux(ns, waiter=waiter) == 0
    assert captured["config"].identity == "SYNAPSE-CHANNEL/codex-main"
    assert captured["max_wakes"] == 3
