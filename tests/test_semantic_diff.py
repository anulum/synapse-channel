# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — conservative tree-sitter Git-diff claim regressions
"""Drive semantic diff inference through real temporary Git repositories."""

from __future__ import annotations

import importlib.util
import io
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

import pytest

from synapse_channel.git import semantic_diff
from synapse_channel.git.semantic_tree_sitter import Declaration, default_parser, language_for_path

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL = REPO_ROOT / "tools" / "semantic_diff_claims.py"


def _load_tool() -> Any:
    spec = importlib.util.spec_from_file_location("semantic_diff_claims_tool", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


semantic_diff_tool = _load_tool()


class _FakeGitProcess:
    """Small binary-pipe process double for the bounded Git runner."""

    def __init__(
        self,
        *,
        stdout: bytes = b"",
        stderr: bytes = b"",
        returncode: int | None = 0,
        wait_results: list[int | subprocess.TimeoutExpired] | None = None,
    ) -> None:
        self.stdout = io.BytesIO(stdout)
        self.stderr = io.BytesIO(stderr)
        self.returncode = returncode
        self.pid = 4242
        self.wait_results = list(wait_results or [])
        self.terminate_calls = 0
        self.kill_calls = 0

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        if self.wait_results:
            outcome = self.wait_results.pop(0)
            if isinstance(outcome, subprocess.TimeoutExpired):
                raise outcome
            self.returncode = outcome
        if self.returncode is None:
            raise subprocess.TimeoutExpired("git", timeout or 0.0)
        return self.returncode

    def terminate(self) -> None:
        self.terminate_calls += 1

    def kill(self) -> None:
        self.kill_calls += 1
        self.returncode = -9


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _write(repo: Path, relative: str, text: str) -> None:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _repo(tmp_path: Path, files: dict[str, str]) -> tuple[Path, str]:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    for relative, text in files.items():
        _write(tmp_path, relative, text)
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "base")
    return tmp_path, _git(tmp_path, "rev-parse", "HEAD")


def test_working_tree_function_edit_narrows_to_smallest_declaration(tmp_path: Path) -> None:
    repo, base = _repo(
        tmp_path,
        {
            "src/worker.py": (
                "def outer():\n"
                "    def inner():\n"
                "        return 1\n"
                "    return inner()\n\n"
                "def other():\n"
                "    return 2\n"
            )
        },
    )
    _write(
        repo,
        "src/worker.py",
        "def outer():\n"
        "    def inner():\n"
        "        return 3\n"
        "    return inner()\n\n"
        "def other():\n"
        "    return 2\n",
    )

    records = semantic_diff.resolve_git_diff(repo, base=base)

    assert records == (
        semantic_diff.SemanticDiffRecord(
            status="M",
            source="src/worker.py",
            old_source="src/worker.py",
            language="python",
            symbols=("outer.inner",),
            semantic_scopes=("src/worker.py/.synapse-symbol/outer/inner",),
            claim_paths=("src/worker.py/.synapse-symbol/outer/inner",),
            narrowed=True,
            reason="all changed lines map to named declarations",
        ),
    )


def test_symbol_rename_claims_both_old_and_new_names(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path, {"worker.py": "def old():\n    return 1\n"})
    _write(repo, "worker.py", "def new():\n    return 1\n")

    record = semantic_diff.resolve_git_diff(repo, base=base)[0]

    assert record.symbols == ("old", "new")
    assert record.claim_paths == (
        "worker.py/.synapse-symbol/old",
        "worker.py/.synapse-symbol/new",
    )


@pytest.mark.parametrize(
    ("path", "before", "after", "reason"),
    [
        (
            "worker.py",
            "VALUE = 1\n\ndef run():\n    return VALUE\n",
            "VALUE = 2\n\ndef run():\n    return VALUE\n",
            "outside a named declaration",
        ),
        ("README.md", "old\n", "new\n", "language is not supported"),
        ("broken.py", "def run():\n    return 1\n", "def run(:\n", "outside a named declaration"),
    ],
)
def test_incomplete_semantic_evidence_widens_to_whole_file(
    tmp_path: Path,
    path: str,
    before: str,
    after: str,
    reason: str,
) -> None:
    repo, base = _repo(tmp_path, {path: before})
    _write(repo, path, after)

    record = semantic_diff.resolve_git_diff(repo, base=base)[0]

    assert record.claim_paths == (path,)
    assert record.narrowed is False
    assert reason in record.reason


