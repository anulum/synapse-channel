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


def test_cmd_git_claim_dispatches() -> None:
    captured: dict[str, Any] = {}

    async def run_claim(**kwargs: Any) -> int:
        captured.update(kwargs)
        return 0

    ns = argparse.Namespace(
        uri="ws://h",
        name="U",
        task_id="T1",
        paths=["src"],
        base="main",
        auto_release_on="merge",
        token=None,
    )
    assert cli_git._cmd_git_claim(ns, claim_runner=run_claim) == 0
    assert captured["task_id"] == "T1"
    assert captured["paths"] == ["src"]


# --- git-hook ----------------------------------------------------------------


def test_parser_git_hook() -> None:
    args = cli.build_parser().parse_args(["git-hook", "install", "--name", "ME"])
    assert args.func is cli_git._cmd_git_hook
    assert args.action == "install"
    assert args.name == "ME"


def test_cmd_git_hook_dispatches(capsys: pytest.CaptureFixture[str]) -> None:
    ns = argparse.Namespace(
        action="install", uri="ws://h", name="ME", token=None, token_file=None, synapse_bin=None
    )
    assert cli_git._cmd_git_hook(ns, installer=lambda **kwargs: ["installed post-commit"]) == 0
    assert "installed post-commit" in capsys.readouterr().out


def test_cmd_git_hook_reports_git_error(capsys: pytest.CaptureFixture[str]) -> None:
    def boom(**kwargs: Any) -> list[str]:
        raise GitError("not a git repository")

    ns = argparse.Namespace(
        action="install", uri="ws://h", name="ME", token=None, token_file=None, synapse_bin=None
    )
    assert cli_git._cmd_git_hook(ns, installer=boom) == 1
    assert "not a git repository" in capsys.readouterr().err


def test_parser_git_hook_test_action() -> None:
    args = cli.build_parser().parse_args(["git-hook", "test"])
    assert args.func is cli_git._cmd_git_hook
    assert args.action == "test"


# --- git-init ----------------------------------------------------------------


def test_parser_git_init() -> None:
    args = cli.build_parser().parse_args(["git-init", "--name", "ME", "--base", "develop"])
    assert args.func is cli_git._cmd_git_init
    assert args.name == "ME"
    assert args.base == "develop"


def test_parser_git_init_has_token_file_companion() -> None:
    args = cli.build_parser().parse_args(["git-init", "--token-file", "/tmp/tok"])
    assert args.token_file == "/tmp/tok"


def test_parser_git_init_service_flags() -> None:
    args = cli.build_parser().parse_args(
        [
            "git-init",
            "--install-user-services",
            "--service-project",
            "repo",
            "--service-identity",
            "repo/ux",
        ]
    )
    assert args.install_user_services is True
    assert args.service_project == "repo"
    assert args.service_identity == "repo/ux"


