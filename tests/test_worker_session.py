# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for provider-neutral worker-session launcher

from __future__ import annotations

import subprocess
from pathlib import Path
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


class StubbornPopen(FakePopen):
    """Sidecar stand-in that ignores graceful termination until killed."""

    def wait(self, timeout: float | None = None) -> int:
        """Raise on timed waits, then report exit after forced termination."""
        if self.killed:
            return 0
        raise subprocess.TimeoutExpired(self.args, 0.0 if timeout is None else timeout)


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


def test_worker_session_passes_sidecar_auth_options(monkeypatch: pytest.MonkeyPatch) -> None:
    popens: list[FakePopen] = []

    def fake_popen(args: list[str], **kwargs: Any) -> FakePopen:
        proc = FakePopen(args, **kwargs)
        popens.append(proc)
        return proc

    monkeypatch.setattr("synapse_channel.worker_session.subprocess.Popen", fake_popen)
    monkeypatch.setattr(
        "synapse_channel.worker_session.subprocess.run",
        lambda args, **kwargs: subprocess.CompletedProcess(args, 0),
    )

    assert (
        run_worker_session(
            identity="repo/ux",
            command=["provider-cmd"],
            uri="ws://localhost:9999",
            syn_bin="/bin/syn",
            token="secret",
            token_file="/tmp/token",
            environ={},
        )
        == 0
    )
    assert popens[0].args == [
        "/bin/syn",
        "arm",
        "--uri",
        "ws://localhost:9999",
        "--token",
        "secret",
        "--token-file",
        "/tmp/token",
    ]


def test_worker_session_kills_sidecar_after_graceful_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    popens: list[StubbornPopen] = []

    def fake_popen(args: list[str], **kwargs: Any) -> StubbornPopen:
        proc = StubbornPopen(args, **kwargs)
        popens.append(proc)
        return proc

    monkeypatch.setattr("synapse_channel.worker_session.subprocess.Popen", fake_popen)
    monkeypatch.setattr(
        "synapse_channel.worker_session.subprocess.run",
        lambda args, **kwargs: subprocess.CompletedProcess(args, 0),
    )

    assert run_worker_session(identity="repo/ux", command=["provider-cmd"], environ={}) == 0
    assert popens[0].terminated is True
    assert popens[0].killed is True


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


def test_cmd_worker_session_rejects_missing_command(capsys: pytest.CaptureFixture[str]) -> None:
    ns = cli.build_parser().parse_args(["worker-session", "--identity", "repo/ux"])
    assert cli_services._cmd_worker_session(ns) == 2
    assert "requires a provider command" in capsys.readouterr().out


def test_cmd_worker_session_reports_value_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fail(**kwargs: Any) -> int:
        raise ValueError("bad worker command")

    monkeypatch.setattr(cli_services, "run_worker_session", fail)
    ns = cli.build_parser().parse_args(["worker-session", "--identity", "repo/ux", "--", "cmd"])
    assert cli_services._cmd_worker_session(ns) == 2
    assert "bad worker command" in capsys.readouterr().out


def test_cmd_init_prints_service_suggestions(capsys: pytest.CaptureFixture[str]) -> None:
    ns = cli.build_parser().parse_args(["init", "--project", "repo", "--identity", "repo/ux"])
    assert cli_services._cmd_init(ns) == 0
    out = capsys.readouterr().out
    assert "User services are not installed automatically" in out
    assert "synapse-arm@.service" in out


def test_cmd_init_defaults_project_to_current_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    project_dir = tmp_path / "repo-from-cwd"
    project_dir.mkdir()
    monkeypatch.chdir(project_dir)
    ns = cli.build_parser().parse_args(["init"])

    assert cli_services._cmd_init(ns) == 0
    out = capsys.readouterr().out
    assert "repo-from-cwd" in out


def test_cmd_init_installs_user_services(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    captured: dict[str, Any] = {}

    def fake_install(**kwargs: Any) -> list[str]:
        captured.update(kwargs)
        return ["wrote synapse-hub.service", "wrote synapse-arm@.service"]

    monkeypatch.setattr(cli_services, "install_user_services", fake_install)
    ns = cli.build_parser().parse_args(
        [
            "init",
            "--project",
            "repo",
            "--identity",
            "repo/ux",
            "--install-user-services",
            "--synapse-bin",
            "/bin/synapse",
        ]
    )
    assert cli_services._cmd_init(ns) == 0
    assert captured == {
        "project": "repo",
        "identity": "repo/ux",
        "synapse_bin": "/bin/synapse",
        "start": False,
    }
    assert "synapse-arm@.service" in capsys.readouterr().out