def test_add_delete_and_rename_are_always_file_wide(tmp_path: Path) -> None:
    repo, base = _repo(
        tmp_path,
        {
            "delete.py": "def removed():\n    return 1\n",
            "rename.py": "def moved():\n    return 2\n",
        },
    )
    (repo / "delete.py").unlink()
    _git(repo, "mv", "rename.py", "renamed.py")
    _write(repo, "added.py", "def added():\n    return 3\n")
    _git(repo, "add", "-A")

    records = semantic_diff.resolve_git_diff(repo, base=base)
    by_status = {record.status: record for record in records}

    assert {record.status for record in records} == {"A", "D", "R"}
    assert by_status["A"].claim_paths == ("added.py",)
    assert by_status["D"].claim_paths == ("delete.py",)
    assert by_status["R"].claim_paths == ("renamed.py",)
    assert all(not record.narrowed for record in records)


def test_committed_head_and_path_filter_ignore_later_worktree_changes(tmp_path: Path) -> None:
    repo, base = _repo(
        tmp_path,
        {
            "a.py": "def a():\n    return 1\n",
            "b.py": "def b():\n    return 1\n",
        },
    )
    _write(repo, "a.py", "def a():\n    return 2\n")
    _write(repo, "b.py", "def b():\n    return 2\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "head")
    head = _git(repo, "rev-parse", "HEAD")
    _write(repo, "a.py", "def a():\n    return 99\n")

    records = semantic_diff.resolve_git_diff(repo, base=base, head=head, paths=("b.py",))

    assert len(records) == 1
    assert records[0].source == "b.py"
    assert records[0].symbols == ("b",)


def test_staged_diff_reads_the_index_and_ignores_later_worktree_changes(
    tmp_path: Path,
) -> None:
    repo, _base = _repo(
        tmp_path,
        {"worker.py": ("def staged():\n    return 1\n\ndef unstaged():\n    return 1\n")},
    )
    _write(
        repo,
        "worker.py",
        "def staged():\n    return 2\n\ndef unstaged():\n    return 1\n",
    )
    _git(repo, "add", "worker.py")
    _write(
        repo,
        "worker.py",
        "def staged():\n    return 2\n\ndef unstaged():\n    return 99\n",
    )

    records = semantic_diff.resolve_staged_diff(repo)

    assert len(records) == 1
    assert records[0].symbols == ("staged",)
    assert records[0].claim_paths == ("worker.py/.synapse-symbol/staged",)


def test_staged_module_level_change_requires_a_whole_file_claim(tmp_path: Path) -> None:
    repo, _base = _repo(
        tmp_path,
        {"worker.py": "VALUE = 1\n\ndef run():\n    return VALUE\n"},
    )
    _write(repo, "worker.py", "VALUE = 2\n\ndef run():\n    return VALUE\n")
    _git(repo, "add", "worker.py")

    record = semantic_diff.resolve_staged_diff(repo)[0]

    assert not record.narrowed
    assert record.claim_paths == ("worker.py",)
    assert "outside a named declaration" in record.reason


def test_staged_diff_uses_the_empty_tree_for_an_initial_commit(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    _write(tmp_path, "worker.py", "def run():\n    return 1\n")
    _git(tmp_path, "add", "worker.py")

    record = semantic_diff.resolve_staged_diff(tmp_path)[0]

    assert record.status == "A"
    assert not record.narrowed
    assert record.claim_paths == ("worker.py",)
    assert record.reason == "git status A is file-wide"


def test_staged_base_does_not_mask_a_non_initial_broken_head(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []

    def fail(args_root: Path, args: tuple[str, ...] | list[str]) -> bytes:
        assert args_root == tmp_path
        calls.append(tuple(args))
        if args[0] == "status":
            return b"# branch.oid deadbeef\n"
        raise ValueError("broken HEAD")

    monkeypatch.setattr(semantic_diff, "_git", fail)

    with pytest.raises(ValueError, match="broken HEAD"):
        semantic_diff._staged_base(tmp_path)
    assert calls == [
        ("rev-parse", "--verify", "HEAD"),
        ("status", "--porcelain=v2", "--branch"),
    ]


def test_mode_only_and_oversized_changes_widen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, base = _repo(tmp_path, {"worker.py": "def run():\n    return 1\n"})
    os.chmod(repo / "worker.py", 0o755)
    mode_record = semantic_diff.resolve_git_diff(repo, base=base)[0]
    assert mode_record.reason == "diff has no textual hunks"

    os.chmod(repo / "worker.py", 0o644)
    _write(repo, "worker.py", "def run():\n    return 200\n")
    monkeypatch.setattr(semantic_diff, "MAX_SEMANTIC_SOURCE_BYTES", 4)
    size_record = semantic_diff.resolve_git_diff(repo, base=base)[0]
    assert "size ceiling" in size_record.reason


def test_parser_failure_and_invalid_revisions_are_fail_visible(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path, {"worker.py": "def run():\n    return 1\n"})
    _write(repo, "worker.py", "def run():\n    return 2\n")

    def refuse(_spec: object) -> Any:
        raise RuntimeError("parser unavailable")

    with pytest.raises(RuntimeError, match="parser unavailable"):
        semantic_diff.resolve_git_diff(repo, base=base, parser_factory=refuse)
    with pytest.raises(ValueError, match="must not be blank"):
        semantic_diff.resolve_git_diff(repo, base=" ")
    with pytest.raises(ValueError, match="cannot also specify"):
        semantic_diff.resolve_git_diff(repo, base=base, head="HEAD", cached=True)
    with pytest.raises(ValueError, match="git semantic diff failed"):
        semantic_diff.resolve_git_diff(repo, base="missing-ref")


def test_missing_git_binary_is_fail_visible(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    with pytest.raises(ValueError, match="git is not installed"):
        semantic_diff._git(tmp_path, ("status",))


def test_bounded_git_runner_uses_isolated_argv_and_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    process = _FakeGitProcess(stdout=b"ok")

    def popen(argv: list[str], **kwargs: object) -> _FakeGitProcess:
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return process

    monkeypatch.setattr(shutil, "which", lambda _name: "/trusted/bin/git")
    monkeypatch.setattr(subprocess, "Popen", popen)

    assert semantic_diff._git(tmp_path, ("status",)) == b"ok"
    assert captured["argv"] == [
        "/trusted/bin/git",
        "-c",
        "core.fsmonitor=false",
        "--no-pager",
        "-C",
        str(tmp_path),
        "status",
    ]
    kwargs = captured["kwargs"]
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert kwargs["stdout"] is subprocess.PIPE
    assert kwargs["stderr"] is subprocess.PIPE
    env = kwargs["env"]
    assert env["PATH"] == "/trusted/bin"
    assert env["GIT_CONFIG_NOSYSTEM"] == "1"
    assert env["GIT_CONFIG_GLOBAL"] == os.devnull
    assert env["GIT_LITERAL_PATHSPECS"] == "1"
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert "HOME" not in env


def test_git_environment_copies_only_required_platform_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TMP", "/bounded/tmp")
    monkeypatch.setenv("UNTRUSTED_GIT_SETTING", "ignored")

    env = semantic_diff._git_environment("/trusted/bin/git")

    assert env["TMP"] == "/bounded/tmp"
    assert "UNTRUSTED_GIT_SETTING" not in env


def test_bounded_git_runner_rejects_output_overflow_and_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    processes = [
        _FakeGitProcess(stdout=b"12345", returncode=None),
        _FakeGitProcess(returncode=None),
    ]
    terminated: list[_FakeGitProcess] = []

    def popen(_argv: list[str], **_kwargs: object) -> _FakeGitProcess:
        return processes.pop(0)

    def terminate(process: _FakeGitProcess) -> None:
        terminated.append(process)
        process.returncode = -9

    monkeypatch.setattr(shutil, "which", lambda _name: "/trusted/bin/git")
    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setattr(semantic_diff, "_terminate_git_process", terminate)

    with pytest.raises(ValueError, match="stdout exceeded its byte limit"):
        semantic_diff._git(tmp_path, ("status",), max_stdout_bytes=4)

    monkeypatch.setattr(semantic_diff, "GIT_READ_TIMEOUT_SECONDS", 0.001)
    with pytest.raises(ValueError, match="git command timed out"):
        semantic_diff._git(tmp_path, ("status",))
    assert len(terminated) == 2


@pytest.mark.parametrize("pipe_name", ["stdout", "stderr"])
def test_bounded_git_runner_rejects_midstream_pipe_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pipe_name: str,
) -> None:
    class FailingPipe(io.BytesIO):
        def __init__(self) -> None:
            super().__init__(b"partial")
            self.exhausted = False

        def read(self, size: int | None = -1) -> bytes:
            chunk = super().read(size)
            if chunk:
                return chunk
            if not self.exhausted:
                self.exhausted = True
                raise OSError("injected pipe failure")
            return b""

    process = _FakeGitProcess(returncode=None)
    setattr(process, pipe_name, FailingPipe())
    terminated: list[_FakeGitProcess] = []

    def terminate(child: _FakeGitProcess) -> None:
        terminated.append(child)
        child.returncode = -9

    monkeypatch.setattr(shutil, "which", lambda _name: "/trusted/bin/git")
    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(semantic_diff, "_terminate_git_process", terminate)

    with pytest.raises(ValueError, match=rf"git {pipe_name} stream read failed"):
        semantic_diff._git(tmp_path, ("status",))
    assert terminated == [process]


def test_git_failure_detail_is_terminal_safe_and_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _FakeGitProcess(
        stderr=b"\x1b[31mfailed\nsecond\x07 " + b"x" * 700,
        returncode=2,
    )
    monkeypatch.setattr(shutil, "which", lambda _name: "/trusted/bin/git")
    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: process)

    with pytest.raises(ValueError) as caught:
        semantic_diff._git(tmp_path, ("status",))

    detail = str(caught.value)
    assert "failed second" in detail
    assert "\x1b" not in detail
    assert "\x07" not in detail
    assert "\n" not in detail
    assert len(detail) <= 560


def test_git_runner_rejects_invalid_limit_launch_and_pipe_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: "/trusted/bin/git")

    with pytest.raises(ValueError, match="limit must not be negative"):
        semantic_diff._git(tmp_path, ("status",), max_stdout_bytes=-1)

    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("refused")),
    )
    with pytest.raises(ValueError, match="could not start git"):
        semantic_diff._git(tmp_path, ("status",))

    process = _FakeGitProcess()
    process.stdout = None  # type: ignore[assignment]
    terminated: list[_FakeGitProcess] = []
    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(
        semantic_diff,
        "_terminate_git_process",
        lambda child: terminated.append(child),
    )
    with pytest.raises(ValueError, match="pipes were unavailable"):
        semantic_diff._git(tmp_path, ("status",))
    assert terminated == [process]


