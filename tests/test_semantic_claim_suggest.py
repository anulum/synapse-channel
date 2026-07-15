# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — semantic claim suggestion regressions

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from synapse_channel import cli_git

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL = REPO_ROOT / "tools" / "semantic_claim_suggest.py"


def _load_tool(module_name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


semantic_claim_suggest = _load_tool("semantic_claim_suggest", TOOL)
_impl = _load_tool(
    "semantic_claim_suggest_impl",
    REPO_ROOT / "src" / "synapse_channel" / "git" / "semantic_claim_suggest.py",
)


def _run_tool(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(TOOL), *args],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def _write(path: Path, text: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _build_temp_repo(root: Path) -> None:
    _write(
        root / "src" / "synapse_channel" / "auth" / "tokens.py",
        "def verify_token():\n    pass\n",
    )
    _write(
        root / "src" / "synapse_channel" / "auth" / "session.py",
        "class Session:\n    pass\n",
    )
    _write(
        root / "src" / "synapse_channel" / "core" / "receipts.py",
        "def build_release_receipt():\n    return {}\n",
    )
    _write(
        root / "tests" / "test_auth_tokens.py",
        "from synapse_channel.auth.tokens import verify_token\n",
    )
    _write(root / "README.md", "# project\n")
    _write(root / "migrations" / "001_initial.sql", "create table t(id integer);\n")
    _write(root / "pyproject.toml", "[project]\nname = 'demo'\n")


def test_tokenize_splits_and_lowercases() -> None:
    assert _impl._tokenize("Auth token handling") == ("auth", "token", "handling")
    assert _impl._tokenize("auth-token_handling") == ("auth", "token", "handling")
    assert _impl._tokenize("") == ()


def test_suggest_ranks_auth_files_highest(tmp_path: Path) -> None:
    _build_temp_repo(tmp_path)
    suggestions = semantic_claim_suggest.suggest_paths(tmp_path, "auth tokens", limit=10)
    paths = [s.path for s in suggestions]
    assert "src/synapse_channel/auth/tokens.py" in paths
    assert "tests/test_auth_tokens.py" in paths
    # Source files should outrank unrelated paths.
    first = paths[0]
    assert first.startswith("src/synapse_channel/auth/") or first == "tests/test_auth_tokens.py"


def test_suggest_limits_results(tmp_path: Path) -> None:
    _build_temp_repo(tmp_path)
    suggestions = semantic_claim_suggest.suggest_paths(tmp_path, "synapse", limit=2)
    assert len(suggestions) <= 2


def test_suggest_returns_empty_for_non_matching_intent(tmp_path: Path) -> None:
    _build_temp_repo(tmp_path)
    suggestions = semantic_claim_suggest.suggest_paths(tmp_path, "xyznonexistent")
    assert suggestions == ()


def test_suggest_ignores_build_and_cache_dirs(tmp_path: Path) -> None:
    _build_temp_repo(tmp_path)
    _write(tmp_path / "__pycache__" / "cached.cpython-312.pyc", "")
    _write(tmp_path / ".venv" / "lib" / "site.py", "")
    suggestions = semantic_claim_suggest.suggest_paths(tmp_path, "cached", limit=20)
    paths = {s.path for s in suggestions}
    assert not any("__pycache__" in p for p in paths)
    assert not any(".venv" in p for p in paths)


def test_render_human_includes_scores_and_tokens() -> None:
    suggestions = (
        semantic_claim_suggest.SuggestedPath("src/auth.py", 12.5, ("auth",)),
        semantic_claim_suggest.SuggestedPath("tests/test_auth.py", 8.0, ("auth", "test")),
    )
    rendered = semantic_claim_suggest.render_human(suggestions)
    assert "src/auth.py" in rendered
    assert "12.5" in rendered
    assert "tokens: auth" in rendered


def test_render_human_reports_no_matches() -> None:
    assert semantic_claim_suggest.render_human(()) == "no paths matched the intent"


def test_render_json_is_stable() -> None:
    suggestions = (semantic_claim_suggest.SuggestedPath("src/auth.py", 12.5, ("auth",)),)
    payload = json.loads(semantic_claim_suggest.render_json(suggestions))
    assert payload == [{"path": "src/auth.py", "score": 12.5, "matched_tokens": ["auth"]}]


def test_render_draft_claim_includes_paths() -> None:
    suggestions = (
        semantic_claim_suggest.SuggestedPath("src/auth.py", 12.5, ("auth",)),
        semantic_claim_suggest.SuggestedPath("tests/test_auth.py", 8.0, ("auth",)),
    )
    draft = semantic_claim_suggest.render_draft_claim(
        suggestions, "AUTH-42", name="dev", base="dev"
    )
    assert draft.startswith("synapse git-claim")
    assert "AUTH-42" in draft
    assert "--name=dev" in draft
    assert "--base=dev" in draft
    assert "--paths=src/auth.py" in draft
    assert "--paths=tests/test_auth.py" in draft


def test_render_draft_claim_comments_when_empty() -> None:
    assert semantic_claim_suggest.render_draft_claim((), "TASK-001").startswith("# no paths")


def test_main_human_output_runs_against_current_repo(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = semantic_claim_suggest.main(
        ["--repo-root", str(REPO_ROOT), "--intent", "receipts", "--limit", "10"]
    )
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "src/synapse_channel/core/receipts.py" in captured.out


def test_main_json_output(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = semantic_claim_suggest.main(
        ["--repo-root", str(REPO_ROOT), "--intent", "receipts", "--json", "--limit", "3"]
    )
    captured = capsys.readouterr()
    assert exit_code == 0
    payload = json.loads(captured.out)
    assert len(payload) <= 3
    assert payload[0]["path"]
    assert isinstance(payload[0]["score"], float)


def test_main_draft_output(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = semantic_claim_suggest.main(
        [
            "--repo-root",
            str(REPO_ROOT),
            "--intent",
            "receipts",
            "--draft",
            "--draft-task-id",
            "RCPT-1",
            "--limit",
            "2",
        ]
    )
    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.out.strip().startswith("synapse git-claim")
    assert "RCPT-1" in captured.out


def test_main_requires_intent(capsys: pytest.CaptureFixture[str]) -> None:
    assert semantic_claim_suggest.main(["--repo-root", str(REPO_ROOT)]) == 2
    assert "required" in capsys.readouterr().err


def test_main_reports_missing_repo_root(capsys: pytest.CaptureFixture[str]) -> None:
    missing = REPO_ROOT / "does-not-exist-semantic-suggest"
    assert semantic_claim_suggest.main(["--repo-root", str(missing), "--intent", "x"]) == 2
    assert "does not exist" in capsys.readouterr().err


def test_cli_tool_human_output_against_current_repo() -> None:
    result = _run_tool("--intent", "receipts", "--limit", "10")
    assert result.returncode == 0, result.stderr + result.stdout
    assert "src/synapse_channel/core/receipts.py" in result.stdout


def test_cli_tool_json_output_against_current_repo() -> None:
    result = _run_tool("--intent", "receipts", "--json", "--limit", "4")
    assert result.returncode == 0, result.stderr + result.stdout
    payload = json.loads(result.stdout)
    assert len(payload) <= 4


def test_cli_tool_draft_output_against_current_repo() -> None:
    result = _run_tool("--intent", "receipts", "--draft", "--draft-task-id", "RCPT-2")
    assert result.returncode == 0, result.stderr + result.stdout
    assert result.stdout.strip().startswith("synapse git-claim")
    assert "RCPT-2" in result.stdout


# --- coverage: internal error paths and ranking branches (a7c2 integration) ---


def test_suggest_returns_empty_for_punctuation_only_intent(tmp_path: Path) -> None:
    """An intent that tokenises to nothing yields no suggestions."""
    _build_temp_repo(tmp_path)
    assert _impl.suggest_paths(tmp_path, "!!! @@@ ---") == ()


def test_iter_repo_files_empty_for_missing_root(tmp_path: Path) -> None:
    """A non-existent repo root yields no candidate files."""
    assert list(_impl._iter_repo_files(tmp_path / "absent")) == []


def test_iter_repo_files_skips_symlinked_files(tmp_path: Path) -> None:
    """A symlinked file is never a candidate, so it cannot be suggested."""
    _write(tmp_path / "real.py", "x = 1\n")
    (tmp_path / "link.py").symlink_to(tmp_path / "real.py")
    names = [p.name for p in _impl._iter_repo_files(tmp_path)]
    assert "real.py" in names
    assert "link.py" not in names


def test_iter_repo_files_prunes_ignored_directories(tmp_path: Path) -> None:
    """Ignored directories, including *.egg-info, are pruned before descent."""
    _write(tmp_path / "keep.py", "x = 1\n")
    _write(tmp_path / ".git" / "config", "[core]\n")
    _write(tmp_path / "demo.egg-info" / "SOURCES.txt", "keep.py\n")
    names = [p.name for p in _impl._iter_repo_files(tmp_path)]
    assert "keep.py" in names
    assert "config" not in names
    assert "SOURCES.txt" not in names


def test_iter_repo_files_skips_oversize_files(tmp_path: Path) -> None:
    """A file above the size cutoff is skipped to keep the scan fast."""
    _write(tmp_path / "small.py", "x = 1\n")
    (tmp_path / "big.bin").write_bytes(b"0" * (2 * 1024 * 1024 + 1))
    names = [p.name for p in _impl._iter_repo_files(tmp_path)]
    assert "small.py" in names
    assert "big.bin" not in names


def test_iter_repo_files_skips_unstattable_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file whose size stat races away mid-walk is skipped, not fatal."""
    _write(tmp_path / "keep.py", "x = 1\n")
    _write(tmp_path / "gone.py", "y = 2\n")
    real_stat = Path.stat

    def flaky_stat(self: Path, *, follow_symlinks: bool = True) -> os.stat_result:
        # Only the size-check stat (follow_symlinks=True) races away; the
        # is_symlink probe (follow_symlinks=False) must still resolve.
        if self.name == "gone.py" and follow_symlinks:
            raise OSError("vanished")
        return real_stat(self, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(Path, "stat", flaky_stat)
    names = [p.name for p in _impl._iter_repo_files(tmp_path)]
    assert "keep.py" in names
    assert "gone.py" not in names


def test_score_path_matches_a_suffix_only_token() -> None:
    """A token found only as a path substring (the suffix) scores lowest."""
    score, matched = _impl._score_path("src/config.yaml", ("yaml",), {})
    assert matched == ("yaml",)
    assert score == 1.0


def test_score_path_uses_the_module_index_over_a_parent_substring() -> None:
    """A module-index hit (4.0) outranks a mere parent-directory substring (3.0)."""
    module_index = {"pkg.widget.core": "authentication/x.py"}
    score, matched = _impl._score_path("authentication/x.py", ("widget",), module_index)
    assert matched == ("widget",)
    assert score == 4.5  # 4.0 module-index + 0.5 source bonus


def test_build_module_index_is_empty_when_discovery_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A discovery error degrades to an empty module index, not a crash."""

    def boom(_root: Path) -> object:
        raise ValueError("bad tree")

    monkeypatch.setattr(_impl.test_ownership_map, "build_ownership_map", boom)
    assert _impl._build_module_index(tmp_path) == {}


def test_tokenize_deduplicates_repeated_words() -> None:
    """A repeated intent word is collapsed so it cannot inflate the score."""
    assert _impl._tokenize("auth AUTH auth token") == ("auth", "token")


# --- coverage: cli_git `git-claim suggest` integration (a7c2 integration) ---


def _suggest_args(**overrides: object) -> argparse.Namespace:
    base: dict[str, object] = {
        "intent": "receipts",
        "suggest_limit": 10,
        "suggest_draft": False,
        "suggest_json": False,
        "suggest_draft_task_id": "TASK-001",
        "name": "USER",
        "base": "main",
    }
    base.update(overrides)
    return argparse.Namespace(**base)


def test_cli_git_suggest_human_output(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(REPO_ROOT)
    assert cli_git._cmd_git_claim_suggest(_suggest_args()) == 0
    assert "src/synapse_channel/core/receipts.py" in capsys.readouterr().out


def test_cli_git_suggest_json_output(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(REPO_ROOT)
    assert cli_git._cmd_git_claim_suggest(_suggest_args(suggest_json=True, suggest_limit=3)) == 0
    assert len(json.loads(capsys.readouterr().out)) <= 3


def test_cli_git_suggest_draft_output(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(REPO_ROOT)
    args = _suggest_args(suggest_draft=True, suggest_draft_task_id="RCPT-9")
    assert cli_git._cmd_git_claim_suggest(args) == 0
    out = capsys.readouterr().out
    assert out.strip().startswith("synapse git-claim")
    assert "RCPT-9" in out


@pytest.mark.parametrize("intent", ["", "   "])
def test_cli_git_suggest_requires_intent(intent: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli_git._cmd_git_claim_suggest(_suggest_args(intent=intent)) == 2
    assert "needs --intent" in capsys.readouterr().err


def test_cli_git_suggest_reports_scan_errors(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom(*_args: object, **_kwargs: object) -> object:
        raise OSError("scan failed")

    monkeypatch.setattr(cli_git, "suggest_paths", boom)
    assert cli_git._cmd_git_claim_suggest(_suggest_args()) == 1
    assert "semantic suggestion error" in capsys.readouterr().err


def test_cli_git_claim_dispatches_suggest_only_with_intent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`git-claim suggest --intent` routes to suggestion; without it, it claims."""
    monkeypatch.setattr(cli_git, "_cmd_git_claim_suggest", lambda _args: 7)
    monkeypatch.setattr(cli_git, "_resolve_git_claim_task_id", lambda _args: "suggest")
    assert cli_git._cmd_git_claim(_suggest_args(intent="find auth")) == 7
