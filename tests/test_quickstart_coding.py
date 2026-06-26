# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the `synapse quickstart-coding` command

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from synapse_channel import cli, cli_quickstart_coding

REPO_ROOT = Path(__file__).resolve().parents[1]


def _read_repo_text(relative_path: str) -> str:
    """Read a repository text file for quickstart documentation checks."""
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def _single_spaced(text: str) -> str:
    """Normalize documentation whitespace for phrase checks."""
    return " ".join(text.split())


def test_parser_routes_quickstart_coding_to_command_handler(tmp_path: Path) -> None:
    args = cli.build_parser().parse_args(["quickstart-coding", str(tmp_path / "fleet")])

    assert args.func is cli_quickstart_coding._cmd_quickstart_coding
    assert args.path == str(tmp_path / "fleet")
    assert args.force is False
    assert args.keep is False


def test_quickstart_coding_cli_creates_runnable_workspace(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "fleet"

    assert cli.main(["quickstart-coding", str(target)]) == 0

    out = capsys.readouterr().out
    assert "created coding fleet workspace:" in out
    assert str(target) in out
    assert "success: coding fleet demo completed" in out
    assert "next: cd" in out
    assert (target / "run_demo.py").exists()
    assert (
        (target / "README.md").read_text(encoding="utf-8").startswith("# Synapse coding fleet demo")
    )


def test_quickstart_coding_cli_refuses_non_empty_workspace(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "fleet"
    target.mkdir()
    (target / "notes.txt").write_text("keep me", encoding="utf-8")

    assert cli.main(["quickstart-coding", str(target)]) == 2

    captured = capsys.readouterr()
    assert "synapse quickstart-coding:" in captured.err
    assert "not empty" in captured.err
    assert (target / "notes.txt").read_text(encoding="utf-8") == "keep me"


def test_quickstart_coding_force_refreshes_workspace_without_deleting_unrelated_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "fleet"
    target.mkdir()
    (target / "notes.txt").write_text("keep me", encoding="utf-8")

    assert cli.main(["quickstart-coding", str(target), "--force"]) == 0

    out = capsys.readouterr().out
    assert "success: coding fleet demo completed" in out
    assert (target / "notes.txt").read_text(encoding="utf-8") == "keep me"
    assert (target / "run_demo.py").exists()


def test_quickstart_coding_without_path_removes_disposable_workspace(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    disposable_parent = tmp_path / "scratch"
    disposable_parent.mkdir()
    monkeypatch.setenv("TMPDIR", str(disposable_parent))
    monkeypatch.setattr(tempfile, "tempdir", str(disposable_parent))

    assert cli.main(["quickstart-coding"]) == 0

    out = capsys.readouterr().out
    assert "temporary workspace:" in out
    assert "success: coding fleet demo completed" in out
    assert not any(disposable_parent.iterdir())


def test_quickstart_coding_keep_preserves_temporary_workspace(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    disposable_parent = tmp_path / "scratch"
    disposable_parent.mkdir()
    monkeypatch.setenv("TMPDIR", str(disposable_parent))
    monkeypatch.setattr(tempfile, "tempdir", str(disposable_parent))

    assert cli.main(["quickstart-coding", "--keep"]) == 0

    out = capsys.readouterr().out
    kept = list(disposable_parent.glob("synapse-coding-*/fleet/run_demo.py"))
    assert "kept temporary workspace:" in out
    assert kept


def test_public_docs_explain_quickstart_coding_command() -> None:
    combined = _single_spaced(
        "\n".join(
            [
                _read_repo_text("README.md"),
                _read_repo_text("docs/quickstart.md"),
                _read_repo_text("docs/cli.md"),
                _read_repo_text("docs/examples.md"),
                _read_repo_text("docs/recipes.md"),
                _read_repo_text("examples/README.md"),
            ]
        )
    )

    assert "synapse quickstart-coding" in combined
    assert "success: coding fleet demo completed" in combined
    assert "temporary workspace" in combined
