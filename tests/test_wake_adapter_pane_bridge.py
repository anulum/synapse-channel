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
import os
import shutil
import socket as netsock
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
    build_wake_prompt,
    start_session,
)
from synapse_channel.agent_tmux_registry import registry_path
from synapse_channel.agent_tmux_types import AgentTmuxConfig
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
        self.socket_name = socket
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
        done = subprocess.run(  # the test's own tmux wrapper
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

    def socket_file(self) -> Path:
        """Return the socket this server reports, or the path ``-L`` would use.

        ``kill-server`` leaves the socket behind. When the server is already
        gone, the path is the tmux default for this socket name.
        """
        shown = subprocess.run(
            [str(self.tmux), "display-message", "-p", "#{socket_path}"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        reported = shown.stdout.strip()
        if shown.returncode == 0 and reported:
            return Path(reported)
        root = Path(os.environ.get("TMUX_TMPDIR", "/tmp"))
        return root / f"tmux-{os.getuid()}" / self.socket_name

    def stop(self) -> Path:
        """Stop this private server and unlink the socket ``kill-server`` leaves.

        Returns
        -------
        pathlib.Path
            The socket path that was removed, whether or not the server still answered.
        """
        socket = self.socket_file()
        subprocess.run(
            [str(self.tmux), "kill-server"],
            capture_output=True,
            check=False,
            timeout=10,
        )
        socket.unlink(missing_ok=True)
        return socket


def server_answers(socket: Path) -> bool:
    """Return whether a tmux server answers on ``socket``.

    ``list-sessions`` does not start a server. A socket left after
    ``kill-server`` does not answer.
    """
    done = subprocess.run(
        ["tmux", "-S", str(socket), "list-sessions"],
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    return done.returncode == 0


def remove_unanswered_test_sockets(directory: Path) -> list[Path]:
    """Unlink ``synapse-test-*`` sockets in ``directory`` when nothing answers.

    A socket whose server still answers is left in place.

    Returns
    -------
    list[pathlib.Path]
        The sockets that were removed, in name order.
    """
    removed: list[Path] = []
    if not directory.is_dir():
        return removed
    for socket in sorted(directory.glob("synapse-test-*")):
        if socket.is_dir() or server_answers(socket):
            continue
        socket.unlink(missing_ok=True)
        removed.append(socket)
    return removed


@pytest.fixture
def composer(tmp_path: Path) -> Iterator[Pane]:
    pane = Pane(tmp_path, _COMPOSER)
    assert start_session(pane.config).returncode == 0
    pane.wait_for("›")
    yield pane
    pane.stop()


@pytest.fixture
def dialog(tmp_path: Path) -> Iterator[Pane]:
    pane = Pane(tmp_path, _DIALOG)
    assert start_session(pane.config).returncode == 0
    pane.wait_for("2. No")
    yield pane
    pane.stop()


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


def test_the_pane_fixture_unlinks_the_socket_it_created(tmp_path: Path) -> None:
    """The fixture stop removes the real socket ``kill-server`` leaves behind."""
    pane = Pane(tmp_path, "#!/bin/sh\nsleep 60\n")
    started = subprocess.run(
        [str(pane.tmux), "new-session", "-d", "-s", pane.config.session, "sleep", "60"],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert started.returncode == 0, started.stderr
    socket = Path(pane.run("display-message", "-p", "#{socket_path}").strip())
    assert socket.name.startswith("synapse-test-")
    assert socket.exists()
    try:
        assert pane.stop() == socket
        assert not socket.exists()
    finally:
        subprocess.run(
            [str(pane.tmux), "kill-server"],
            capture_output=True,
            check=False,
            timeout=10,
        )
        socket.unlink(missing_ok=True)


def test_only_an_unanswered_test_socket_is_removed() -> None:
    """A live synapse-test server stays; a socket nothing answers on is removed."""
    directory = Path("/tmp") / f"c117-{uuid.uuid4().hex[:8]}"
    directory.mkdir()
    dead = directory / "synapse-test-dead"
    live = directory / "synapse-test-live"
    try:
        with netsock.socket(netsock.AF_UNIX) as held:
            held.bind(str(dead))
        started = subprocess.run(
            ["tmux", "-S", str(live), "new-session", "-d", "-s", "keep", "sleep", "60"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        assert started.returncode == 0, started.stderr
        assert server_answers(live)
        assert not server_answers(dead)
        assert remove_unanswered_test_sockets(directory) == [dead]
        assert not dead.exists()
        assert live.exists()
        assert server_answers(live)
    finally:
        subprocess.run(
            ["tmux", "-S", str(live), "kill-server"],
            capture_output=True,
            check=False,
            timeout=10,
        )
        live.unlink(missing_ok=True)
        dead.unlink(missing_ok=True)
        directory.rmdir()
