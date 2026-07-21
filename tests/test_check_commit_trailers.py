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
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "tools" / "check_commit_trailers.py"
AUTHORSHIP = "Authored by Anulum Fortis & Arcane Sapience (protoscience@anulum.li)"


def _load_tool() -> ModuleType:
    """Load the standalone tool without making ``tools`` a package."""
    spec = importlib.util.spec_from_file_location("commit_trailer_gate", TOOL_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {TOOL_PATH}")
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
    assert "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97" in workflow
    assert "check_commit_trailers.py" in preflight
