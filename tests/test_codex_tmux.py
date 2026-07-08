# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — Codex-named compatibility surface over the generic agent waker

from __future__ import annotations

import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

from synapse_channel import agent_tmux, codex_tmux
from synapse_channel.codex_tmux import (
    CODEX_PANE_COMMANDS,
    CodexTmuxConfig,
    CodexTmuxStatus,
    CodexTmuxWakeResult,
    inject_wake,
    registry_path,
)


def _runner(*results: subprocess.CompletedProcess[str]) -> object:
    queue = list(results)

    def run(
        args: Sequence[str],
        *,
        capture_output: bool = False,
        text: bool = False,
        check: bool = False,
        env: Mapping[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        del capture_output, text, check, env
        if queue:
            return queue.pop(0)
        return subprocess.CompletedProcess(list(args), 0, "", "")

    return run


def test_codex_aliases_are_the_generic_agent_symbols() -> None:
    assert CodexTmuxConfig is agent_tmux.AgentTmuxConfig
    assert CodexTmuxStatus is agent_tmux.AgentTmuxStatus
    assert CodexTmuxWakeResult is agent_tmux.AgentTmuxWakeResult
    assert codex_tmux.inject_wake is agent_tmux.inject_wake
    assert codex_tmux.wait_and_wake is agent_tmux.wait_and_wake
    assert CODEX_PANE_COMMANDS is agent_tmux.DEFAULT_AGENT_PANE_COMMANDS


def test_codex_config_defaults_to_the_codex_launch_command(tmp_path: Path) -> None:
    config = CodexTmuxConfig(
        identity="SYNAPSE-CHANNEL/codex-main",
        session="synapse-codex-main",
        cwd=tmp_path,
        registry_dir=tmp_path / "registry",
    )

    assert config.agent_command == ("codex",)
    assert agent_tmux.agent_binary(config) == "codex"


def test_inject_wake_through_codex_surface_still_two_steps(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def run(
        args: Sequence[str],
        *,
        capture_output: bool = False,
        text: bool = False,
        check: bool = False,
        env: Mapping[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        del capture_output, text, check, env
        calls.append(list(args))
        return subprocess.CompletedProcess(list(args), 0, "", "")

    config = CodexTmuxConfig(
        identity="SYNAPSE-CHANNEL/codex-main",
        session="synapse-codex-main",
        cwd=tmp_path,
        registry_dir=tmp_path / "registry",
    )

    result = inject_wake(config, runner=run, sleeper=lambda _seconds: None)

    assert result.injected is True
    assert len(calls) == 2
    assert calls[0][:5] == ["tmux", "send-keys", "-t", "synapse-codex-main", "-l"]
    assert calls[1] == ["tmux", "send-keys", "-t", "synapse-codex-main", "Enter"]
    assert registry_path(config).exists()
