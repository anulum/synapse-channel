# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — commit-message policy tests
"""Exercise the local hook and real Git history-audit boundary."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "tools" / "check_commit_trailers.py"
AUTHORSHIP = "Authored by Anulum Fortis & Arcane Sapience (protoscience@anulum.li)"


def _load_tool() -> ModuleType:
    """Load the standalone tool without making ``tools`` a package."""
    spec = importlib.util.spec_from_file_location("commit_trailer_gate", TOOL_PATH)
    assert spec is not None and spec.loader is not None, f"cannot load {TOOL_PATH}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


GATE = _load_tool()


def _message(*, subject: str = "ci: enforce commit policy", seat: str = "23696") -> str:
    """Return one valid commit message."""
    return f"{subject}\n\nSeat: {seat}\n{AUTHORSHIP}\n"


def _message_file(tmp_path: Path, message: str) -> Path:
    """Write one pending commit-message fixture."""
    path = tmp_path / "COMMIT_EDITMSG"
    path.write_text(message, encoding="utf-8")
    return path


@pytest.mark.parametrize("seat", ["23696", "a7c2", "rf_01-2"])
def test_hook_accepts_vendor_neutral_seat_suffix(tmp_path: Path, seat: str) -> None:
    assert GATE.main([str(_message_file(tmp_path, _message(seat=seat)))]) == 0


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (f"ci: enforce policy\n\n{AUTHORSHIP}\n", "Seat: <seat-suffix>"),
        (_message() + "Seat: second\n", "Seat: <seat-suffix>"),
        (_message(seat="user/terminal-23696"), "invalid vendor-neutral"),
        (_message(seat="codex-23696"), "vendor-prefixed"),
        (_message(seat="claude-a7c2"), "vendor-prefixed"),
        ("ci: enforce policy\n\nSeat: 23696\n", "exactly one `Authored by"),
        (_message() + f"{AUTHORSHIP}\n", "exactly one `Authored by"),
    ],
)
def test_hook_rejects_invalid_trailer_shapes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    message: str,
    expected: str,
) -> None:
    assert GATE.main([str(_message_file(tmp_path, message))]) == 1
    assert expected in capsys.readouterr().err


def test_hook_rejects_text_between_seat_and_authorship(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    message = f"ci: enforce policy\n\nSeat: 23696\nnot a trailer\n{AUTHORSHIP}\n"

    assert GATE.main([str(_message_file(tmp_path, message))]) == 1
    assert "must immediately precede" in capsys.readouterr().err


@pytest.mark.parametrize(
    "word",
    ["elite", "SUPERIOR", "ETALON", "comprehensive", "robust", "leveraging"],
)
def test_hook_rejects_forbidden_subject_language(tmp_path: Path, word: str) -> None:
    path = _message_file(tmp_path, _message(subject=f"ci: add {word} gate"))

    assert GATE.main([str(path)]) == 1


def test_hook_allows_quoted_subject_language_in_body(tmp_path: Path) -> None:
    message = _message().replace("\n\nSeat:", "\n\nThe old text said robust.\n\nSeat:")

    assert GATE.main([str(_message_file(tmp_path, message))]) == 0


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run Git in a disposable repository."""
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _commit(repo: Path, message: str, marker: str) -> str:
    """Create one deliberately hook-free history fixture commit."""
    (repo / "marker.txt").write_text(marker, encoding="utf-8")
    _git(repo, "add", "marker.txt")
    _git(repo, "commit", "--no-verify", "-m", message)
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def _repo(tmp_path: Path) -> Path:
    """Initialise a disposable repository with local author metadata."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Trailer Gate Test")
    _git(repo, "config", "user.email", "trailer-gate@example.invalid")
    return repo


def test_history_audit_accepts_real_clean_git_range(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    baseline = _commit(repo, "historical baseline", "base")
    _commit(repo, _message(), "clean")

    assert GATE._audit_range(f"{baseline}..HEAD", repo=repo) == 0


def test_history_audit_reports_real_bad_git_commit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path)
    baseline = _commit(repo, "historical baseline", "base")
    bad = _commit(repo, "ci: missing trailers", "bad")

    assert GATE._audit_range(f"{baseline}..HEAD", repo=repo) == 1
    output = capsys.readouterr().out
    assert bad[:12] in output
    assert "Violations: 1" in output


def test_history_audit_accepts_only_an_exact_exempt_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = _repo(tmp_path)
    baseline = _commit(repo, "historical baseline", "base")
    exempt = _commit(repo, "ci: missing trailers", "exempt")
    rejected = _commit(repo, "ci: also missing trailers", "rejected")
    monkeypatch.setattr(GATE, "HISTORY_EXEMPTIONS", {exempt: "test fixture"})

    assert GATE._audit_range(f"{baseline}..{exempt}", repo=repo) == 0
    accepted_output = capsys.readouterr().out
    assert f"{exempt[:12]}: test fixture" in accepted_output
    assert "Explicit history exemptions: 1" in accepted_output

    assert GATE._audit_range(f"{exempt}..{rejected}", repo=repo) == 1
    rejected_output = capsys.readouterr().out
    assert rejected[:12] in rejected_output
    assert "Explicit history exemptions: 0" in rejected_output


def test_history_audit_fails_closed_without_git(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PATH", "")

    assert GATE._audit_range("HEAD") == 2
    assert "git executable unavailable" in capsys.readouterr().err


def test_repository_wires_local_and_remote_trailer_gates() -> None:
    config = (REPO_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    workflow = (REPO_ROOT / ".github/workflows/commit-trailers.yml").read_text(encoding="utf-8")
    preflight = (REPO_ROOT / "tools/preflight.sh").read_text(encoding="utf-8")

    assert "default_install_hook_types: [pre-commit, commit-msg, pre-push]" in config
    assert "stages: [commit-msg]" in config
    assert "python tools/check_commit_trailers.py" in config
    assert "fetch-depth: 0" in workflow
    assert GATE.POLICY_BASELINE in workflow
    assert "tools/check_commit_trailers.py --range" in workflow
    assert "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1" in workflow
    assert "actions/setup-python@6d4fd22b20465ccc0f7354476588e8df7c9ca0e0" in workflow
    assert "check_commit_trailers.py" in preflight


STAMPING_CONFIG = (
    "repos:\n  - repo: local\n    hooks:\n      - id: commit-trailers\n"
    "        entry: python tools/check_commit_trailers.py --stamp\n"
    "        stages: [commit-msg]\n"
)


def _install_commit_msg_hook(repo: Path) -> None:
    """Install a real commit-msg hook that runs the gate with ``--stamp``."""
    hook = repo / ".git" / "hooks" / "commit-msg"
    hook.write_text(
        f'#!/bin/sh\nexec "{sys.executable}" "{TOOL_PATH}" --stamp "$1"\n', encoding="utf-8"
    )
    hook.chmod(0o755)


def _stamping_repo(tmp_path: Path) -> tuple[Path, str]:
    """A repository whose committed hook configuration already requires stamps."""
    repo = _repo(tmp_path)
    baseline = _commit(repo, "historical baseline", "base")
    (repo / ".pre-commit-config.yaml").write_text(STAMPING_CONFIG, encoding="utf-8")
    _git(repo, "add", ".pre-commit-config.yaml")
    _install_commit_msg_hook(repo)
    return repo, baseline


def test_stamp_sits_directly_above_seat_and_replaces_an_earlier_one() -> None:
    """The stamp goes directly above the Seat trailer and replaces an earlier stamp."""
    once = GATE.stamp_message(_message(), "a" * 16)
    twice = GATE.stamp_message(once, "b" * 16)

    assert once.splitlines()[-3:] == [
        "Hooks-Verified: pre-commit sha256:" + "a" * 16,
        "Seat: 23696",
        AUTHORSHIP,
    ]
    assert twice.count("Hooks-Verified:") == 1
    assert "sha256:" + "b" * 16 in twice
    assert GATE._message_violations(twice) == []


@pytest.mark.parametrize(
    ("stamp_lines", "expected"),
    [
        ("Hooks-Verified: pre-commit sha256:XYZ\n", "malformed `Hooks-Verified:` stamp"),
        (
            "Hooks-Verified: pre-commit sha256:" + "a" * 16 + "\n"
            "Hooks-Verified: pre-commit sha256:" + "b" * 16 + "\n",
            "expected at most one `Hooks-Verified:` stamp",
        ),
    ],
)
def test_malformed_or_repeated_stamps_are_rejected(stamp_lines: str, expected: str) -> None:
    """A malformed, misplaced or repeated stamp is a violation."""
    message = _message().replace("Seat:", stamp_lines + "Seat:")

    assert expected in GATE._message_violations(message)


def test_a_real_commit_through_the_hook_is_stamped_and_passes_the_audit(tmp_path: Path) -> None:
    """``git commit`` runs commit-msg after pre-commit; the stamp binds the staged config."""
    repo, baseline = _stamping_repo(tmp_path)
    (repo / "work.txt").write_text("work", encoding="utf-8")
    _git(repo, "add", "work.txt")
    _git(repo, "commit", "-q", "-m", _message())

    message = _git(repo, "show", "-s", "--format=%B", "HEAD").stdout
    digest = GATE.config_digest(STAMPING_CONFIG.encode())
    assert f"Hooks-Verified: pre-commit sha256:{digest}" in message
    assert GATE._audit_range(f"{baseline}..HEAD", repo=repo) == 0


@pytest.mark.parametrize("bypass", ["no-verify", "commit-tree"])
def test_a_commit_that_skipped_the_hooks_is_refused_by_the_audit(
    tmp_path: Path, bypass: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """Both bypasses that produced an unchecked commit here are caught before push."""
    repo, baseline = _stamping_repo(tmp_path)
    (repo / "work.txt").write_text("work", encoding="utf-8")
    _git(repo, "add", "work.txt")
    if bypass == "no-verify":
        _git(repo, "commit", "-q", "--no-verify", "-m", _message())
    else:
        tree = _git(repo, "write-tree").stdout.strip()
        commit = _git(repo, "commit-tree", tree, "-p", "HEAD", "-m", _message()).stdout.strip()
        _git(repo, "update-ref", "HEAD", commit)

    assert GATE._audit_range(f"{baseline}..HEAD", repo=repo) == 1
    assert "made without the pre-commit hooks" in capsys.readouterr().out


def test_a_stamp_for_another_configuration_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A stamp whose digest names another hook configuration is refused."""
    repo, baseline = _stamping_repo(tmp_path)
    stale = GATE.stamp_message(_message(), "0" * 16)
    _git(repo, "commit", "-q", "--no-verify", "-m", stale)

    assert GATE._audit_range(f"{baseline}..HEAD", repo=repo) == 1
    assert "does not match this commit's hook configuration" in capsys.readouterr().out


