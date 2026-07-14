# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the git-aware CLI commands (git-claim/hook/release/conflicts)

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path
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


def test_parser_git_claim_accepts_task_id_flag() -> None:
    args = cli.build_parser().parse_args(["git-claim", "--task-id", "T1", "--paths", "src"])
    assert args.task_id is None
    assert args.task_id_flag == "T1"
    assert args.paths == ["src"]


def test_parser_git_claim_accepts_semantic_selector_flags() -> None:
    args = cli.build_parser().parse_args(
        [
            "git-claim",
            "T1",
            "--module",
            "synapse_channel.core.receipts",
            "--symbol",
            "synapse_channel.core.receipts.build_release_receipt",
            "--api",
            "synapse_channel.core.receipts.ReleaseReceipt",
            "--source",
            "src/synapse_channel/core/receipts.py",
            "--test",
            "tests/test_release_receipts.py",
            "--generated",
            "docs/_generated/capability_manifest.json",
            "--migration",
            "migrations/001_initial.sql",
            "--semantic-evidence-json",
            "semantic-evidence.json",
            "--diff-base",
            "main",
            "--diff-head",
            "HEAD",
            "--diff-path",
            "src",
            "--diff-path",
            "tests",
        ]
    )

    assert args.module == ["synapse_channel.core.receipts"]
    assert args.symbol == ["synapse_channel.core.receipts.build_release_receipt"]
    assert args.api == ["synapse_channel.core.receipts.ReleaseReceipt"]
    assert args.source == ["src/synapse_channel/core/receipts.py"]
    assert args.test == ["tests/test_release_receipts.py"]
    assert args.generated == ["docs/_generated/capability_manifest.json"]
    assert args.migration == ["migrations/001_initial.sql"]
    assert args.semantic_evidence_json == "semantic-evidence.json"
    assert args.semantic_diff_base == "main"
    assert args.semantic_diff_head == "HEAD"
    assert args.semantic_diff_path == ["src", "tests"]


def test_cmd_git_claim_dispatches() -> None:
    captured: dict[str, Any] = {}

    async def run_claim(**kwargs: Any) -> int:
        captured.update(kwargs)
        return 0

    ns = argparse.Namespace(
        uri="ws://h",
        name="U",
        task_id="T1",
        task_id_flag=None,
        paths=["src"],
        base="main",
        auto_release_on="merge",
        token=None,
        module=None,
        symbol=None,
        api=None,
        source=None,
        test=None,
        generated=None,
        migration=None,
        semantic_evidence_json=None,
    )
    assert cli_git._cmd_git_claim(ns, claim_runner=run_claim) == 0
    assert captured["task_id"] == "T1"
    assert captured["paths"] == ["src"]
    assert captured["semantic_selectors"] == ()
    assert captured["semantic_diff_base"] is None
    assert captured["semantic_diff_head"] is None
    assert captured["semantic_diff_paths"] == ()
    assert captured["semantic_evidence_json"] is None


def test_cmd_git_claim_dispatches_task_id_flag() -> None:
    captured: dict[str, Any] = {}

    async def run_claim(**kwargs: Any) -> int:
        captured.update(kwargs)
        return 0

    ns = argparse.Namespace(
        uri="ws://h",
        name="U",
        task_id=None,
        task_id_flag="T1",
        paths=["src"],
        base="main",
        auto_release_on="merge",
        token=None,
        module=None,
        symbol=None,
        api=None,
        source=None,
        test=None,
        generated=None,
        migration=None,
        semantic_evidence_json=None,
    )
    assert cli_git._cmd_git_claim(ns, claim_runner=run_claim) == 0
    assert captured["task_id"] == "T1"


