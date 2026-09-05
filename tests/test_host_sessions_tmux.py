# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real isolated tmux metadata tests
"""Observe a disposable pane without capturing or modifying provider content."""

import shutil
import subprocess
import time
from pathlib import Path

import pytest

from synapse_channel.host_sessions_tmux import observe_tmux


def test_missing_socket_is_unavailable(tmp_path: Path) -> None:
    assert observe_tmux(str(tmp_path / "absent.sock")) == ((), "unavailable")


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux is not installed")
def test_real_detached_pane_and_identity(tmp_path: Path) -> None:
    socket = str(tmp_path / "tmux.sock")
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
