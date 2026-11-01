# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — a tmux waiter that cannot be recorded is stopped
"""A started waiter whose pidfile cannot be written must not be left running."""

from __future__ import annotations

import contextlib
from pathlib import Path

import pytest

from synapse_channel.worker_session import run_worker_session
from test_worker_session import _recording_tmux, _write_script

pytestmark = pytest.mark.skipif(
    not Path("/proc/self/cmdline").exists(),
    reason="finding a leftover process by its command line needs Linux /proc",
)


def _command_lines() -> list[bytes]:
    found: list[bytes] = []
    for entry in Path("/proc").iterdir():
        if entry.name.isdigit():
            with contextlib.suppress(OSError):
                found.append((entry / "cmdline").read_bytes())
    return found


def test_tmux_waiter_is_stopped_when_its_pidfile_cannot_be_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The write failure is raised and no waiter process is left behind."""
    runtime = tmp_path / "synapse-provider-tmux"
    runtime.mkdir(mode=0o700)
    locked = tmp_path / "locked"
    locked.mkdir(mode=0o500)
    (runtime / "repo_codex-main.pid").symlink_to(locked / "waiter.pid")
    waiter = _write_script(tmp_path / "synapse", "import time\nwhile True:\n    time.sleep(0.05)\n")
    provider = _write_script(tmp_path / "codex", "raise SystemExit(0)\n")
    monkeypatch.setenv("TMUX_RECORD", str(tmp_path / "tmux.json"))
    try:
        with pytest.raises(PermissionError):
            run_worker_session(
                identity="repo/codex-main",
                command=[str(provider)],
                synapse_bin=str(waiter),
                tmux_bin=str(_recording_tmux(tmp_path / "tmux")),
                terminal_tmux="on",
                environ={
                    "TMUX_RECORD": str(tmp_path / "tmux.json"),
                    "XDG_RUNTIME_DIR": str(tmp_path),
                },
            )
    finally:
        locked.chmod(0o700)
    assert not any(str(waiter).encode() in line for line in _command_lines())
    assert not (locked / "waiter.pid").exists()
