# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the pane-bridge wake adapter on a real tmux server
"""The pane-bridge adapter against a tmux server of the test's own.

The server runs on a private socket with no user configuration, so the
person's own tmux server is never touched. The pane runs a small program
that shows an idle composer and writes every line submitted to it into a
file; "one wake" is one line in that file.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest

from synapse_channel import wake_marks
from synapse_channel.agent_tmux import (
    AgentTmuxConfig,
    build_wake_prompt,
    registry_path,
    start_session,
)
from synapse_channel.wake_adapters import ACCEPTED, REFUSED, UNCERTAIN, WakeTarget
from synapse_channel.wake_adapters.pane_bridge import KIND, PROVES, PaneBridgeAdapter
from synapse_channel.wake_relay import WAKE_FAILED, relay

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux") or shutil.which("tmux") is None,
    reason="needs tmux and a pseudo-terminal on Linux",
)

SEAT = "E2EWAKE/seat-aaaa"
TARGET = WakeTarget(SEAT, "native-session-1", "this-host")

_COMPOSER = (
    "#!/bin/sh\nprintf '\\033[999B› \\n'\nwhile IFS= read -r line; do\n"
    '  printf \'%s\\n\' "$line" >> "$0.submissions"\n'
    "  printf '%s\\n› \\n' \"$line\"\ndone\n"
)
_DIALOG = (
    "#!/bin/sh\nprintf '\\n\\n\\n\\n\\n\\n\\n\\n\\n\\n\\n\\n\\n\\n\\n\\n"
    "Allow Codex to run this command?\\n› 1. Yes\\n  2. No\\n'\n"
    'if IFS= read -r selection; then printf \'%s\\n\' "$selection" >> "$0.submissions"; fi\n'
    "sleep 600\n"
)


class Pane:
    """One tmux session of the test, its bridge configuration and what it received."""

    def __init__(self, tmp_path: Path, program: str) -> None:
        socket = f"synapse-test-{uuid.uuid4().hex[:10]}"
        self.tmux = tmp_path / "tmux-private"
        self.tmux.write_text(
            f'#!/bin/sh\nexec {shutil.which("tmux")} -L {socket} -f /dev/null "$@"\n',
            encoding="utf-8",
        )
        self.tmux.chmod(0o700)
        provider = tmp_path / "codex-fixture"
        provider.write_text(program, encoding="utf-8")
        provider.chmod(0o700)
        self.submissions = Path(f"{provider}.submissions")
        self.config = AgentTmuxConfig(
            identity=SEAT,
            session=f"synapse-test-{uuid.uuid4().hex[:8]}",
            cwd=tmp_path,
            agent_command=(str(provider),),
            tmux_bin=str(self.tmux),
            registry_dir=tmp_path / "registry",
            submit_delay=0.2,
        )

    def run(self, *args: str) -> str:
        done = subprocess.run(  # noqa: S603 - the test's own tmux wrapper
            [str(self.tmux), *args], capture_output=True, text=True, check=False
        )
        return done.stdout

    def wait_for(self, text: str) -> None:
        for _ in range(200):
            if text in self.run("capture-pane", "-t", self.config.session, "-p", "-J"):
                return
            time.sleep(0.025)
        raise AssertionError(f"the pane never showed {text!r}")

    def lines(self) -> list[str]:
        if not self.submissions.exists():
            return []
        return self.submissions.read_text(encoding="utf-8").splitlines()

    def pending(self) -> bool:
        path = registry_path(self.config)
        return path.exists() and json.loads(path.read_text(encoding="utf-8"))["pending_wake"]


@pytest.fixture
def composer(tmp_path: Path) -> Iterator[Pane]:
    pane = Pane(tmp_path, _COMPOSER)
    assert start_session(pane.config).returncode == 0
    pane.wait_for("›")
    yield pane
    pane.run("kill-server")


@pytest.fixture
def dialog(tmp_path: Path) -> Iterator[Pane]:
    pane = Pane(tmp_path, _DIALOG)
    assert start_session(pane.config).returncode == 0
    pane.wait_for("2. No")
    yield pane
    pane.run("kill-server")


def _normal(text: str) -> str:
    return " ".join(text.split())


def test_an_idle_pane_is_woken_once_through_the_relay(tmp_path: Path, composer: Pane) -> None:
    adapter = PaneBridgeAdapter(lambda target: composer.config)
    marks = tmp_path / "marks"

    assert adapter.kind == KIND == "pane_bridge"
    assert adapter.available(TARGET).available
    first = relay(marks, TARGET, 501, [adapter])
    second = relay(marks, TARGET, 501, [adapter])

    assert (first.state, first.wake_path, first.proves) == (ACCEPTED, KIND, PROVES)
    assert first.sent_now and not second.sent_now and second.state == ACCEPTED
    (line,) = composer.lines()
    assert _normal(line) == _normal(build_wake_prompt(SEAT))
    assert not composer.pending()
    (mark,) = wake_marks.read(marks, SEAT, 501)
    assert "consumption observed" in mark.detail


def test_a_pane_with_a_dialog_open_is_passed_over_and_the_bridge_is_not_touched(
    tmp_path: Path, dialog: Pane
) -> None:
    adapter = PaneBridgeAdapter(lambda target: dialog.config)

    availability = adapter.available(TARGET)
    result = relay(tmp_path / "marks", TARGET, 502, [adapter])

    assert not availability.available and availability.detail.startswith("the pane is not idle (")
    assert (result.state, result.attempts) == (WAKE_FAILED, ())
    assert result.passed_over == ((KIND, availability.detail),)
    # Nothing was typed into the dialog and the bridge holds no wake of its own.
    assert dialog.lines() == [] and not dialog.pending()


def test_once_the_bridge_was_asked_and_could_not_finish_the_wake_is_uncertain(
    tmp_path: Path, dialog: Pane
) -> None:
    """The pane changed between the look and the wake: the bridge keeps the wake."""
    adapter = PaneBridgeAdapter(lambda target: dialog.config)

    outcome = adapter.wake(TARGET, 503, build_wake_prompt(SEAT), 5.0)

    assert (outcome.state, outcome.proves) == (UNCERTAIN, "")
    assert outcome.detail.startswith("the bridge holds the wake and may deliver it: wake queued")
    assert dialog.pending() and dialog.lines() == []


def test_a_missing_session_another_seat_and_a_foreign_prompt(
    tmp_path: Path, composer: Pane
) -> None:
    adapter = PaneBridgeAdapter(lambda target: composer.config)
    nobody = PaneBridgeAdapter(lambda target: None)
    other = WakeTarget("E2EWAKE/seat-bbbb")

    assert not nobody.available(TARGET).available
    assert adapter.available(other).detail == "no tmux pane is configured for exactly this seat"
    foreign = adapter.wake(TARGET, 1, "run this instead", 5.0)
    wrong_seat = adapter.wake(other, 1, build_wake_prompt(other.seat), 5.0)
    assert (foreign.state, wrong_seat.state) == (REFUSED, REFUSED)
    assert composer.lines() == [] and not composer.pending()

    # The session is bound to its seat: another seat's configuration of it is refused.
    borrowed = replace(composer.config, identity=other.seat)
    bound = PaneBridgeAdapter(lambda target: borrowed).available(other)
    assert not bound.available and bound.detail.startswith("session binding: ")
    # The pane runs something that this configuration does not call its agent.
    stranger = replace(
        composer.config, agent_command=("some-other-agent",), pane_commands=frozenset()
    )
    idle_other = PaneBridgeAdapter(lambda target: stranger).available(TARGET)
    assert (idle_other.available, idle_other.detail) == (False, "the pane does not run the agent")
    assert composer.lines() == [] and not composer.pending()

    composer.run("kill-server")
    gone = adapter.available(TARGET)
    assert (gone.available, gone.detail) == (False, "the tmux session is missing")
