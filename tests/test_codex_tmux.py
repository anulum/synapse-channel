# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — Codex-named compatibility surface over the generic agent waker

from __future__ import annotations

import shutil
import subprocess
import time
import uuid
from pathlib import Path

import pytest

from synapse_channel import agent_tmux, codex_tmux
from synapse_channel.codex_tmux import (
    CODEX_PANE_COMMANDS,
    CodexTmuxConfig,
    CodexTmuxStatus,
    CodexTmuxWakeResult,
    inject_wake,
    registry_path,
)


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


def test_inject_wake_through_codex_surface_uses_safe_bracketed_delivery(tmp_path: Path) -> None:
    tmux = shutil.which("tmux")
    if tmux is None:
        pytest.skip("tmux is not installed")

    provider = tmp_path / "codex-fixture-recording"
    submissions = tmp_path / "codex-fixture-recording.submissions"
    provider.write_text(
        "#!/bin/sh\nprintf '\\033[999B› \\n'\nwhile IFS= read -r line; do\n"
        '  printf \'%s\\n\' "$line" >> "$0.submissions"\n'
        "  printf '%s\\n› \\n' \"$line\"\ndone\n",
        encoding="utf-8",
    )
    provider.chmod(0o700)

    config = CodexTmuxConfig(
        identity="SYNAPSE-CHANNEL/codex-main",
        session=f"synapse-codex-real-{uuid.uuid4().hex[:12]}",
        cwd=tmp_path,
        agent_command=(str(provider),),
        tmux_bin=tmux,
        registry_dir=tmp_path / "registry",
        submit_delay=0.1,
    )
    try:
        started = codex_tmux.start_session(config)
        assert started.started is True, started.detail

        for _ in range(80):
            pane = subprocess.run(
                [tmux, "capture-pane", "-t", config.session, "-p", "-J"],
                capture_output=True,
                text=True,
                check=False,
            )
            if pane.returncode == 0 and "›" in pane.stdout:
                break
            time.sleep(0.025)
        else:
            raise AssertionError("real tmux provider did not render its idle composer")

        result = inject_wake(config)

        assert result.injected is True, result.detail
        assert result.detail == "injected and consumption observed"
        assert submissions.read_text(encoding="utf-8").splitlines() == [
            agent_tmux.build_wake_prompt(config.identity)
        ]
        payload = registry_path(config).read_text(encoding="utf-8")
        assert '"pending_wake": false' in payload
        assert '"wake_prompt_staged": false' in payload
    finally:
        subprocess.run(
            [tmux, "kill-session", "-t", config.session],
            capture_output=True,
            text=True,
            check=False,
        )
