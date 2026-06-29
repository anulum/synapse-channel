# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — `synapse adapters` CLI regressions

from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path

import pytest

from synapse_channel.adapters import MARKER_BEGIN
from synapse_channel.cli_adapters import _cmd_install, _cmd_list, add_parsers


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    add_parsers(parser.add_subparsers())
    return parser


def _args(*argv: str) -> argparse.Namespace:
    return _parser().parse_args(["adapters", *argv])


def _which(*present: str) -> Callable[[str], str | None]:
    found = set(present)
    return lambda binary: f"/usr/bin/{binary}" if binary in found else None


def _claude(home: Path) -> Path:
    return home / ".claude/synapse.md"


def test_list_reports_all_tools_and_detection(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = _args("list", "--home", str(tmp_path), "--project", str(tmp_path))
    assert _cmd_list(args, which=_which("claude")) == 0
    out = capsys.readouterr().out
    assert "claude-code" in out and "cursor" in out
    assert "yes" in out  # claude detected via the binary


def test_list_rejects_an_unknown_tool(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    args = _args("list", "bogus", "--home", str(tmp_path), "--project", str(tmp_path))
    assert _cmd_list(args, which=_which()) == 2
    assert "unknown tool" in capsys.readouterr().err


def test_install_named_writes_file_and_append_adapters(tmp_path: Path) -> None:
    (tmp_path / "CONVENTIONS.md").write_text("My rules.\n", encoding="utf-8")
    args = _args(
        "install",
        "claude-code",
        "aider",
        "--home",
        str(tmp_path),
        "--project",
        str(tmp_path),
        "--identity",
        "proj/agent",
    )
    assert _cmd_install(args, which=_which()) == 0
    claude_text = _claude(tmp_path).read_text(encoding="utf-8")
    assert MARKER_BEGIN in claude_text and "proj/agent" in claude_text
    conventions = (tmp_path / "CONVENTIONS.md").read_text(encoding="utf-8")
    assert conventions.startswith("My rules.")
    assert MARKER_BEGIN in conventions


def test_install_is_idempotent(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    args = _args("install", "claude-code", "--home", str(tmp_path), "--project", str(tmp_path))
    assert _cmd_install(args, which=_which()) == 0
    capsys.readouterr()
    # second install reports "updated" and does not duplicate the block
    assert _cmd_install(args, which=_which()) == 0
    assert "updated" in capsys.readouterr().out
    assert _claude(tmp_path).read_text(encoding="utf-8").count(MARKER_BEGIN) == 1


def test_install_without_names_installs_only_detected(tmp_path: Path) -> None:
    args = _args("install", "--home", str(tmp_path), "--project", str(tmp_path))
    assert _cmd_install(args, which=_which("claude")) == 0
    assert _claude(tmp_path).is_file()  # claude detected
    assert not (tmp_path / ".cursor/rules/synapse.mdc").exists()  # cursor not detected


def test_install_without_detection_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = _args("install", "--home", str(tmp_path), "--project", str(tmp_path))
    assert _cmd_install(args, which=_which()) == 0
    assert "no tools detected" in capsys.readouterr().out
    assert not _claude(tmp_path).exists()


def test_install_dry_run_writes_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    args = _args(
        "install", "claude-code", "--dry-run", "--home", str(tmp_path), "--project", str(tmp_path)
    )
    assert _cmd_install(args, which=_which()) == 0
    out = capsys.readouterr().out
    assert "dry run" in out and "would write" in out
    assert not _claude(tmp_path).exists()


def test_install_rejects_an_unknown_tool(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = _args("install", "bogus", "--home", str(tmp_path), "--project", str(tmp_path))
    assert _cmd_install(args, which=_which()) == 2
    assert "unknown tool" in capsys.readouterr().err


def test_uninstall_removes_file_and_clears_block(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "CONVENTIONS.md").write_text("My rules.\n", encoding="utf-8")
    install = _args(
        "install", "claude-code", "aider", "--home", str(tmp_path), "--project", str(tmp_path)
    )
    _cmd_install(install, which=_which())
    capsys.readouterr()

    args = _args(
        "uninstall", "claude-code", "aider", "--home", str(tmp_path), "--project", str(tmp_path)
    )
    assert args.func(args) == 0
    out = capsys.readouterr().out
    assert "removed" in out and "cleared" in out
    assert not _claude(tmp_path).exists()  # file-mode adapter deleted
    conventions = (tmp_path / "CONVENTIONS.md").read_text(encoding="utf-8")
    assert conventions == "My rules.\n"  # append-mode block stripped, rules kept


def test_uninstall_reports_not_installed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = _args("uninstall", "cursor", "--home", str(tmp_path), "--project", str(tmp_path))
    assert args.func(args) == 0
    assert "not installed" in capsys.readouterr().out


def test_uninstall_deletes_an_append_file_that_becomes_empty(tmp_path: Path) -> None:
    # an append-mode file whose only content is the adapter is removed entirely on uninstall
    install = _args("install", "aider", "--home", str(tmp_path), "--project", str(tmp_path))
    _cmd_install(install, which=_which())
    assert (tmp_path / "CONVENTIONS.md").is_file()
    args = _args("uninstall", "aider", "--home", str(tmp_path), "--project", str(tmp_path))
    assert args.func(args) == 0
    assert not (tmp_path / "CONVENTIONS.md").exists()


def test_uninstall_rejects_an_unknown_tool(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = _args("uninstall", "bogus", "--home", str(tmp_path), "--project", str(tmp_path))
    assert args.func(args) == 2
    assert "unknown tool" in capsys.readouterr().err


def test_default_roots_used_when_not_overridden() -> None:
    # parsing without --home/--project leaves the defaults None; the command fills them
    args = _args("list")
    assert args.home is None and args.project is None