def test_stamping_needs_a_message_file_and_a_staged_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Stamping without a message file or a staged configuration fails closed."""
    with pytest.raises(SystemExit):
        GATE.main(["--stamp"])
    repo = _repo(tmp_path)
    monkeypatch.chdir(repo)
    path = _message_file(tmp_path, _message())

    assert GATE.main(["--stamp", str(path)]) == 2
    assert "refusing to stamp" in capsys.readouterr().err
    monkeypatch.setenv("PATH", "")
    assert GATE.main(["--stamp", str(path)]) == 2
    assert path.read_text(encoding="utf-8") == _message()


def test_an_invalid_message_is_refused_before_any_stamp(tmp_path: Path) -> None:
    """A message that breaks the trailer policy is refused and never stamped."""
    path = _message_file(tmp_path, "ci: missing trailers\n")

    assert GATE.main(["--stamp", str(path)]) == 1
    assert "Hooks-Verified" not in path.read_text(encoding="utf-8")


def test_the_repository_stamps_through_its_commit_msg_hook() -> None:
    """The repository's commit-msg hook entry runs the gate with --stamp."""
    config = (REPO_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")

    assert GATE.STAMP_MARKER in config


def test_an_unreadable_message_file_fails_closed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A message file that cannot be read fails the hook."""
    assert GATE.main([str(tmp_path / "missing")]) == 2
    assert "cannot read commit message" in capsys.readouterr().err


def test_message_file_and_range_are_mutually_exclusive(tmp_path: Path) -> None:
    """A message file and a history range cannot be given together."""
    with pytest.raises(SystemExit):
        GATE.main([str(_message_file(tmp_path, _message())), "--range", "HEAD"])


def test_unknown_ranges_and_the_default_range_fail_closed_outside_this_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A range git cannot resolve, including the policy baseline in a foreign repo, is 2."""
    repo = _repo(tmp_path)
    _commit(repo, _message(), "only")

    assert GATE._audit_range("no-such-ref..HEAD", repo=repo) == 2
    assert "git rev-list failed" in capsys.readouterr().err
    monkeypatch.chdir(repo)
    assert GATE.main([]) == 2


def test_git_resolution_refuses_a_vanished_or_non_executable_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Environment failures between lookup and use: the gate finds no usable git."""
    plain = tmp_path / "git"
    plain.write_text("not a program", encoding="utf-8")
    plain.chmod(0o644)
    for candidate in (str(tmp_path / "vanished"), str(plain)):
        monkeypatch.setattr(GATE.shutil, "which", lambda _name, found=candidate: found)
        assert GATE._resolve_git() is None


def test_a_commit_git_cannot_show_fails_the_audit_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An object store that lists a commit but cannot show it is an environment failure."""
    repo = _repo(tmp_path)
    baseline = _commit(repo, "historical baseline", "base")
    _commit(repo, _message(), "clean")
    real: Callable[..., subprocess.CompletedProcess[str]] = GATE._run_git

    def failing_show(git: str, root: Path, *args: str) -> subprocess.CompletedProcess[str]:
        """Fail as git would when the staged configuration cannot be shown."""
        if args[0] == "show":
            return subprocess.CompletedProcess(args, 128, "", "object unreadable")
        return real(git, root, *args)

    monkeypatch.setattr(GATE, "_run_git", failing_show)

    assert GATE._audit_range(f"{baseline}..HEAD", repo=repo) == 2
    assert "git show failed" in capsys.readouterr().err


def test_the_hook_stamps_in_process_from_the_staged_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The hook stamps the digest of the staged configuration it reads in process."""
    repo, _baseline = _stamping_repo(tmp_path)
    monkeypatch.chdir(repo)
    path = _message_file(tmp_path, _message())

    assert GATE.main(["--stamp", str(path)]) == 0
    digest = GATE.config_digest(STAMPING_CONFIG.encode())
    assert f"Hooks-Verified: pre-commit sha256:{digest}" in path.read_text(encoding="utf-8")
