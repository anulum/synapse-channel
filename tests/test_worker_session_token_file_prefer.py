# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — worker-session token-file preference (SCH-H-NEW-16 / 12c)

from __future__ import annotations

import stat
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from synapse_channel import worker_session


def test_private_sidecar_runtime_re_tightens_preexisting_mode(tmp_path: Path) -> None:
    leaf = "synapse-worker-session"
    runtime = tmp_path / leaf
    runtime.mkdir(mode=0o755)
    env = {"XDG_RUNTIME_DIR": str(tmp_path)}
    out = worker_session._private_sidecar_runtime(env, leaf)
    assert out == runtime
    assert stat.S_IMODE(out.stat().st_mode) == 0o700


def test_private_sidecar_runtime_refuses_symlink_leaf(tmp_path: Path) -> None:
    from synapse_channel.core.private_dir import PrivateDirError

    leaf = "synapse-worker-session"
    real = tmp_path / "real-dir"
    real.mkdir(mode=0o700)
    link = tmp_path / leaf
    link.symlink_to(real)
    env = {"XDG_RUNTIME_DIR": str(tmp_path)}
    with pytest.raises(PrivateDirError):
        worker_session._private_sidecar_runtime(env, leaf)


def test_start_tmux_waiter_prefers_token_file_over_inline_token(
    tmp_path: Path, monkeypatch: Any
) -> None:
    env = {"XDG_RUNTIME_DIR": str(tmp_path)}
    token_file = str(tmp_path / "hub.token")
    Path(token_file).write_text("secret\n", encoding="utf-8")
    Path(token_file).chmod(0o600)

    captured: list[list[str]] = []

    def fake_popen(cmd: list[str], **kwargs: object) -> MagicMock:
        captured.append(list(cmd))
        proc = MagicMock()
        proc.pid = 4242
        return proc

    monkeypatch.setattr("synapse_channel.worker_session.subprocess.Popen", fake_popen)
    worker_session._start_tmux_waiter(
        identity="proj/agent",
        session="synapse-proj_agent",
        cwd=tmp_path,
        command=["codex"],
        synapse_bin="synapse",
        uri="ws://127.0.0.1:8876",
        token="inline-must-not-appear",
        token_file=token_file,
        env=env,
    )
    assert len(captured) == 1
    argv = captured[0]
    assert "--token-file" in argv
    assert token_file in argv
    assert "--token" not in argv
    assert "inline-must-not-appear" not in argv
