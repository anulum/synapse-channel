# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the git-aware CLI commands (git-claim/hook/release/conflicts)

from __future__ import annotations

import argparse
from typing import Any

import pytest

from synapse_channel import cli, cli_git
from synapse_channel.git.gitclaim import GitError

# --- git-claim ---------------------------------------------------------------


def test_parser_git_claim() -> None:
    args = cli.build_parser().parse_args(
        ["git-claim", "T1", "--paths", "src", "--base", "develop", "--auto-release-on", "commit"]
    )
    assert args.func is cli_git._cmd_git_claim
    assert args.task_id == "T1"
    assert args.paths == ["src"]
    assert args.base == "develop"
    assert args.auto_release_on == "commit"


def test_cmd_git_claim_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake(**kwargs: Any) -> int:
        return 0

    monkeypatch.setattr(cli_git, "run_git_claim", fake)
    ns = argparse.Namespace(
        uri="ws://h",
        name="U",
        task_id="T1",
        paths=["src"],
        base="main",
        auto_release_on="merge",
        token=None,
    )
    assert cli_git._cmd_git_claim(ns) == 0


# --- git-hook ----------------------------------------------------------------


def test_parser_git_hook() -> None:
    args = cli.build_parser().parse_args(["git-hook", "install", "--name", "ME"])
    assert args.func is cli_git._cmd_git_hook
    assert args.action == "install"
    assert args.name == "ME"


def test_cmd_git_hook_dispatches(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli_git, "install_hooks", lambda **kwargs: ["installed post-commit"])
    ns = argparse.Namespace(
        action="install", uri="ws://h", name="ME", token=None, token_file=None, synapse_bin=None
    )
    assert cli_git._cmd_git_hook(ns) == 0
    assert "installed post-commit" in capsys.readouterr().out


def test_cmd_git_hook_reports_git_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom(**kwargs: Any) -> list[str]:
        raise GitError("not a git repository")

    monkeypatch.setattr(cli_git, "install_hooks", boom)
    ns = argparse.Namespace(
        action="install", uri="ws://h", name="ME", token=None, token_file=None, synapse_bin=None
    )
    assert cli_git._cmd_git_hook(ns) == 1
    assert "not a git repository" in capsys.readouterr().err


# --- git-release -------------------------------------------------------------


def test_parser_git_release() -> None:
    args = cli.build_parser().parse_args(["git-release", "--trigger", "merge"])
    assert args.func is cli_git._cmd_git_release
    assert args.trigger == "merge"


def test_cmd_git_release_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake(**kwargs: Any) -> int:
        return 0

    monkeypatch.setattr(cli_git, "run_git_release", fake)
    ns = argparse.Namespace(task_id=None, uri="ws://h", name="ME", trigger="commit", token=None)
    assert cli_git._cmd_git_release(ns) == 0


def test_parser_git_release_trigger_is_optional() -> None:
    args = cli.build_parser().parse_args(["git-release"])
    assert args.task_id is None
    assert args.trigger is None
    assert args.func is cli_git._cmd_git_release


def test_cmd_git_release_positional_redirects_to_release(
    capsys: pytest.CaptureFixture[str],
) -> None:
    ns = argparse.Namespace(
        task_id="studio-panel", trigger=None, uri="ws://h", name="ME", token=None
    )
    assert cli_git._cmd_git_release(ns) == 2
    err = capsys.readouterr().err
    assert "synapse release studio-panel --name ME" in err  # the verb they actually wanted


def test_cmd_git_release_missing_trigger_explains(capsys: pytest.CaptureFixture[str]) -> None:
    ns = argparse.Namespace(task_id=None, trigger=None, uri="ws://h", name="ME", token=None)
    assert cli_git._cmd_git_release(ns) == 2
    assert "--trigger" in capsys.readouterr().err


# --- conflicts ---------------------------------------------------------------


def test_parser_conflicts() -> None:
    args = cli.build_parser().parse_args(["conflicts", "--check-diff"])
    assert args.func is cli_git._cmd_conflicts
    assert args.check_diff is True


def test_cmd_conflicts_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    async def fake(**kwargs: Any) -> int:
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(cli_git, "run_conflicts", fake)
    ns = argparse.Namespace(uri="ws://h", name="ME", token=None, check_diff=True)
    assert cli_git._cmd_conflicts(ns) == 0
    assert captured["check_diff"] is True
