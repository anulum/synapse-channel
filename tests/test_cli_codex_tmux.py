# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — Codex-named alias of the generic agent-tmux wake CLI

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from synapse_channel import cli, cli_agent_tmux, cli_codex_tmux
from synapse_channel.agent_tmux import AgentTmuxConfig, AgentTmuxWakeResult


def test_codex_tmux_command_is_registered_with_codex_flag() -> None:
    args = cli.build_parser().parse_args(
        [
            "codex-tmux",
            "start",
            "--identity",
            "SYNAPSE-CHANNEL/codex-main",
            "--session",
            "synapse-codex-main",
            "--codex-command",
            "codex --sandbox never",
        ]
    )

    # The Codex alias dispatches the same generic handler and resolves the
    # --codex-command flag into the shared agent_command destination.
    assert args.func is cli_agent_tmux._cmd_agent_tmux
    assert args.agent_tmux_command == "start"
    assert args.agent_command == "codex --sandbox never"


def test_codex_tmux_default_command_is_codex() -> None:
    args = cli.build_parser().parse_args(
        [
            "codex-tmux",
            "wait",
            "--identity",
            "SYNAPSE-CHANNEL/codex-main",
            "--session",
            "synapse-codex-main",
        ]
    )

    assert args.agent_command == "codex"
    assert args.max_wait_failures is None


def test_codex_tmux_dispatch_splits_command(capsys: Any, tmp_path: Path) -> None:
    captured: dict[str, AgentTmuxConfig] = {}

    def starter(config: AgentTmuxConfig) -> AgentTmuxWakeResult:
        captured["config"] = config
        return AgentTmuxWakeResult(injected=False, started=True, returncode=0, detail="started")

    ns = argparse.Namespace(
        agent_tmux_command="start",
        identity="SYNAPSE-CHANNEL/codex-main",
        session="synapse-codex-main",
        cwd=tmp_path,
        agent_command="codex --sandbox danger-full-access",
        tmux_bin="tmux",
        synapse_bin="synapse",
        uri="ws://localhost:8876",
        submit_delay=0.4,
        max_wakes=1,
        token=None,
    )

    assert cli_codex_tmux._cmd_agent_tmux(ns, starter=starter) == 0
    assert captured["config"].agent_command == ("codex", "--sandbox", "danger-full-access")
    assert "started" in capsys.readouterr().out
