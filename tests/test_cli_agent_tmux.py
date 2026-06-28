# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the generic tmux-backed agent wake CLI

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from synapse_channel import cli, cli_agent_tmux
from synapse_channel.agent_tmux import AgentTmuxConfig, AgentTmuxStatus, AgentTmuxWakeResult


def test_parser_registers_agent_tmux_start_with_agent_command() -> None:
    args = cli.build_parser().parse_args(
        [
            "agent-tmux",
            "start",
            "--identity",
            "user/terminal-1135378",
            "--session",
            "synapse-user_terminal-1135378",
            "--cwd",
            "/repo",
            "--agent-command",
            "kimi",
        ]
    )

    assert args.func is cli_agent_tmux._cmd_agent_tmux
    assert args.agent_tmux_command == "start"
    assert args.identity == "user/terminal-1135378"
    assert args.cwd == Path("/repo")
    assert args.agent_command == "kimi"


def test_parser_registers_wait_resilience_options() -> None:
    args = cli.build_parser().parse_args(
        [
            "agent-tmux",
            "wait",
            "--identity",
            "user/terminal-1135378",
            "--session",
            "synapse-user_terminal-1135378",
            "--submit-delay",
            "0.7",
            "--max-wait-failures",
            "4",
        ]
    )

    assert args.agent_tmux_command == "wait"
    assert args.submit_delay == 0.7
    assert args.max_wait_failures == 4
    assert args.max_wakes is None
    # Default agent command is codex when the flag is omitted.
    assert args.agent_command == "codex"


def test_cmd_start_dispatches_and_splits_agent_command(capsys: Any, tmp_path: Path) -> None:
    captured: dict[str, AgentTmuxConfig] = {}

    def starter(config: AgentTmuxConfig) -> AgentTmuxWakeResult:
        captured["config"] = config
        return AgentTmuxWakeResult(injected=False, started=True, returncode=0, detail="started")

    ns = argparse.Namespace(
        agent_tmux_command="start",
        identity="user/terminal-1135378",
        session="synapse-user_terminal-1135378",
        cwd=tmp_path,
        agent_command="kimi --flag",
        tmux_bin="tmux",
        synapse_bin="synapse",
        uri="ws://localhost:8876",
        submit_delay=0.4,
        max_wakes=1,
        token=None,
    )

    assert cli_agent_tmux._cmd_agent_tmux(ns, starter=starter) == 0
    assert captured["config"].agent_command == ("kimi", "--flag")
    assert "started" in capsys.readouterr().out


def test_cmd_status_dispatches(capsys: Any, tmp_path: Path) -> None:
    def status_runner(config: AgentTmuxConfig) -> AgentTmuxStatus:
        assert config.session == "synapse-user_terminal-1135378"
        return AgentTmuxStatus(
            identity=config.identity,
            session=config.session,
            session_exists=True,
            pane_command="fish",
            pane_start_command="kimi",
            agent_active=True,
        )

    ns = argparse.Namespace(
        agent_tmux_command="status",
        identity="user/terminal-1135378",
        session="synapse-user_terminal-1135378",
        cwd=tmp_path,
        agent_command="kimi",
        tmux_bin="tmux",
        synapse_bin="synapse",
        uri="ws://localhost:8876",
        submit_delay=0.4,
        max_wakes=1,
        token=None,
    )

    assert cli_agent_tmux._cmd_agent_tmux(ns, status_runner=status_runner) == 0
    out = capsys.readouterr().out
    assert "tmux session: online" in out
    assert "agent pane: active" in out


def test_cmd_wait_dispatches(tmp_path: Path) -> None:
    captured: dict[str, Any] = {}

    def waiter(
        config: AgentTmuxConfig, *, max_wakes: int | None, max_wait_failures: int | None
    ) -> int:
        captured["config"] = config
        captured["max_wakes"] = max_wakes
        captured["max_wait_failures"] = max_wait_failures
        return 0

    ns = argparse.Namespace(
        agent_tmux_command="wait",
        identity="user/terminal-1135378",
        session="synapse-user_terminal-1135378",
        cwd=tmp_path,
        agent_command="kimi",
        tmux_bin="tmux",
        synapse_bin="synapse",
        uri="ws://localhost:8876",
        submit_delay=0.4,
        max_wakes=3,
        max_wait_failures=5,
        token=None,
    )

    assert cli_agent_tmux._cmd_agent_tmux(ns, waiter=waiter) == 0
    assert captured["config"].agent_command == ("kimi",)
    assert captured["max_wakes"] == 3
    assert captured["max_wait_failures"] == 5
