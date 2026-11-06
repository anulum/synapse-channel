# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — bridge wait selection, legacy fallback and parent-bound waits
"""Exercise the bridge's wait choice and the parent-bound ``synapse wait`` for real."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from cli_e2e_helpers import _candidate_environment, isolated_hub, run_cli
from synapse_channel import agent_tmux
from synapse_channel.agent_tmux import AgentTmuxConfig, _bridge_wait, _retry_exit


def _finished(returncode: int) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["synapse", "wait"], returncode, "", "")


def test_retry_exit_waits_for_the_failure_limit() -> None:
    """Retries continue below the limit or without one, and then exit with the right code."""
    assert _retry_exit(5, None, False, _finished(1)) is None
    assert _retry_exit(1, 2, False, _finished(1)) is None
    assert _retry_exit(2, 2, False, _finished(1)) == 1
    assert _retry_exit(2, 2, True, _finished(0)) == 3


def _legacy_synapse(tmp_path: Path) -> tuple[Path, Path]:
    """Write a ``synapse`` whose ``wait`` predates ``--exit-with-parent``."""
    calls = tmp_path / "legacy.calls"
    binary = tmp_path / "synapse-legacy"
    binary.write_text(
        f"#!{sys.executable}\n"
        "import argparse, sys\n"
        f"open({str(calls)!r}, 'a').write(' '.join(sys.argv[1:]) + '\\n')\n"
        "parser = argparse.ArgumentParser()\n"
        "wait = parser.add_subparsers(dest='command').add_parser('wait')\n"
        "for option in ('--name', '--for', '--timeout', '--wake-capability', '--uri'):\n"
        "    wait.add_argument(option)\n"
        "wait.add_argument('--directed-only', action='store_true')\n"
        "parser.parse_args()\n"
        "sys.exit(2)\n",
        encoding="utf-8",
    )
    binary.chmod(0o700)
    return binary, calls


def test_an_old_synapse_keeps_the_bridge_on_the_one_shot_wait(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ``synapse`` that refuses the parent binding is detected once and then used one-shot."""
    monkeypatch.setattr(agent_tmux, "_ONE_SHOT_WAIT_BINARIES", set())
    binary, calls = _legacy_synapse(tmp_path)
    config = AgentTmuxConfig(
        identity="LEGACY/seat",
        session="unused",
        cwd=tmp_path,
        synapse_bin=str(binary),
        pane_probe_interval=30.0,
    )
    first, refusal = _bridge_wait(config, runner=subprocess.run, heartbeat=None)
    assert (first.returncode, refusal) == (2, 0)
    assert agent_tmux._ONE_SHOT_WAIT_BINARIES == {str(binary)}
    second, refusal = _bridge_wait(config, runner=subprocess.run, heartbeat=None)
    assert (second.returncode, refusal) == (2, 0)
    lines = calls.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    assert "--exit-with-parent" in lines[0]
    assert all("--exit-with-parent" not in line for line in lines[1:])
    assert all("--timeout 30" in line for line in lines[1:])


def test_a_wait_bound_to_a_process_that_is_not_its_parent_never_connects() -> None:
    """The CLI refuses at once, before contacting the hub, when its parent is not the bridge."""
    started = time.monotonic()
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "synapse_channel.cli",
            "wait",
            "--name",
            "BOUND/seat-rx",
            "--for",
            "BOUND/seat",
            "--timeout",
            "30",
            "--uri",
            "ws://127.0.0.1:9",
            "--exit-with-parent",
            str(os.getpid() + 1_000_000),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        env=_candidate_environment(None),
        check=False,
    )
    assert result.returncode == 2, result.stdout + result.stderr
    assert result.stdout == ""
    assert time.monotonic() - started < 20


def test_a_parent_bound_wait_leaves_the_hub_when_its_parent_exits(tmp_path: Path) -> None:
    """Even with the kernel signal ignored, the wait notices its new parent and disconnects."""
    name = "BOUND/orphan-rx"
    with isolated_hub(tmp_path) as hub:
        parent = (
            "import os, signal, subprocess, sys\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "subprocess.Popen([sys.executable, '-m', 'synapse_channel.cli', 'wait', '--name',"
            f" {name!r}, '--for', 'BOUND/orphan', '--timeout', '60', '--uri', sys.argv[1],"
            " '--exit-with-parent', str(os.getpid())])\n"
            "sys.stdin.readline()\n"
            "os._exit(0)\n"
        )
        bridge = subprocess.Popen(
            [sys.executable, "-c", parent, hub.uri],
            stdin=subprocess.PIPE,
            text=True,
            env=_candidate_environment(None),
        )
        try:
            deadline = time.monotonic() + 15
            while name not in run_cli("who", uri=hub.uri).stdout:
                assert time.monotonic() < deadline, "the bound wait did not connect"
                time.sleep(0.1)
            assert bridge.stdin is not None
            bridge.stdin.write("exit\n")
            bridge.stdin.flush()
            assert bridge.wait(timeout=10) == 0
            deadline = time.monotonic() + 10
            while name in run_cli("who", uri=hub.uri).stdout:
                assert time.monotonic() < deadline, "the orphaned wait stayed connected"
                time.sleep(0.1)
        finally:
            if bridge.poll() is None:
                bridge.kill()
                bridge.wait(timeout=10)
            if bridge.stdin is not None:
                bridge.stdin.close()
