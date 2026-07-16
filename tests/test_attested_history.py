# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — M3 collector (attested main history) regressions

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from synapse_channel.core.governance_metrics import GovernanceMetrics
from synapse_channel.git.attested_history import (
    MainMoveRecord,
    _default_git_runner,
    classify_main_moves,
    collect_attestation_tokens,
    list_main_moves,
    main_history_metrics,
)
from synapse_channel.git.gitclaim import GitError

_SHA_A = "a" * 40
_SHA_B = "b1" + "0" * 38
_SHA_C = "c2" + "f" * 38


def _runner_returning(output: str) -> Any:
    """Build a fake GitRunner that records its argv and returns ``output``."""
    calls: list[list[str]] = []

    def runner(args: list[str]) -> str:
        calls.append(args)
        return output

    runner.calls = calls  # type: ignore[attr-defined]
    return runner


class TestListMainMoves:
    def test_parses_sha_and_subject_oldest_first(self) -> None:
        runner = _runner_returning(f"{_SHA_A}\tfirst move\n{_SHA_B}\tsecond\twith tab")
        moves = list_main_moves(Path("/repo"), runner=runner)
        assert moves == ((_SHA_A, "first move"), (_SHA_B, "second\twith tab"))

    def test_builds_first_parent_reverse_argv_with_ref_and_repo(self) -> None:
        runner = _runner_returning("")
        list_main_moves(Path("/repo"), ref="origin/main", runner=runner)
        argv = runner.calls[0]
        assert argv[:2] == ["-C", "/repo"]
        assert "--first-parent" in argv
        assert "--reverse" in argv
        assert argv[-1] == "origin/main"

    def test_limit_adds_max_count(self) -> None:
        runner = _runner_returning("")
        list_main_moves(Path("/repo"), limit=25, runner=runner)
        assert "--max-count=25" in runner.calls[0]

    def test_no_limit_omits_max_count(self) -> None:
        runner = _runner_returning("")
        list_main_moves(Path("/repo"), runner=runner)
        assert not any(arg.startswith("--max-count") for arg in runner.calls[0])

    def test_malformed_lines_are_skipped(self) -> None:
        runner = _runner_returning(f"not-a-sha\tnoise\n{_SHA_A}\treal")
        assert list_main_moves(Path("/repo"), runner=runner) == ((_SHA_A, "real"),)

    def test_empty_history_is_empty(self) -> None:
        assert list_main_moves(Path("/repo"), runner=_runner_returning("")) == ()


class TestCollectAttestationTokens:
    def test_absent_directory_is_empty(self, tmp_path: Path) -> None:
        assert collect_attestation_tokens(tmp_path / "missing") == frozenset()

    def test_collects_full_and_short_citations_across_nested_files(self, tmp_path: Path) -> None:
        (tmp_path / "sub").mkdir()
        (tmp_path / "log.md").write_text(f"landed {_SHA_A} cleanly", encoding="utf-8")
        (tmp_path / "sub" / "handover.md").write_text("audited b1000000 today", encoding="utf-8")
        tokens = collect_attestation_tokens(tmp_path)
        assert _SHA_A in tokens
        assert "b1000000" in tokens

    def test_uppercase_citations_are_lowered(self, tmp_path: Path) -> None:
        (tmp_path / "note.md").write_text("see ABC1234 for detail", encoding="utf-8")
        assert "abc1234" in collect_attestation_tokens(tmp_path)

    def test_tokens_shorter_than_the_floor_are_ignored(self, tmp_path: Path) -> None:
        (tmp_path / "note.md").write_text("short abc123 citation", encoding="utf-8")
        assert collect_attestation_tokens(tmp_path) == frozenset()

    def test_unreadable_file_is_skipped_not_raised(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "good.md").write_text(f"cites {_SHA_B}", encoding="utf-8")
        (tmp_path / "bad.md").write_text("cites " + _SHA_A, encoding="utf-8")
        real_read_text = Path.read_text

        def failing_read_text(self: Path, **kwargs: Any) -> str:
            if self.name == "bad.md":
                raise OSError("unreadable")
            return real_read_text(self, **kwargs)

        monkeypatch.setattr(Path, "read_text", failing_read_text)
        tokens = collect_attestation_tokens(tmp_path)
        assert _SHA_B in tokens
        assert _SHA_A not in tokens

    def test_undecodable_bytes_are_ignored(self, tmp_path: Path) -> None:
        (tmp_path / "mixed.log").write_bytes(b"\xff\xfe " + _SHA_C.encode() + b" \xff")
        assert _SHA_C in collect_attestation_tokens(tmp_path)

    def test_oversized_dumps_are_skipped_as_evidence(self, tmp_path: Path) -> None:
        # A coordination tree holds tens-of-megabytes run dumps; scanning them
        # turns one measurement into minutes of I/O without adding evidence.
        (tmp_path / "dump.jsonl").write_text(f"cites {_SHA_A} deep inside", encoding="utf-8")
        (tmp_path / "note.md").write_text(f"audited {_SHA_B}", encoding="utf-8")
        cap = (tmp_path / "note.md").stat().st_size
        tokens = collect_attestation_tokens(tmp_path, max_bytes=cap)
        assert _SHA_B in tokens
        assert _SHA_A not in tokens


