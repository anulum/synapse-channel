# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — actual pane disappearance during wake staging
"""Prove real tmux wakes: a paste failure, a dropped Enter, and a stage the registry lost."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path

import pytest

from synapse_channel.agent_tmux import (
    BINDING_REFUSAL_EXIT_CODE,
    _write_registry,
    build_wake_prompt,
    inject_wake,
    registry_path,
    start_session,
)
from synapse_channel.agent_tmux_types import AgentTmuxConfig

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
        identity="pane-wake/paste-failure",
        session="synapse-pane-wake-" + uuid.uuid4().hex[:12],
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
        self.paste_attempts = 0
        self.prepared_before_paste = False

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
        if args[1] == "paste-buffer":
            prepared = json.loads(registry_path(self.config).read_text())
            self.prepared_before_paste = (
                prepared["pending_wake"] is True and prepared["wake_prompt_staged"] is True
            )
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
            self.paste_attempts += 1
            self.paste_returncode = result.returncode
        return result


def test_actual_pane_disappearance_preserves_an_uncertain_paste_without_delivery(
    tmp_path: Path,
) -> None:
    """Persist before the real attempted paste and never repaste into a missing target."""
    with idle_pane(tmp_path) as config:
        runner = PaneDisappears(config)
        try:
            result = inject_wake(config, runner=runner)
            assert runner.paste_returncode is not None and runner.paste_returncode != 0
            assert runner.prepared_before_paste is True
            assert result.returncode == runner.paste_returncode
            assert not result.injected and not result.started
            record = json.loads(registry_path(config).read_text())
            assert record["pending_wake"] is True
            assert record["wake_prompt_staged"] is True
            assert record["last_inject_returncode"] == result.returncode
            retried = inject_wake(config, runner=runner)
            assert not retried.injected and retried.returncode == BINDING_REFUSAL_EXIT_CODE
            assert runner.paste_attempts == 1
        finally:
            if runner.buffer is not None:
                subprocess.run(
                    [config.tmux_bin, "delete-buffer", "-b", runner.buffer],
                    capture_output=True,
                    text=True,
                    check=False,
                )


_GROK_COMPOSER = r"""
import os
import sys
import tty

WIDTH = 60
log_path, mode = sys.argv[1], sys.argv[2]
drops = 1 if mode == "drop-first-enter" else 0
history = []
composer = ""


def log(line):
    with open(log_path, "a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def rows():
    wrapped = []
    for word in composer.split():
        if wrapped and len(wrapped[-1]) + 1 + len(word) <= WIDTH:
            wrapped[-1] += " " + word
        else:
            wrapped.append(word)
    return wrapped or [""]


def draw():
    out = ["\x1b[2J\x1b[H"]
    out += ["     ❯ " + turn[:40] + "\r\n" for turn in history[-4:]]
    out.append("  ╭" + "─" * (WIDTH + 4) + "╮\r\n")
    for index, row in enumerate(rows()):
        out.append("  │ " + ("❯ " if index == 0 else "  ") + row.ljust(WIDTH) + " │\r\n")
    out.append("  ╰" + "─" * (WIDTH + 4) + "╯\r\n")
    out.append("  Enter:send  │  Ctrl+x:quit\r\n")
    os.write(1, "".join(out).encode())


tty.setraw(0)
os.write(1, b"\x1b[?2004h")
draw()
pending = b""
while True:
    data = os.read(0, 65536)
    if not data:
        break
    pending += data
    while pending:
        if pending.startswith(b"\x1b[200~"):
            end = pending.find(b"\x1b[201~")
            if end < 0:
                break
            composer += pending[6:end].decode()
            pending = pending[end + 6 :]
            log("paste")
        elif pending.startswith(b"\r"):
            pending = pending[1:]
            if drops:
                drops -= 1
                log("dropped")
            else:
                history.append(composer)
                log("submit " + composer)
                composer = ""
        else:
            pending = pending[1:]
    draw()
"""


@contextmanager
def grok_pane(root: Path, mode: str) -> Iterator[tuple[AgentTmuxConfig, Path]]:
    """Own a Grok-shaped TUI whose boxed composer wraps a long hint, and destroy it after."""
    tmux = shutil.which("tmux")
    assert tmux is not None
    script = root / "grok-composer.py"
    script.write_text(_GROK_COMPOSER, encoding="utf-8")
    log = root / "composer.log"
    config = AgentTmuxConfig(
        identity="pane-wake/wrapped-grok",
        session="synapse-pane-wake-" + uuid.uuid4().hex[:12],
        cwd=root,
        agent_command=(sys.executable, str(script), str(log), mode),
        tmux_bin=tmux,
        registry_dir=root / "registry",
        submit_delay=0.3,
    )
    try:
        assert start_session(config).started
        _wait_for_screen(config, "Enter:send")
        yield config, log
    finally:
        subprocess.run(
            [tmux, "kill-session", "-t", config.session],
            capture_output=True,
            text=True,
            check=False,
        )


def _wait_for_screen(config: AgentTmuxConfig, text: str) -> None:
    """Wait a bounded time until the pane shows ``text``."""
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        capture = subprocess.run(
            [config.tmux_bin, "capture-pane", "-p", "-t", config.session],
            capture_output=True,
            text=True,
            check=False,
        )
        if text in capture.stdout:
            return
        time.sleep(0.02)
    raise AssertionError("the composer did not draw in time")


def test_a_wrapped_grok_hint_whose_first_enter_is_dropped_is_submitted_by_the_retry(
    tmp_path: Path,
) -> None:
    """The wrapped hint stays staged after a dropped Enter, and the retry submits it once."""
    with grok_pane(tmp_path, "drop-first-enter") as (config, log):
        first = inject_wake(config)
        assert not first.injected and first.returncode == 0
        record = json.loads(registry_path(config).read_text())
        assert record["pending_wake"] is True and record["wake_prompt_staged"] is True

        second = inject_wake(config)
        assert second.injected
        record = json.loads(registry_path(config).read_text())
        assert record["pending_wake"] is False and record["wake_prompt_staged"] is False
        hint = build_wake_prompt(config.identity)
        assert log.read_text(encoding="utf-8").splitlines() == [
            "paste",
            "dropped",
            f"submit {hint}",
        ]


def test_a_composer_holding_the_hint_the_registry_lost_is_adopted_without_a_second_paste(
    tmp_path: Path,
) -> None:
    """A pending wake whose registry dropped the stage is submitted, never pasted again."""
    with grok_pane(tmp_path, "accept") as (config, log):
        hint = build_wake_prompt(config.identity)
        buffer = "synapse-test-" + uuid.uuid4().hex[:12]
        for command in (
            ["set-buffer", "-b", buffer, "--", hint],
            ["paste-buffer", "-b", buffer, "-d", "-p", "-t", config.session],
        ):
            done = subprocess.run(
                [config.tmux_bin, *command], capture_output=True, text=True, check=False
            )
            assert done.returncode == 0
        _wait_for_screen(config, "Reply once only")
        _write_registry(
            config, last_inject_returncode=0, pending_wake=True, wake_prompt_staged=False
        )

        result = inject_wake(config)
        assert result.injected
        assert log.read_text(encoding="utf-8").splitlines() == ["paste", f"submit {hint}"]
        record = json.loads(registry_path(config).read_text())
        assert record["pending_wake"] is False
