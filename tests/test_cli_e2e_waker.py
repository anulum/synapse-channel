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
from pathlib import Path

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
    )


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