def test_cmd_git_claim_dispatches_semantic_selectors() -> None:
    captured: dict[str, Any] = {}

    async def run_claim(**kwargs: Any) -> int:
        captured.update(kwargs)
        return 0

    ns = argparse.Namespace(
        uri="ws://h",
        name="U",
        task_id="T1",
        task_id_flag=None,
        paths=["docs/manual.md"],
        base="main",
        auto_release_on="merge",
        token=None,
        module=["synapse_channel.core.receipts"],
        symbol=["synapse_channel.core.receipts.build_release_receipt"],
        api=None,
        source=None,
        test=None,
        generated=["docs/_generated/capability_manifest.json"],
        migration=None,
        semantic_diff_base="main",
        semantic_diff_head="HEAD",
        semantic_diff_path=["src", "tests"],
        semantic_evidence_json="semantic-evidence.json",
    )

    assert cli_git._cmd_git_claim(ns, claim_runner=run_claim) == 0
    assert captured["semantic_selectors"] == (
        "module:synapse_channel.core.receipts",
        "symbol:synapse_channel.core.receipts.build_release_receipt",
        "generated:docs/_generated/capability_manifest.json",
    )
    assert captured["semantic_evidence_json"] == "semantic-evidence.json"
    assert captured["semantic_diff_base"] == "main"
    assert captured["semantic_diff_head"] == "HEAD"
    assert captured["semantic_diff_paths"] == ("src", "tests")


