# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real isolated tmux metadata tests
"""Observe a disposable pane without capturing or modifying provider content."""

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from synapse_channel.host_sessions_tmux import observe_tmux


def test_missing_socket_is_unavailable(tmp_path: Path) -> None:
    assert observe_tmux(str(tmp_path / "absent.sock")) == ((), "unavailable")


def _tmux_on_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str) -> Path:
    """Put an executable named ``tmux`` first on PATH; it records its arguments."""
    directory = tmp_path / "bin"
    directory.mkdir()
    program = directory / "tmux"
    program.write_text(
        f'#!/bin/sh\nprintf "%s\\n" "$@" > "{tmp_path}/argv"\n{body}\n', encoding="utf-8"
    )
    program.chmod(0o755)
    monkeypatch.setenv("PATH", f"{directory}{os.pathsep}{os.environ['PATH']}")
    return tmp_path / "argv"


def test_tmux_missing_from_path_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    assert observe_tmux() == ((), "unavailable")


def test_no_socket_reads_the_default_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    argv = _tmux_on_path(tmp_path, monkeypatch, r"printf '42\t$1\t%%3\t0\t\t\n'")
    panes, status = observe_tmux()
    assert status == "complete"
    assert [(pane.pid, pane.session, pane.pane, pane.attached) for pane in panes] == [
        (42, "$1", "%3", False)
    ]
    assert panes[0].identity is None and panes[0].project is None
    assert argv.read_text(encoding="utf-8").splitlines()[0] == "list-panes"


def test_a_silent_tmux_is_stopped_at_the_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _tmux_on_path(tmp_path, monkeypatch, "exec sleep 30")
    started = time.monotonic()
    assert observe_tmux() == ((), "partial")
    assert time.monotonic() - started < 5


def test_a_tmux_that_closes_its_output_but_keeps_running_is_stopped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _tmux_on_path(tmp_path, monkeypatch, "exec >&-\nexec sleep 30")
    started = time.monotonic()
    assert observe_tmux() == ((), "partial")
    assert time.monotonic() - started < 5


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux is not installed")
@pytest.mark.parametrize("socket_name", ["tmux.sock", "tmux observation.sock"])
def test_real_detached_pane_and_identity(tmp_path: Path, socket_name: str) -> None:
    socket = str(tmp_path / socket_name)
    command = ["tmux", "-S", socket]
    subprocess.run(
        command
        + [
            "new-session",
            "-d",
            "-s",
            "fixture",
            "-e",
            "SYN_PROJECT=MONITOR-TEST",
            "-e",
            "SYN_IDENTITY=MONITOR-TEST/fixture",
            "sleep 120",
        ],
        check=True,
        timeout=5,
    )
    try:
        panes, status = observe_tmux(socket)
        assert status == "complete" and len(panes) == 1
        pane = panes[0]
        assert pane.pid > 0 and pane.pane.startswith("%") and pane.session.startswith("$")
        assert pane.attached is False
        assert pane.identity == "MONITOR-TEST/fixture"
        assert pane.project == "MONITOR-TEST"
        with subprocess.Popen(
            command + ["-C", "attach-session", "-t", "fixture"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ) as client:
            try:
                deadline = time.monotonic() + 5
                while True:
                    connected, status = observe_tmux(socket)
                    if connected and connected[0].attached:
                        break
                    assert client.poll() is None, "disposable tmux client exited"
                    assert time.monotonic() < deadline, "disposable client did not attach"
                    time.sleep(0.01)
                assert status == "complete" and connected[0].pid == pane.pid
                assert connected[0].session == pane.session
            finally:
                try:
                    client.communicate("detach-client\n", timeout=5)
                except subprocess.TimeoutExpired:
                    client.kill()
                    client.communicate(timeout=5)
        detached, status = observe_tmux(socket)
        assert status == "complete" and detached[0].attached is False
        subprocess.run(
            command
            + [
                "set-environment",
                "-t",
                "fixture",
                "SYN_IDENTITY",
                "MONITOR-TEST/fixture\ninjected",
            ],
            check=True,
            timeout=5,
        )
        assert observe_tmux(socket) == ((), "partial")
        subprocess.run(
            command + ["set-environment", "-t", "fixture", "SYN_IDENTITY", "x" * 4096],
            check=True,
            timeout=5,
        )
        for index in range(17):
            session = f"linked-{index}"
            subprocess.run(
                command + ["new-session", "-d", "-t", "fixture", "-s", session],
                check=True,
                timeout=5,
            )
            subprocess.run(
                command + ["set-environment", "-t", session, "SYN_IDENTITY", "x" * 4096],
                check=True,
                timeout=5,
            )
        assert observe_tmux(socket) == ((), "partial")
    finally:
        subprocess.run(command + ["kill-server"], check=False, timeout=5)
