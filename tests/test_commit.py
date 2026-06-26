# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the `syn commit` safe git-lease workflow

from __future__ import annotations

import asyncio
import importlib
import subprocess
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any

import pytest

from synapse_channel import commit as commit_command
from synapse_channel.ergonomics import Identity


def _identity() -> Identity:
    return Identity(
        project="SYNAPSE-CHANNEL",
        identity="SYNAPSE-CHANNEL/codex-1",
        source="env",
        plausible=True,
    )


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _init_repo(repo: Path) -> None:
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Synapse Test")
    (repo / "a.txt").write_text("a1\n", encoding="utf-8")
    (repo / "b.txt").write_text("b1\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")


def test_build_request_defaults_to_project_git_lock() -> None:
    request = commit_command.build_request(_identity(), ["-m", "update", "src/app.py"])

    assert request is not None
    assert request.name == "SYNAPSE-CHANNEL/codex-1"
    assert request.lock_id == "SYNAPSE-CHANNEL:git"
    assert request.message == "update"
    assert request.paths == ("src/app.py",)
    assert request.stage_command() == ["git", "add", "-A", "--", "src/app.py"]
    assert request.commit_command() == ["git", "commit", "-m", "update", "--", "src/app.py"]


def test_build_request_accepts_task_and_wait_overrides() -> None:
    request = commit_command.build_request(
        _identity(),
        ["--task-id", "repo:commit", "--wait-timeout", "3", "src/app.py", "-m", "update"],
    )

    assert request is not None
    assert request.lock_id == "repo:commit"
    assert request.wait_timeout == 3.0


def test_build_request_rejects_missing_message(capsys: pytest.CaptureFixture[str]) -> None:
    assert commit_command.build_request(_identity(), ["src/app.py"]) is None
    assert "syn commit needs -m/--message" in capsys.readouterr().err


def test_build_request_rejects_missing_paths(capsys: pytest.CaptureFixture[str]) -> None:
    assert commit_command.build_request(_identity(), ["-m", "update"]) is None
    assert "syn commit needs at least one path" in capsys.readouterr().err


def test_build_request_rejects_parser_errors(capsys: pytest.CaptureFixture[str]) -> None:
    assert commit_command.build_request(_identity(), ["--wait-timeout", "soon"]) is None
    assert "invalid float value" in capsys.readouterr().err


@pytest.mark.parametrize("path", ["/tmp/file", "../outside", "src/../outside", ".git/config", ""])
def test_build_request_rejects_unsafe_paths(path: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert commit_command.build_request(_identity(), ["-m", "update", path]) is None
    assert "unsafe path" in capsys.readouterr().err


def test_main_holds_project_git_lock_and_commits_only_requested_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_repo(tmp_path)
    (tmp_path / "a.txt").write_text("a2\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("b2\n", encoding="utf-8")
    _git(tmp_path, "add", "b.txt")
    monkeypatch.chdir(tmp_path)
    captured: dict[str, Any] = {}

    async def fake_lock(**kwargs: Any) -> int:
        captured.update(kwargs)
        runner: Callable[[list[str]], Awaitable[int]] = kwargs["runner"]
        return await runner(kwargs["command"])

    assert commit_command.main(_identity(), ["a.txt", "-m", "update a"], lock_runner=fake_lock) == 0

    assert captured["task_id"] == "SYNAPSE-CHANNEL:git"
    assert captured["name"] == "SYNAPSE-CHANNEL/codex-1"
    assert captured["paths"] == []
    assert _git(tmp_path, "show", "--name-only", "--format=", "HEAD").splitlines() == ["a.txt"]
    assert _git(tmp_path, "status", "--short").splitlines() == ["M  b.txt"]


def test_main_returns_usage_error_before_lock(capsys: pytest.CaptureFixture[str]) -> None:
    async def fake_lock(**kwargs: Any) -> int:
        raise AssertionError("lock runner must not be called")

    assert commit_command.main(_identity(), ["README.md"], lock_runner=fake_lock) == 2
    assert "syn commit needs -m/--message" in capsys.readouterr().err


def test_stage_failure_stops_before_commit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)

    async def failing_runner(command: Sequence[str]) -> int:
        assert list(command[:3]) == ["git", "add", "-A"]
        return 7

    assert (
        asyncio.run(
            commit_command.stage_then_commit(
                paths=("missing.txt",),
                commit_command=["git", "commit", "-m", "never", "--", "missing.txt"],
                command_runner=failing_runner,
            )
        )
        == 7
    )
    assert _git(tmp_path, "log", "--format=%s", "-1") == "initial"


def test_syn_commit_is_packaged_and_documented() -> None:
    try:
        toml_parser = importlib.import_module("tomllib")
    except ModuleNotFoundError:  # pragma: no cover
        toml_parser = importlib.import_module("tomli")

    root = Path(__file__).resolve().parents[1]
    pyproject = toml_parser.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    scripts = pyproject["project"]["scripts"]
    readme = (root / "README.md").read_text(encoding="utf-8")
    cli_docs = (root / "docs" / "cli.md").read_text(encoding="utf-8")
    recipes = (root / "docs" / "recipes.md").read_text(encoding="utf-8")

    assert scripts["syn-commit"] == "synapse_channel.ergonomics:alias_commit"
    assert 'syn commit README.md -m "document the change"' in readme
    assert "syn commit <paths> -m <message>" in cli_docs
    assert 'syn commit src/app/api.py tests/test_api.py -m "ship API change"' in recipes