def test_git_runner_denies_unclosed_reader_threads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Reader:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def start(self) -> None:
            pass

        def join(self, timeout: float | None = None) -> None:
            pass

        def is_alive(self) -> bool:
            return True

    process = _FakeGitProcess()
    terminated: list[_FakeGitProcess] = []
    monkeypatch.setattr(shutil, "which", lambda _name: "/trusted/bin/git")
    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(threading, "Thread", Reader)
    monkeypatch.setattr(
        semantic_diff,
        "_terminate_git_process",
        lambda child: terminated.append(child),
    )

    with pytest.raises(ValueError, match="output streams did not close"):
        semantic_diff._git(tmp_path, ("status",))
    assert terminated == [process]


def test_git_process_termination_escalates_to_kill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _FakeGitProcess(
        returncode=None,
        wait_results=[subprocess.TimeoutExpired("git", 0.5), -9],
    )
    signals: list[int] = []
    monkeypatch.setattr(os, "killpg", lambda _pid, sent: signals.append(sent))

    semantic_diff._terminate_git_process(process, posix=True)  # type: ignore[arg-type]

    assert signals == [signal.SIGTERM, signal.SIGKILL]


def test_git_process_exit_race_does_not_trigger_stale_group_kill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _FakeGitProcess(returncode=None)
    signals: list[int] = []

    def exited_during_signal(_pid: int, sent: int) -> None:
        signals.append(sent)
        process.returncode = 0
        raise ProcessLookupError

    monkeypatch.setattr(os, "killpg", exited_during_signal)

    semantic_diff._terminate_git_process(process, posix=True)  # type: ignore[arg-type]

    assert signals == [signal.SIGTERM]


