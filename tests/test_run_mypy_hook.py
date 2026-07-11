# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — tests for the environment-stable mypy hook
"""Tests for the whole-tree mypy hook interpreter boundary."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

TOOL_PATH = Path(__file__).resolve().parents[1] / "tools" / "run_mypy_hook.py"


def _load_tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location("run_mypy_hook", TOOL_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["run_mypy_hook"] = module
    spec.loader.exec_module(module)
    return module


def _make_executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
    path.chmod(0o700)
    return path


def test_resolve_python_prefers_explicit_override(tmp_path: Path) -> None:
    tool = _load_tool()
    explicit = _make_executable(tmp_path / "custom-python")
    conventional = _make_executable(tmp_path / ".venv" / "bin" / "python")

    resolved = tool.resolve_python(
        tmp_path,
        {tool.PYTHON_OVERRIDE_ENV: str(explicit)},
        fallback=conventional,
    )

    assert resolved == explicit


def test_resolve_python_rejects_invalid_explicit_override(tmp_path: Path) -> None:
    tool = _load_tool()
    fallback = _make_executable(tmp_path / "fallback-python")

    with pytest.raises(tool.PythonResolutionError, match=tool.PYTHON_OVERRIDE_ENV):
        tool.resolve_python(
            tmp_path,
            {tool.PYTHON_OVERRIDE_ENV: "missing-python"},
            fallback=fallback,
        )


def test_resolve_python_prefers_posix_then_windows_venv(tmp_path: Path) -> None:
    tool = _load_tool()
    posix = _make_executable(tmp_path / ".venv" / "bin" / "python")
    windows = _make_executable(tmp_path / ".venv" / "Scripts" / "python.exe")

    assert tool.resolve_python(tmp_path, {}, fallback=windows) == posix
    posix.unlink()
    assert tool.resolve_python(tmp_path, {}, fallback=posix) == windows


def test_resolve_python_uses_executable_fallback(tmp_path: Path) -> None:
    tool = _load_tool()
    fallback = _make_executable(tmp_path / "fallback-python")

    assert tool.resolve_python(tmp_path, {}, fallback=fallback) == fallback


def test_run_mypy_uses_fixed_non_shell_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool = _load_tool()
    python = _make_executable(tmp_path / "python")
    calls: list[tuple[list[str], Path, bool]] = []

    def fake_run(
        argv: list[str],
        *,
        cwd: Path,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        calls.append((argv, cwd, check))
        return subprocess.CompletedProcess(argv, 7)

    monkeypatch.setattr(tool.subprocess, "run", fake_run)

    assert tool.run_mypy(python, root=tmp_path) == 7
    assert calls == [([str(python), "-m", "mypy"], tmp_path, False)]


def test_main_rejects_filename_narrowing(capsys: pytest.CaptureFixture[str]) -> None:
    tool = _load_tool()

    assert tool.main(["src/example.py"]) == 2
    assert "accepts no filenames" in capsys.readouterr().err


def test_main_returns_mypy_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tool = _load_tool()
    python = _make_executable(tmp_path / ".venv" / "bin" / "python")
    calls: list[tuple[Path, Path]] = []

    def fake_run_mypy(selected: Path, *, root: Path) -> int:
        calls.append((selected, root))
        return 1

    monkeypatch.setattr(tool, "run_mypy", fake_run_mypy)

    assert tool.main([], root=tmp_path, environ={}) == 1
    assert calls == [(python, tmp_path)]


def test_main_reports_setup_failure(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    tool = _load_tool()
    missing = tmp_path / "missing-python"

    assert tool.main([], root=tmp_path, environ={}, fallback=missing) == 2
    assert "mypy hook setup failed" in capsys.readouterr().err
