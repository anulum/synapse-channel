# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for provider-neutral worker-session launcher

from __future__ import annotations

import subprocess
from typing import Any

import pytest

from synapse_channel import cli, cli_services
from synapse_channel.worker_session import run_worker_session


class FakePopen:
    """Minimal Popen stand-in for the wake sidecar."""

    def __init__(self, args: list[str], **kwargs: Any) -> None:
        self.args = args
        self.kwargs = kwargs
        self.terminated = False
        self.killed = False

    def poll(self) -> None:
        """Report that the sidecar is still running."""
        return None

    def terminate(self) -> None:
        """Record graceful termination."""
        self.terminated = True

    def wait(self, timeout: float | None = None) -> int:
        """Return a successful process exit."""
        return 0

    def kill(self) -> None:
        """Record forced termination."""
        self.killed = True


def test_worker_session_sets_identity_and_starts_sidecar(monkeypatch: pytest.MonkeyPatch) -> None:
    popens: list[FakePopen] = []
    runs: list[tuple[list[str], dict[str, str]]] = []

    def fake_popen(args: list[str], **kwargs: Any) -> FakePopen:
        proc = FakePopen(args, **kwargs)
        popens.append(proc)
        return proc

    def fake_run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        runs.append((args, kwargs["env"]))
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr("synapse_channel.worker_session.subprocess.Popen", fake_popen)
    monkeypatch.setattr("synapse_channel.worker_session.subprocess.run", fake_run)

    assert run_worker_session(identity="repo/ux", command=["codex"], environ={}) == 0
    assert popens[0].args[:3] == ["syn", "arm", "--uri"]
    assert popens[0].terminated is True
    assert runs[0][0] == ["codex"]
    assert runs[0][1]["SYN_PROJECT"] == "repo"
    assert runs[0][1]["SYN_IDENTITY"] == "repo/ux"


def test_worker_session_can_skip_sidecar(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "synapse_channel.worker_session.subprocess.run",
        lambda args, **kwargs: subprocess.CompletedProcess(args, 7),
    )
    assert run_worker_session(identity="repo/ux", command=["false"], arm=False, environ={}) == 7


def test_worker_session_rejects_empty_command() -> None:
    with pytest.raises(ValueError, match="provider command"):
        run_worker_session(identity="repo/ux", command=[], environ={})


def test_parser_worker_session() -> None:
    args = cli.build_parser().parse_args(["worker-session", "--identity", "repo/ux", "--", "codex"])
    assert args.func is cli_services._cmd_worker_session
    assert args.identity == "repo/ux"
    assert args.command == ["--", "codex"]


def test_cmd_worker_session_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake(**kwargs: Any) -> int:
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(cli_services, "run_worker_session", fake)
    ns = cli.build_parser().parse_args(
        ["worker-session", "--identity", "repo/ux", "--project", "repo", "--", "codex"]
    )
    assert cli_services._cmd_worker_session(ns) == 0
    assert captured["identity"] == "repo/ux"
    assert captured["command"] == ["codex"]