def test_cmd_git_claim_rejects_positional_and_flag(
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def run_claim(**kwargs: Any) -> int:
        raise AssertionError("claim runner must not be called")

    ns = argparse.Namespace(
        uri="ws://h",
        name="U",
        task_id="T1",
        task_id_flag="T2",
        paths=["src"],
        base="main",
        auto_release_on="merge",
        token=None,
        module=None,
        symbol=None,
        api=None,
        source=None,
        test=None,
        generated=None,
        migration=None,
        semantic_evidence_json=None,
    )
    assert cli_git._cmd_git_claim(ns, claim_runner=run_claim) == 2
    assert "use either TASK_ID or --task-id" in capsys.readouterr().err


def test_cmd_git_claim_requires_a_task_id(capsys: pytest.CaptureFixture[str]) -> None:
    async def run_claim(**kwargs: Any) -> int:
        raise AssertionError("claim runner must not be called")

    ns = argparse.Namespace(
        uri="ws://h",
        name="U",
        task_id=None,
        task_id_flag=None,
        paths=["src"],
        base="main",
        auto_release_on="merge",
        token=None,
        module=None,
        symbol=None,
        api=None,
        source=None,
        test=None,
        generated=None,
        migration=None,
        semantic_evidence_json=None,
    )
    assert cli_git._cmd_git_claim(ns, claim_runner=run_claim) == 2
    assert "git-claim needs TASK_ID or --task-id" in capsys.readouterr().err


def test_git_claim_argument_docs_are_aligned() -> None:
    root = Path(__file__).resolve().parents[1]
    readme = (root / "README.md").read_text(encoding="utf-8")
    cli_doc = (root / "docs/cli.md").read_text(encoding="utf-8")
    git_claims = (root / "docs/git-claims.md").read_text(encoding="utf-8")

    assert "synapse git-claim --task-id AUTH" in readme
    assert "synapse git-claim --task-id TASK-1" in cli_doc
    assert "Use either the positional `TASK-1` form or `--task-id TASK-1`" in git_claims
    assert "--symbol synapse_channel.core.receipts.build_release_receipt" in readme
    assert "`--module`, `--symbol`, `--api`" in cli_doc
    assert "--semantic-evidence-json semantic-evidence.json" in git_claims
    assert "--diff-base main" in git_claims


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


def test_cmd_git_init_installs_services(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    def install_services(**kwargs: Any) -> list[str]:
        captured.update(kwargs)
        return ["wrote synapse-arm@.service"]

    def initialise_repo(**kwargs: Any) -> list[str]:
        captured["repo_synapse_bin"] = kwargs["synapse_bin"]
        return ["wrote .synapse/git-claims.md"]

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
            repo_initializer=initialise_repo,
            service_installer=install_services,
        )
        == 0
    )
    assert "synapse-arm@.service" in capsys.readouterr().out
    assert captured["project"] == "repo"
    assert captured["identity"] == "repo/ux"
    assert captured["synapse_bin"] == "/bin/synapse"
    assert captured["repo_synapse_bin"] == captured["synapse_bin"]

    resolved: dict[str, str] = {}

    def initialise_with_default(**kwargs: Any) -> list[str]:
        resolved["repo"] = kwargs["synapse_bin"]
        return ["wrote .synapse/git-claims.md"]

    def install_with_default(**kwargs: Any) -> list[str]:
        resolved["service"] = kwargs["synapse_bin"]
        return ["wrote synapse-arm@.service"]

    ns.synapse_bin = None
    with monkeypatch.context() as patch:
        patch.setattr(cli_git, "default_synapse_bin", lambda: "/resolved/synapse")
        assert (
            cli_git._cmd_git_init(
                ns,
                repo_initializer=initialise_with_default,
                service_installer=install_with_default,
            )
            == 0
        )
    assert resolved == {"repo": "/resolved/synapse", "service": "/resolved/synapse"}
    capsys.readouterr()

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    home = tmp_path / "home"
    monkeypatch.chdir(repo)
    monkeypatch.setenv("HOME", str(home))

    def assert_no_git_init_mutation() -> None:
        assert not (repo / ".synapse").exists()
        assert not (repo / ".git" / "hooks" / "post-commit").exists()
        assert not (repo / ".git" / "hooks" / "post-merge").exists()
        assert not home.exists()

    code = cli.main(
        [
            "git-init",
            "--install-user-services",
            "--synapse-bin",
            "+/usr/bin/synapse",
        ]
    )
    captured_io = capsys.readouterr()
    assert code == 2
    assert "ExecStart control prefix" in captured_io.err
    assert "Traceback" not in captured_io.out + captured_io.err
    assert_no_git_init_mutation()

    invalid_simple_names = ("./venv/bin/synapse", ".", "..", ";", "x" * 256)
    for invalid_name in invalid_simple_names:
        code = cli.main(
            [
                "git-init",
                "--install-user-services",
                "--synapse-bin",
                invalid_name,
            ]
        )
        captured_io = capsys.readouterr()
        assert code == 2
        assert "valid simple file name" in captured_io.err
        assert "Traceback" not in captured_io.out + captured_io.err
        assert_no_git_init_mutation()

    hostile_bin = tmp_path / "hostile bin"
    hostile_bin.mkdir()
    executable = hostile_bin / "synapse"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", f"{hostile_bin}:/usr/bin:/bin")
    code = cli.main(["git-init", "--install-user-services"])
    captured_io = capsys.readouterr()
    assert code == 2
    assert "without whitespace" in captured_io.err
    assert "Traceback" not in captured_io.out + captured_io.err
    assert_no_git_init_mutation()

    relative_bin = repo / "relative-bin"
    relative_bin.mkdir()
    relative_executable = relative_bin / "synapse"
    relative_executable.write_text("#!/bin/sh\n", encoding="utf-8")
    relative_executable.chmod(0o755)
    monkeypatch.setenv("PATH", "relative-bin:/usr/bin:/bin")
    for invalid_name in ("relative-bin/synapse", ".", "..", ";", "x" * 256):
        with monkeypatch.context() as patch:
            patch.setattr(
                cli_git,
                "default_synapse_bin",
                lambda invalid_name=invalid_name: invalid_name,
            )
            code = cli.main(["git-init", "--install-user-services"])
        captured_io = capsys.readouterr()
        assert code == 2
        assert "valid simple file name" in captured_io.err
        assert "Traceback" not in captured_io.out + captured_io.err
        assert_no_git_init_mutation()

    code = cli.main(["git-init", "--install-user-services"])
    captured_io = capsys.readouterr()
    assert code == 0
    assert "Traceback" not in captured_io.out + captured_io.err
    assert (repo / ".synapse" / "git-claims.md").exists()
    assert (repo / ".git" / "hooks" / "post-commit").exists()
    assert (repo / ".git" / "hooks" / "post-merge").exists()
    hub_unit = home / ".config" / "systemd" / "user" / "synapse-hub.service"
    assert hub_unit.exists()
    unit_text = hub_unit.read_text(encoding="utf-8")
    assert f"ExecStart={relative_executable.resolve()} hub" in unit_text
    assert "ExecStart=relative-bin/synapse" not in unit_text


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
    assert "synapse release --name=ME -- studio-panel" in err  # the verb they actually wanted


def test_cmd_git_release_redirect_shell_quotes_hostile_fields(
    capsys: pytest.CaptureFixture[str],
) -> None:
    hostile = "--help$(touch injected)\x1b]0;fake\x07"
    ns = argparse.Namespace(task_id=hostile, trigger=None, uri="ws://h", name=hostile, token=None)

    assert cli_git._cmd_git_release(ns) == 2

    err = capsys.readouterr().err
    assert "\x1b" not in err
    assert "\x07" not in err
    assert "--name='--help$(touch injected)\\x1b]0;fake\\x07'" in err
    assert "-- '--help$(touch injected)\\x1b]0;fake\\x07'" in err


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