def test_windows_style_git_termination_escalates_and_fails_closed() -> None:
    gentle = _FakeGitProcess(returncode=None, wait_results=[0])
    semantic_diff._terminate_git_process(gentle, posix=False)  # type: ignore[arg-type]
    assert gentle.terminate_calls == 1
    assert gentle.kill_calls == 0

    stubborn = _FakeGitProcess(
        returncode=None,
        wait_results=[subprocess.TimeoutExpired("git", 0.5), -9],
    )
    semantic_diff._terminate_git_process(stubborn, posix=False)  # type: ignore[arg-type]
    assert stubborn.terminate_calls == 1
    assert stubborn.kill_calls == 1

    unterminated = _FakeGitProcess(
        returncode=None,
        wait_results=[
            subprocess.TimeoutExpired("git", 0.5),
            subprocess.TimeoutExpired("git", 0.5),
        ],
    )
    unterminated.kill = lambda: None  # type: ignore[method-assign]
    with pytest.raises(ValueError, match="could not be terminated"):
        semantic_diff._terminate_git_process(unterminated, posix=False)  # type: ignore[arg-type]


def test_pipe_drain_keeps_first_overflow_label_and_exact_byte_ceiling() -> None:
    output = bytearray(b"1234")
    overflow = threading.Event()
    labels = ["stderr"]

    semantic_diff._drain_git_pipe(
        io.BytesIO(b"5678"),
        output,
        limit=4,
        label="stdout",
        overflow=overflow,
        overflow_labels=labels,
        reader_errors=[],
        overflow_lock=threading.Lock(),
    )

    assert output == b"1234"
    assert labels == ["stderr"]
    assert overflow.is_set()