def test_cmd_git_init_dispatches(capsys: pytest.CaptureFixture[str]) -> None:
    captured: dict[str, Any] = {}

    def initialise_repo(**kwargs: Any) -> list[str]:
        captured.update(kwargs)
        return ["installed post-commit", "wrote .synapse/git-claims.md"]

    ns = argparse.Namespace(
        uri="ws://h",
        name="ME",
        base="trunk",
        token=None,
        token_file=None,
        synapse_bin=None,
        install_user_services=False,
        start_user_services=False,
        service_project="repo",
        service_identity="repo/ux",
    )
    assert (
        cli_git._cmd_git_init(
            ns,
            repo_initializer=initialise_repo,
            suggestion_builder=lambda **kwargs: ["suggestion"],
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "wrote .synapse/git-claims.md" in out
    assert "service setup available" in out
    assert captured["base_branch"] == "trunk"


def test_cmd_git_init_installs_services(capsys: pytest.CaptureFixture[str]) -> None:
    captured: dict[str, Any] = {}

    def install_services(**kwargs: Any) -> list[str]:
        captured.update(kwargs)
        return ["wrote synapse-arm@.service"]

    ns = argparse.Namespace(
        uri="ws://h",
        name="ME",
        base="main",
        token=None,
        token_file=None,
        synapse_bin="/bin/synapse",
        install_user_services=True,
        start_user_services=False,
        service_project="repo",
        service_identity="repo/ux",
    )
    assert (
        cli_git._cmd_git_init(
            ns,
            repo_initializer=lambda **kwargs: ["wrote .synapse/git-claims.md"],
            service_installer=install_services,
        )
        == 0
    )
    assert "synapse-arm@.service" in capsys.readouterr().out
    assert captured["project"] == "repo"
    assert captured["identity"] == "repo/ux"


def test_cmd_git_init_reports_git_error(capsys: pytest.CaptureFixture[str]) -> None:
    def boom(**kwargs: Any) -> list[str]:
        raise GitError("not a git repository")

    ns = argparse.Namespace(
        uri="ws://h",
        name="ME",
        base="main",
        token=None,
        token_file=None,
        synapse_bin=None,
        install_user_services=False,
        start_user_services=False,
        service_project=None,
        service_identity=None,
    )
    assert cli_git._cmd_git_init(ns, repo_initializer=boom) == 1
    assert "not a git repository" in capsys.readouterr().err


def test_cmd_git_hook_test_reports_healthy(capsys: pytest.CaptureFixture[str]) -> None:
    report = [
        {
            "trigger": t,
            "filename": f"post-{t}",
            "installed": True,
            "synapse_bin": "/usr/bin/synapse",
            "binary_ok": True,
        }
        for t in ("commit", "merge")
    ]
    assert (
        cli_git._cmd_git_hook(
            argparse.Namespace(action="test"), hook_checker=lambda **kwargs: report
        )
        == 0
    )
    assert "ok: post-commit installed -> /usr/bin/synapse" in capsys.readouterr().out


def test_cmd_git_hook_test_flags_missing_and_unresolvable(
    capsys: pytest.CaptureFixture[str],
) -> None:
    report = [
        {
            "trigger": "commit",
            "filename": "post-commit",
            "installed": False,
            "synapse_bin": None,
            "binary_ok": False,
        },
        {
            "trigger": "merge",
            "filename": "post-merge",
            "installed": True,
            "synapse_bin": "/gone/synapse",
            "binary_ok": False,
        },
    ]
    assert (
        cli_git._cmd_git_hook(
            argparse.Namespace(action="test"), hook_checker=lambda **kwargs: report
        )
        == 1
    )
    out = capsys.readouterr().out
    assert "missing: post-commit not installed" in out
    assert "warning: post-merge installed but its synapse binary '/gone/synapse'" in out


def test_cmd_git_hook_test_reports_git_error(capsys: pytest.CaptureFixture[str]) -> None:
    def boom(**kwargs: Any) -> list[dict[str, Any]]:
        raise GitError("not a git repository")

    assert cli_git._cmd_git_hook(argparse.Namespace(action="test"), hook_checker=boom) == 1
    assert "not a git repository" in capsys.readouterr().err


# --- git-release -------------------------------------------------------------


def test_parser_git_release() -> None:
    args = cli.build_parser().parse_args(["git-release", "--trigger", "merge"])
    assert args.func is cli_git._cmd_git_release
    assert args.trigger == "merge"


def test_cmd_git_release_dispatches() -> None:
    captured: dict[str, Any] = {}

    async def release_claims(**kwargs: Any) -> int:
        captured.update(kwargs)
        return 0

    ns = argparse.Namespace(task_id=None, uri="ws://h", name="ME", trigger="commit", token=None)
    assert cli_git._cmd_git_release(ns, release_runner=release_claims) == 0
    assert captured["trigger"] == "commit"


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


def test_cmd_conflicts_dispatches() -> None:
    captured: dict[str, Any] = {}

    async def predict_conflicts(**kwargs: Any) -> int:
        captured.update(kwargs)
        return 0

    ns = argparse.Namespace(uri="ws://h", name="ME", token=None, check_diff=True)
    assert cli_git._cmd_conflicts(ns, conflict_runner=predict_conflicts) == 0
    assert captured["check_diff"] is True
