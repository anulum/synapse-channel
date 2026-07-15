# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — fail-closed JetBrains X11 window selection
"""Verify bounded, sealed JetBrains evidence and readiness waits."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from e2e.opencode_editors import (
    jetbrains_evidence,
)
from e2e.opencode_editors.jetbrains_client import (
    _ACP_SESSION_COMPLETIONS,
    _ACP_SESSION_PREREQUISITE,
    _CHAT_READY_MARKERS,
)
from e2e.opencode_editors.jetbrains_evidence import (
    capture_screenshot as _screenshot,
)
from e2e.opencode_editors.jetbrains_evidence import (
    wait_for_idea_log as _wait_for_idea_log,
)
from e2e.opencode_editors.jetbrains_evidence import (
    wait_for_trace as _wait_for_trace,
)


def test_idea_log_wait_requires_all_ordered_markers(tmp_path: Path) -> None:
    markers = (_ACP_SESSION_PREREQUISITE, *_ACP_SESSION_COMPLETIONS)
    idea_log = tmp_path / "idea.log"
    idea_log.write_text("\n".join(markers) + "\n", encoding="utf-8")

    _wait_for_idea_log(
        tmp_path,
        markers,
        float("inf"),
        lambda: None,
    )

    idea_log.write_text("\n".join(reversed(markers)) + "\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="IDEA log never contained"):
        _wait_for_idea_log(
            tmp_path,
            markers,
            0.0,
            lambda: None,
        )


def test_idea_log_wait_uses_a_bounded_contents_reader(tmp_path: Path) -> None:
    contents = "plugins ready\ncommands available\nsession started\n"
    reads: list[bool] = []

    def read_contents() -> str:
        reads.append(True)
        return contents

    _wait_for_idea_log(
        tmp_path,
        ("unused ordered marker",),
        float("inf"),
        lambda: None,
        matcher=lambda value: "plugins ready" in value and "session started" in value,
        contents_reader=read_contents,
    )

    assert reads == [True]


def test_idea_log_wait_fails_closed_when_idea_exits(tmp_path: Path) -> None:
    (tmp_path / "idea.log").write_text("Required plugins check passed\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="IntelliJ IDEA exited before log evidence"):
        _wait_for_idea_log(
            tmp_path,
            (
                "Required plugins check passed",
                "Starting ACP client session ",
            ),
            float("inf"),
            lambda: 1,
        )


def test_idea_log_wait_rejects_an_empty_readiness_contract(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="at least one IDEA log marker is required"):
        _wait_for_idea_log(tmp_path, (), float("inf"), lambda: None)


def test_idea_log_wait_rejects_nonpositive_retry_interval(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="retry interval must be positive"):
        _wait_for_idea_log(
            tmp_path,
            "ready",
            float("inf"),
            lambda: None,
            retry=lambda: None,
            retry_interval_seconds=0.0,
        )


def test_idea_log_wait_retries_idempotent_ui_action_until_ready(tmp_path: Path) -> None:
    attempts = 0

    def expose_ready_marker() -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 2:
            (tmp_path / "idea.log").write_text("chat input ready\n", encoding="utf-8")

    _wait_for_idea_log(
        tmp_path,
        "chat input ready",
        time.monotonic() + 1.0,
        lambda: None,
        retry=expose_ready_marker,
        retry_interval_seconds=0.01,
    )

    assert attempts == 2


def test_idea_log_wait_checks_the_lifecycle_guard_before_success(tmp_path: Path) -> None:
    (tmp_path / "idea.log").write_text("ready\n", encoding="utf-8")
    guarded: list[bool] = []

    _wait_for_idea_log(
        tmp_path,
        "ready",
        float("inf"),
        lambda: None,
        guard=lambda: guarded.append(True),
    )

    assert guarded == [True]


def test_trace_wait_checks_the_lifecycle_guard_before_success(tmp_path: Path) -> None:
    trace = tmp_path / "trace.jsonl"
    trace.write_text('{"method":"initialize"}\n', encoding="utf-8")
    process = subprocess.Popen(  # nosec B603
        [sys.executable, "-c", "import time; time.sleep(10)"],
        text=True,
        start_new_session=True,
    )
    guarded: list[bool] = []
    try:
        _wait_for_trace(
            trace,
            '"method":"initialize"',
            float("inf"),
            process,
            guard=lambda: guarded.append(True),
        )
    finally:
        process.terminate()
        process.wait(timeout=5)

    assert guarded == [True]


def test_trace_wait_rejects_duplicate_lifecycle_before_matching_marker(
    tmp_path: Path,
) -> None:
    trace = tmp_path / "trace.jsonl"
    trace.write_text('{"method":"initialize"}\n', encoding="utf-8")
    process = subprocess.Popen(  # nosec B603
        [sys.executable, "-c", "import time; time.sleep(10)"],
        text=True,
        start_new_session=True,
    )

    def reject_duplicate() -> None:
        raise RuntimeError("duplicate lifecycle")

    try:
        with pytest.raises(RuntimeError, match="duplicate lifecycle"):
            _wait_for_trace(
                trace,
                '"method":"initialize"',
                float("inf"),
                process,
                guard=reject_duplicate,
            )
    finally:
        process.terminate()
        process.wait(timeout=5)


def test_chat_readiness_uses_stable_lifecycle_events(tmp_path: Path) -> None:
    idea_log = tmp_path / "idea.log"
    idea_log.write_text(
        "2026-07-15 AcpSessionLifecycleManagerRegistry - "
        "No session managers found for agent 'SYNAPSE OpenCode E2E'\n",
        encoding="utf-8",
    )

    _wait_for_idea_log(
        tmp_path,
        _CHAT_READY_MARKERS,
        float("inf"),
        lambda: None,
    )

    assert "AIAssistantInputSendAction#presentation" not in idea_log.read_text(encoding="utf-8")


def test_selector_screenshot_cannot_cross_its_phase_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timeouts: list[float] = []
    monkeypatch.setattr(time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(jetbrains_evidence, "_required_tool", lambda _name: "/usr/bin/import")

    def capture(
        command: list[str], *, timeout: float, **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        timeouts.append(timeout)
        Path(command[-1]).write_bytes(b"png evidence")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", capture)
    screenshot = tmp_path / "selector.png"

    _screenshot(screenshot, deadline=103.5)

    assert timeouts == [3.5]
    assert screenshot.read_bytes() == b"png evidence"
    with pytest.raises(RuntimeError, match="screenshot phase deadline expired"):
        _screenshot(tmp_path / "expired.png", deadline=100.0)


@pytest.mark.parametrize(
    ("returncode", "payload", "message"),
    [
        (1, b"diagnostic", "could not capture JetBrains evidence"),
        (0, b"", "unsafe or empty JetBrains screenshot"),
    ],
)
def test_screenshot_rejects_failed_or_empty_capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    returncode: int,
    payload: bytes,
    message: str,
) -> None:
    def capture(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        Path(command[-1]).write_bytes(payload)
        return subprocess.CompletedProcess(command, returncode, "", "diagnostic")

    monkeypatch.setattr(jetbrains_evidence, "_required_tool", lambda _name: "/usr/bin/import")
    monkeypatch.setattr(subprocess, "run", capture)

    with pytest.raises(RuntimeError, match=message):
        _screenshot(tmp_path / "selector.png")
    assert not (tmp_path / "selector.png").exists()


def test_screenshot_refuses_existing_destination_and_unsealable_capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    existing = tmp_path / "existing.png"
    existing.write_bytes(b"original")
    with pytest.raises(RuntimeError, match="refusing to replace"):
        _screenshot(existing)
    assert existing.read_bytes() == b"original"

    def capture(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        Path(command[-1]).write_bytes(b"png")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(jetbrains_evidence, "_required_tool", lambda _name: "/usr/bin/import")
    monkeypatch.setattr(subprocess, "run", capture)
    monkeypatch.setattr(
        os,
        "link",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("refused")),
    )
    sealed = tmp_path / "sealed.png"
    with pytest.raises(RuntimeError, match="could not be sealed"):
        _screenshot(sealed)
    assert not sealed.exists()


def test_trace_reader_rejects_symlink_and_wait_reports_exit_or_timeout(
    tmp_path: Path,
) -> None:
    trace = tmp_path / "trace.jsonl"
    trace.write_text('{"method":"initialize"}\n', encoding="utf-8")
    alias = tmp_path / "trace-alias.jsonl"
    alias.symlink_to(trace)
    assert jetbrains_evidence.trace_has(trace, '"method":"initialize"') is True
    assert jetbrains_evidence.trace_has(alias, '"method":"initialize"') is False
    assert jetbrains_evidence.trace_has(tmp_path / "missing", "marker") is False

    process = subprocess.Popen(  # nosec B603
        [sys.executable, "-c", "pass"],
        text=True,
        start_new_session=True,
    )
    process.wait(timeout=5)
    with pytest.raises(RuntimeError, match="exited before ACP evidence"):
        _wait_for_trace(tmp_path / "missing", "marker", float("inf"), process)
    with pytest.raises(RuntimeError, match="trace never contained"):
        _wait_for_trace(tmp_path / "missing", "marker", 0.0, process)


def test_trace_wait_polls_once_before_evidence_arrives(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace = tmp_path / "trace.jsonl"
    process = subprocess.Popen(  # nosec B603
        [sys.executable, "-c", "import time; time.sleep(10)"],
        text=True,
        start_new_session=True,
    )
    sleeps: list[bool] = []

    def expose_trace(_deadline: float) -> None:
        sleeps.append(True)
        trace.write_text("marker\n", encoding="utf-8")

    monkeypatch.setattr(jetbrains_evidence, "_bounded_poll_sleep", expose_trace)
    try:
        _wait_for_trace(trace, "marker", float("inf"), process)
    finally:
        process.terminate()
        process.wait(timeout=5)
    assert sleeps == [True]


def test_log_retry_interval_suppresses_early_second_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = iter([0.0, 1.0, 2.0, 2.5, 3.0])
    retries: list[bool] = []
    monkeypatch.setattr(time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(jetbrains_evidence, "_bounded_poll_sleep", lambda _deadline: None)

    with pytest.raises(RuntimeError, match="IDEA log never contained"):
        _wait_for_idea_log(
            tmp_path,
            "ready",
            3.0,
            lambda: None,
            retry=lambda: retries.append(True),
            retry_interval_seconds=5.0,
        )
    assert retries == [True]