class TestClassifyMainMoves:
    def test_full_sha_citation_attests(self) -> None:
        records = classify_main_moves(((_SHA_A, "s"),), frozenset({_SHA_A}))
        assert records == (MainMoveRecord(sha=_SHA_A, subject="s", attested=True),)

    def test_seven_char_prefix_attests(self) -> None:
        records = classify_main_moves(((_SHA_B, "s"),), frozenset({_SHA_B[:7]}))
        assert records[0].attested is True

    def test_non_matching_token_leaves_unattested(self) -> None:
        records = classify_main_moves(((_SHA_A, "s"),), frozenset({_SHA_B[:7]}))
        assert records[0].attested is False

    def test_no_evidence_leaves_every_move_unattested(self) -> None:
        records = classify_main_moves(((_SHA_A, "one"), (_SHA_B, "two")), frozenset())
        assert [record.attested for record in records] == [False, False]


class TestMainHistoryMetrics:
    def test_measures_the_m3_rate_on_history_plus_evidence(self, tmp_path: Path) -> None:
        runner = _runner_returning(f"{_SHA_A}\tfirst\n{_SHA_B}\tsecond\n{_SHA_C}\tthird")
        (tmp_path / "audit.md").write_text(f"audited {_SHA_A} and {_SHA_B[:9]}", encoding="utf-8")
        metrics, records = main_history_metrics(Path("/repo"), tmp_path, runner=runner)
        assert isinstance(metrics, GovernanceMetrics)
        assert metrics.total_main_moves == 3
        assert metrics.m3_unattested_main_move_rate == pytest.approx(1 / 3)
        assert [record.attested for record in records] == [True, True, False]

    def test_only_the_m3_family_is_populated(self, tmp_path: Path) -> None:
        runner = _runner_returning(f"{_SHA_A}\tonly")
        metrics, _ = main_history_metrics(Path("/repo"), tmp_path, runner=runner)
        assert metrics.total_edits == 0
        assert metrics.total_forbidding_pushes == 0
        assert metrics.total_claim_violations == 0

    def test_fully_attested_history_is_clean(self, tmp_path: Path) -> None:
        runner = _runner_returning(f"{_SHA_A}\tonly")
        (tmp_path / "audit.md").write_text(f"sealed {_SHA_A}", encoding="utf-8")
        metrics, _ = main_history_metrics(Path("/repo"), tmp_path, runner=runner)
        assert metrics.clean is True


class TestDefaultGitRunner:
    def test_missing_git_raises_git_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("synapse_channel.git.attested_history.shutil.which", lambda _: None)
        with pytest.raises(GitError, match="not installed"):
            _default_git_runner(["version"])

    def test_nonzero_exit_raises_git_error_with_stderr_detail(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "synapse_channel.git.attested_history.shutil.which", lambda _: "/usr/bin/git"
        )

        def failing_run(*args: Any, **kwargs: Any) -> Any:
            raise subprocess.CalledProcessError(128, args[0], stderr="fatal: bad ref")

        monkeypatch.setattr("synapse_channel.git.attested_history.subprocess.run", failing_run)
        with pytest.raises(GitError, match="fatal: bad ref"):
            _default_git_runner(["log"])

    def test_nonzero_exit_without_stderr_reports_the_command(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "synapse_channel.git.attested_history.shutil.which", lambda _: "/usr/bin/git"
        )

        def failing_run(*args: Any, **kwargs: Any) -> Any:
            raise subprocess.CalledProcessError(1, args[0], stderr="")

        monkeypatch.setattr("synapse_channel.git.attested_history.subprocess.run", failing_run)
        with pytest.raises(GitError, match="git log exited non-zero"):
            _default_git_runner(["log"])

    def test_success_returns_stripped_stdout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "synapse_channel.git.attested_history.shutil.which", lambda _: "/usr/bin/git"
        )

        def ok_run(*args: Any, **kwargs: Any) -> Any:
            return subprocess.CompletedProcess(args[0], 0, stdout="  out  \n", stderr="")

        monkeypatch.setattr("synapse_channel.git.attested_history.subprocess.run", ok_run)
        assert _default_git_runner(["version"]) == "out"
