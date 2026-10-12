# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — actual pane disappearance during wake staging
"""Prove a real tmux paste failure retains a retryable, unstaged wake."""

from __future__ import annotations

import json
import shutil
import subprocess
import time
import uuid
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path

import pytest

from synapse_channel.agent_tmux import AgentTmuxConfig, inject_wake, registry_path, start_session

pytestmark = pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux is not installed")


@contextmanager
def idle_pane(root: Path) -> Iterator[AgentTmuxConfig]:
    """Own one harmless Codex-shaped composer and always destroy its exact pane."""
    tmux = shutil.which("tmux")
    assert tmux is not None
    provider = root / "codex-fixture"
    provider.write_text(
        "#!/bin/sh\nprintf '\\033[999B› \\n'\n"
        "while IFS= read -r line; do printf '%s\\n› \\n' \"$line\"; done\n"
    )
    provider.chmod(0o700)
    config = AgentTmuxConfig(
        identity="R8/paste-failure",
        session="synapse-r8-" + uuid.uuid4().hex[:12],
        cwd=root,
        agent_command=(str(provider),),
        tmux_bin=tmux,
        registry_dir=root / "registry",
        submit_delay=0.01,
    )
    try:
        assert start_session(config).started
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            capture = subprocess.run(
                [tmux, "capture-pane", "-p", "-t", config.session],
                capture_output=True,
                text=True,
                check=False,
            )
            if "›" in capture.stdout:
                break
            time.sleep(0.02)
        else:
            raise AssertionError("owned composer did not reach idle")
        yield config
    finally:
        subprocess.run(
            [tmux, "kill-session", "-t", config.session],
            capture_output=True,
            text=True,
            check=False,
        )


class PaneDisappears:
    """Run every real command and remove only our pane after its buffer is set."""

    def __init__(self, config: AgentTmuxConfig) -> None:
        """Retain the exact task-owned session and eventual buffer name."""
        self.config = config
        self.buffer: str | None = None
        self.paste_returncode: int | None = None

    def __call__(
        self,
        args: Sequence[str],
        *,
        capture_output: bool = False,
        text: bool = False,
        check: bool = False,
        env: Mapping[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Cause an actual tmux race rather than manufacturing a return value."""
        result = subprocess.run(
            args,
            capture_output=capture_output,
            text=text,
            check=check,
            env=env,
        )
        if args[1] == "set-buffer" and result.returncode == 0:
            self.buffer = args[3]
            killed = subprocess.run(
                [self.config.tmux_bin, "kill-session", "-t", self.config.session],
                capture_output=True,
                text=True,
                check=False,
            )
            assert killed.returncode == 0
        if args[1] == "paste-buffer":
            self.paste_returncode = result.returncode
        return result


def test_actual_pane_disappearance_does_not_claim_a_staged_or_delivered_wake(
    tmp_path: Path,
) -> None:
    """A failed real paste queues retry and cannot report staging or delivery."""
    with idle_pane(tmp_path) as config:
        runner = PaneDisappears(config)
        try:
            result = inject_wake(config, runner=runner)
            assert runner.paste_returncode is not None and runner.paste_returncode != 0
            assert result.returncode == runner.paste_returncode
            assert not result.injected and not result.started
            record = json.loads(registry_path(config).read_text())
            assert record["pending_wake"] is True
            assert record["wake_prompt_staged"] is False
            assert record["last_inject_returncode"] == result.returncode
        finally:
            if runner.buffer is not None:
                subprocess.run(
                    [config.tmux_bin, "delete-buffer", "-b", runner.buffer],
                    capture_output=True,
                    text=True,
                    check=False,
                )
