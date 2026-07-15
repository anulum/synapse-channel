# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — fail-closed JetBrains cleanup contract tests
"""Prove screenshot failures cannot bypass editor process termination."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import cast

import pytest

from e2e.opencode_editors import jetbrains_cleanup
from e2e.opencode_editors.jetbrains_cleanup import (
    JetBrainsCleanupError,
    capture_evidence_and_terminate,
)


def _process() -> subprocess.Popen[str]:
    """Return a typed inert process handle for cleanup orchestration tests."""
    return cast(subprocess.Popen[str], object())


def test_screenshot_failure_still_terminates_the_editor_group(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    terminated: list[subprocess.Popen[str]] = []
    process = _process()
    monkeypatch.setattr(
        jetbrains_cleanup,
        "terminate_isolated_process_group",
        terminated.append,
    )

    def fail_capture(_path: Path) -> None:
        raise subprocess.TimeoutExpired("import", 15.0)

    with pytest.raises(subprocess.TimeoutExpired):
        capture_evidence_and_terminate(
            process,
            screenshot=tmp_path / "missing.png",
            capture_screenshot=fail_capture,
            active_error=None,
        )

    assert terminated == [process]


def test_existing_screenshot_skips_capture_but_still_terminates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    screenshot = tmp_path / "intellij.png"
    screenshot.write_bytes(b"evidence")
    captured: list[Path] = []
    terminated: list[subprocess.Popen[str]] = []
    process = _process()
    monkeypatch.setattr(
        jetbrains_cleanup,
        "terminate_isolated_process_group",
        terminated.append,
    )

    capture_evidence_and_terminate(
        process,
        screenshot=screenshot,
        capture_screenshot=captured.append,
        active_error=None,
    )

    assert captured == []
    assert terminated == [process]


def test_cleanup_failures_are_aggregated_with_the_active_editor_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active_error = RuntimeError("editor failed")

    def fail_capture(_path: Path) -> None:
        raise RuntimeError("screenshot failed")

    def fail_termination(_process: subprocess.Popen[str]) -> None:
        raise RuntimeError("termination failed")

    monkeypatch.setattr(
        jetbrains_cleanup,
        "terminate_isolated_process_group",
        fail_termination,
    )

    with pytest.raises(JetBrainsCleanupError) as raised:
        capture_evidence_and_terminate(
            _process(),
            screenshot=tmp_path / "missing.png",
            capture_screenshot=fail_capture,
            active_error=active_error,
        )

    assert raised.value.__cause__ is active_error
    assert [str(error) for error in raised.value.failures] == [
        "editor failed",
        "screenshot failed",
        "termination failed",
    ]


def test_dual_cleanup_failure_is_reported_as_one_aggregate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_capture(_path: Path) -> None:
        raise RuntimeError("screenshot failed")

    def fail_termination(_process: subprocess.Popen[str]) -> None:
        raise RuntimeError("termination failed")

    monkeypatch.setattr(
        jetbrains_cleanup,
        "terminate_isolated_process_group",
        fail_termination,
    )

    with pytest.raises(JetBrainsCleanupError) as raised:
        capture_evidence_and_terminate(
            _process(),
            screenshot=tmp_path / "missing.png",
            capture_screenshot=fail_capture,
            active_error=None,
        )

    assert [str(error) for error in raised.value.failures] == [
        "screenshot failed",
        "termination failed",
    ]