def test_repository_fsmonitor_cannot_execute_during_semantic_diff(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path, {"worker.py": "def run():\n    return 1\n"})
    marker = repo / "fsmonitor-ran"
    if os.name == "nt":
        hook = repo / "fsmonitor.cmd"
        hook.write_text(f'@echo invoked>"{marker}"\r\n', encoding="utf-8")
    else:
        hook = repo / "fsmonitor.sh"
        hook.write_text(
            f"#!/bin/sh\nprintf invoked > {shlex.quote(str(marker))}\n",
            encoding="utf-8",
        )
        hook.chmod(0o755)
    _git(repo, "config", "core.fsmonitor", str(hook))
    _write(repo, "worker.py", "def run():\n    return 2\n")

    _git(repo, "diff", "--name-status", "-z", "--find-renames", base, "--")
    assert marker.read_text(encoding="utf-8").strip() == "invoked"
    marker.unlink()

    record = semantic_diff.resolve_git_diff(repo, base=base)[0]

    assert record.claim_paths == ("worker.py/.synapse-symbol/run",)
    assert not marker.exists()


def test_working_source_read_stops_after_size_detection_byte(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(semantic_diff, "MAX_SEMANTIC_SOURCE_BYTES", 4)
    (tmp_path / "worker.py").write_bytes(b"0123456789")

    assert semantic_diff._working_source(tmp_path, "worker.py") == b"01234"


def test_semantic_git_diff_disables_external_diff_and_textconv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []

    def git(_root: Path, args: tuple[str, ...] | list[str], **_kwargs: object) -> bytes:
        calls.append(tuple(args))
        if "--name-status" in args:
            return b"M\0worker.py\0"
        return b"@@ -1 +1 @@\n-old\n+new\n"

    monkeypatch.setattr(semantic_diff, "_git", git)
    changed = semantic_diff._changed_files(
        tmp_path,
        base="main",
        head=None,
        paths=(),
        cached=False,
    )

    assert changed[0].status == "M"
    assert all("--no-ext-diff" in call for call in calls)
    assert all("--no-textconv" in call for call in calls)


def test_unsafe_per_file_git_evidence_widens_to_whole_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def git(_root: Path, args: tuple[str, ...] | list[str], **_kwargs: object) -> bytes:
        nonlocal calls
        calls += 1
        if calls == 1:
            return b"M\0worker.py\0"
        raise semantic_diff._SemanticGitReadError("bounded evidence unavailable")

    monkeypatch.setattr(semantic_diff, "_git", git)

    record = semantic_diff.resolve_git_diff(tmp_path, base="main")[0]

    assert record.claim_paths == ("worker.py",)
    assert record.narrowed is False
    assert record.reason == "safe Git diff evidence is unavailable"


def test_unsafe_git_source_read_widens_to_whole_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    changed = semantic_diff.ChangedFile(
        "M",
        "worker.py",
        "worker.py",
        (semantic_diff.LineRange(1, 1),),
        (semantic_diff.LineRange(1, 1),),
    )
    monkeypatch.setattr(semantic_diff, "_changed_files", lambda *_args, **_kwargs: (changed,))
    monkeypatch.setattr(
        semantic_diff,
        "_revision_source",
        lambda *_args: (_ for _ in ()).throw(
            semantic_diff._SemanticGitReadError("bounded evidence unavailable")
        ),
    )

    record = semantic_diff.resolve_git_diff(tmp_path, base="main")[0]

    assert record.claim_paths == ("worker.py",)
    assert record.narrowed is False
    assert record.reason == "safe Git source evidence is unavailable"


def test_non_utf8_declaration_name_widens_instead_of_crashing(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path, {"worker.py": "def run():\n    return 1\n"})
    (repo / "worker.py").write_bytes(b"def r\xffn():\n    return 2\n")

    record = semantic_diff.resolve_git_diff(repo, base=base)[0]

    assert record.claim_paths == ("worker.py",)
    assert record.narrowed is False


def test_non_regular_working_source_is_refused(tmp_path: Path) -> None:
    with pytest.raises(OSError, match="not a regular file"):
        semantic_diff._working_source(tmp_path, "missing.py")
    (tmp_path / "target.py").write_text("pass\n", encoding="utf-8")
    (tmp_path / "linked.py").symlink_to("target.py")
    with pytest.raises(OSError, match="not a regular file"):
        semantic_diff._working_source(tmp_path, "linked.py")


def test_non_regular_revision_and_index_entries_are_refused(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    (tmp_path / "worker.py").symlink_to("def run():\n    return 1\n")
    _git(tmp_path, "add", "worker.py")
    _git(tmp_path, "commit", "-qm", "symlink")
    revision = _git(tmp_path, "rev-parse", "HEAD")

    with pytest.raises(OSError, match="revision .* not a regular file"):
        semantic_diff._revision_source(tmp_path, revision, "worker.py")
    with pytest.raises(OSError, match="index source is not a regular file"):
        semantic_diff._index_source(tmp_path, "worker.py")


def test_staged_symlink_change_widens_instead_of_parsing_target_text(
    tmp_path: Path,
) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    link = tmp_path / "worker.py"
    link.symlink_to("def run():\n    return 1\n")
    _git(tmp_path, "add", "worker.py")
    _git(tmp_path, "commit", "-qm", "symlink")
    link.unlink()
    link.symlink_to("def run():\n    return 2\n")
    _git(tmp_path, "add", "worker.py")

    record = semantic_diff.resolve_staged_diff(tmp_path)[0]

    assert not record.narrowed
    assert record.claim_paths == ("worker.py",)
    assert record.reason == "source side is not a regular file"


def test_missing_or_ambiguous_git_entry_is_not_parsed() -> None:
    with pytest.raises(OSError, match="not one regular file"):
        semantic_diff._require_regular_git_entry(
            b"",
            path="worker.py",
            location="index",
        )
    with pytest.raises(OSError, match="not one regular file"):
        semantic_diff._require_regular_git_entry(
            b"100644 a 0\tone.py\0" + b"100644 b 0\ttwo.py\0",
            path="worker.py",
            location="index",
        )


def test_parser_boundary_failures_widen_instead_of_escaping(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, base = _repo(tmp_path, {"worker.py": "def run():\n    return 1\n"})
    _write(repo, "worker.py", "def run():\n    return 2\n")

    with monkeypatch.context() as patch:
        patch.setattr(
            semantic_diff,
            "_working_source",
            lambda _root, _path: (_ for _ in ()).throw(OSError("not regular")),
        )
        record = semantic_diff.resolve_git_diff(repo, base=base)[0]
        assert record.reason == "source side is not a regular file"

    with monkeypatch.context() as patch:
        patch.setattr(
            semantic_diff,
            "extract_declarations",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                UnicodeDecodeError("utf-8", b"x", 0, 1, "invalid")
            ),
        )
        record = semantic_diff.resolve_git_diff(repo, base=base)[0]
        assert record.reason == "declaration name is not valid UTF-8"

    with monkeypatch.context() as patch:
        patch.setattr(
            semantic_diff,
            "extract_declarations",
            lambda *_args, **_kwargs: (Declaration("unsafe\nname", 1, 2),),
        )
        record = semantic_diff.resolve_git_diff(repo, base=base)[0]
        assert record.reason == "declaration cannot form a safe semantic path"


def test_empty_internal_ranges_keep_the_defensive_whole_file_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    language = language_for_path("worker.py")
    assert language is not None
    changed = semantic_diff.ChangedFile("M", "worker.py", "worker.py")
    monkeypatch.setattr(semantic_diff, "_revision_source", lambda *_args: b"pass\n")
    monkeypatch.setattr(semantic_diff, "_working_source", lambda *_args: b"pass\n")
    monkeypatch.setattr(semantic_diff, "extract_declarations", lambda *_args, **_kwargs: ())

    record = semantic_diff._narrow_modified(
        tmp_path,
        changed,
        base="main",
        head=None,
        cached=False,
        language=language[0],
        spec=language[1],
        parser_factory=default_parser,
    )

    assert record.reason == "changed line is outside a named declaration"


def test_empty_and_json_evidence_shapes_are_stable(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path, {"worker.py": "def run():\n    return 1\n"})
    assert semantic_diff.resolve_git_diff(repo, base=base) == ()

    _write(repo, "worker.py", "def run():\n    return 2\n")
    payload = semantic_diff.records_to_json(semantic_diff.resolve_git_diff(repo, base=base))

    assert payload[0]["kind"] == "diff"
    assert payload[0]["narrowed"] is True
    assert payload[0]["semantic_scopes"] == ["worker.py/.synapse-symbol/run"]


def test_tool_combines_symbol_scope_with_test_and_generated_companions(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo, base = _repo(
        tmp_path,
        {
            "src/synapse_channel/worker.py": "def run():\n    return 1\n",
            "tests/test_worker.py": (
                "from synapse_channel.worker import run\n\ndef test_run():\n    assert run() == 1\n"
            ),
            "README.md": "# Project\n",
            "docs/_generated/capability_manifest.json": "{}\n",
            "tools/capability_manifest.py": "",
            "tools/capability_manifest.toml": "",
            "pyproject.toml": "",
        },
    )
    _write(repo, "src/synapse_channel/worker.py", "def run():\n    return 2\n")

    assert semantic_diff_tool.main(["--repo-root", str(repo), "--base", base, "--json"]) == 0
    document = json.loads(capsys.readouterr().out)
    assert document["claim_paths"] == [
        "src/synapse_channel/worker.py/.synapse-symbol/run",
        "tests/test_worker.py",
        "README.md",
        "docs/_generated/capability_manifest.json",
    ]
    assert "widens to a whole-file claim" in document["note"]

    assert semantic_diff_tool.main(["--repo-root", str(repo), "--base", base, "--claim-args"]) == 0
    claim_args = capsys.readouterr().out
    assert "--paths src/synapse_channel/worker.py/.synapse-symbol/run" in claim_args
    assert "--paths tests/test_worker.py" in claim_args


def test_tool_human_check_empty_and_error_paths(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo, base = _repo(tmp_path, {"worker.py": "def run():\n    return 1\n"})

    assert semantic_diff_tool.main(["--repo-root", str(repo), "--base", base, "--check"]) == 0
    assert "0 file(s), 0 narrowed, 0 whole-file" in capsys.readouterr().out

    _write(repo, "worker.py", "def run():\n    return 2\n")
    assert semantic_diff_tool.main(["--repo-root", str(repo), "--base", base, "--check"]) == 0
    output = capsys.readouterr().out
    assert "M worker.py: symbols=run" in output
    assert "1 file(s), 1 narrowed" in output

    assert semantic_diff_tool.main(["--repo-root", str(repo), "--base", "missing-ref"]) == 2
    assert "semantic diff claim error" in capsys.readouterr().err
