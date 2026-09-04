# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real-process active-waker lifecycle journey
"""Exercise install, inhibit, resume, and status through the packaged CLI process."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from synapse_channel.waker_config import DESIRED_ARMED, DESIRED_INHIBITED, load_waker_config

REPO_ROOT = Path(__file__).resolve().parents[1]
IDENTITY = "journey/codex-1"


def _run_cli(home: Path, fake_bin: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env.update(
        {
            "HOME": str(home),
            "PATH": f"{fake_bin}:{env['PATH']}",
            "PYTHONPATH": str(REPO_ROOT / "src"),
        }
    )
    return subprocess.run(
        [sys.executable, "-m", "synapse_channel.cli", *arguments],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


@pytest.mark.parametrize("first", ["stop", "resume", "install"])
@pytest.mark.parametrize("second", ["stop", "resume", "install"])
def test_cli_rejects_competing_lifecycle_until_service_command_finishes(
    tmp_path: Path, first: str, second: str
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    escape = fake_bin / "systemd-escape"
    escape.write_text(
        "#!/bin/sh\nprintf '%s\\n' 'synapse-waker@journey-codex-1.service'\n",
        encoding="utf-8",
    )
    escape.chmod(0o755)
    systemctl = fake_bin / "systemctl"
    systemctl.write_text(
        f"#!{sys.executable}\n"
        "import os, pathlib, sys, time\n"
        "home = pathlib.Path(os.environ['HOME'])\n"
        "with (home / 'commands').open('a') as log:\n"
        "    log.write(' '.join(sys.argv[1:]) + '\\n')\n"
        "if (home / 'block').exists():\n"
        "    (home / 'entered').touch()\n"
        "    deadline = time.monotonic() + 20\n"
        "    while not (home / 'release').exists():\n"
        "        if time.monotonic() >= deadline:\n"
        "            sys.exit(7)\n"
        "        time.sleep(0.01)\n",
        encoding="utf-8",
    )
    systemctl.chmod(0o755)
    install = (
        "waker",
        "install",
        "--identity",
        IDENTITY,
        "--session",
        "journey",
        "--cwd",
        str(REPO_ROOT),
        "--agent-command",
        "codex",
        "--synapse-bin",
        "/usr/bin/synapse",
        "--start",
    )
    assert _run_cli(tmp_path, fake_bin, *install).returncode == 0

    def arguments(operation: str, generation: int) -> tuple[str, ...]:
        if operation == "install":
            return install
        result = (
            "waker",
            operation,
            "--identity",
            IDENTITY,
            "--expect-generation",
            str(generation),
        )
        return result + (("--reason", "lifecycle test") if operation == "stop" else ())

    (tmp_path / "block").touch()
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(_run_cli, tmp_path, fake_bin, *arguments(first, 1))
        try:
            deadline = time.monotonic() + 10
            while not (tmp_path / "entered").exists():
                assert not pending.done(), pending.result() if pending.done() else ""
                assert time.monotonic() < deadline, "first command did not start"
                time.sleep(0.01)
            before = load_waker_config(IDENTITY, home=tmp_path)
            commands = (tmp_path / "commands").read_text()
            rejected = _run_cli(tmp_path, fake_bin, *arguments(second, 2))
            assert rejected.returncode != 0, rejected.stdout
            assert "lifecycle is already changing" in rejected.stdout + rejected.stderr
            assert load_waker_config(IDENTITY, home=tmp_path) == before
            assert (tmp_path / "commands").read_text() == commands
        finally:
            (tmp_path / "release").touch()
            completed = pending.result(timeout=30)
    assert completed.returncode == 0, completed.stderr
    retried = _run_cli(tmp_path, fake_bin, *arguments(second, 2))
    assert retried.returncode == 0, retried.stderr
    assert load_waker_config(IDENTITY, home=tmp_path).generation == 3


def test_real_cli_process_preserves_terminal_while_controlling_exact_waker(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    systemd_escape = fake_bin / "systemd-escape"
    systemd_escape.write_text(
        "#!/bin/sh\nprintf '%s\\n' 'synapse-waker@journey-codex-1.service'\n",
        encoding="utf-8",
    )
    systemd_escape.chmod(0o755)
    systemctl = fake_bin / "systemctl"
    systemctl.write_text(
        "#!/bin/sh\n"
        'printf \'%s\\n\' "$*" >> "$HOME/systemctl.log"\n'
        'case "$*" in\n'
        "  *' show '*) printf '%s\\n' 'ActiveState=active' 'SubState=running' "
        "'NRestarts=1' 'ExecMainStatus=0' ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    systemctl.chmod(0o755)

    installed = _run_cli(
        tmp_path,
        fake_bin,
        "waker",
        "install",
        "--identity",
        IDENTITY,
        "--session",
        "journey-codex-1",
        "--cwd",
        str(REPO_ROOT),
        "--agent-command",
        "codex --model gpt-5",
        "--synapse-bin",
        "/usr/bin/synapse",
        "--start",
    )
    assert installed.returncode == 0, installed.stderr
    assert load_waker_config(IDENTITY, home=tmp_path).desired_state == DESIRED_ARMED

    stopped = _run_cli(
        tmp_path,
        fake_bin,
        "waker",
        "stop",
        "--identity",
        IDENTITY,
        "--reason",
        "journey malfunction",
        "--expect-generation",
        "1",
    )
    assert stopped.returncode == 0, stopped.stderr
    assert load_waker_config(IDENTITY, home=tmp_path).desired_state == DESIRED_INHIBITED

    inhibited_run = _run_cli(tmp_path, fake_bin, "waker", "run", "--identity", IDENTITY)
    assert inhibited_run.returncode == 78
    assert "is inhibited" in inhibited_run.stdout

    resumed = _run_cli(
        tmp_path,
        fake_bin,
        "waker",
        "resume",
        "--identity",
        IDENTITY,
        "--expect-generation",
        "2",
    )
    assert resumed.returncode == 0, resumed.stderr
    assert load_waker_config(IDENTITY, home=tmp_path).desired_state == DESIRED_ARMED

    status = _run_cli(tmp_path, fake_bin, "waker", "status", "--identity", IDENTITY)
    assert status.returncode == 1
    assert "service: active/running" in status.stdout
    assert "provider: unavailable" in status.stdout

    commands = (tmp_path / "systemctl.log").read_text(encoding="utf-8").splitlines()
    assert "--user stop synapse-waker@journey-codex-1.service" in commands
    assert all("kill" not in command for command in commands)
