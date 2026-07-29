# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
"""End-to-end journey: the tmux wake transport drives a real tmux pane.

``synapse agent-tmux`` bridges a Synapse wake to a terminal coding agent by safely
pasting a fixed prompt into a verified idle composer. The unit suite exercises the module
with an injected command runner; this journey instead starts real throwaway tmux
sessions, runs ``start``/``status``/``wake`` and the supervised ``wait`` boundary
as the packaged CLI, and captures the pane to prove the fixed, payload-free prompt
actually lands. ``codex`` is never launched — a harmless local fixture stands in —
so no provider CLI is needed. The whole file skips when ``tmux`` is not installed.
"""

from __future__ import annotations

import shutil
import subprocess
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from cli_e2e_helpers import run_cli
from synapse_channel.agent_tmux import build_wake_prompt

_TMUX = shutil.which("tmux")
pytestmark = pytest.mark.skipif(_TMUX is None, reason="tmux is not installed")

_IDENTITY = "E2EAGENT"


def _harmless_provider(tmp_path: Path) -> Path:
    """Create a task-owned Codex-shaped idle composer without provider access."""
    provider = tmp_path / "codex-fixture"
    provider.write_text(
        "#!/bin/sh\nprintf '\\n\\n\\n\\n\\n\\n\\n\\n\\n\\n"
        "\\n\\n\\n\\n\\n\\n\\n\\n\\n\\n› \\n'\nwhile IFS= read -r line; do\n"
        "  printf '%s\\n› \\n' \"$line\"\ndone\n",
        encoding="utf-8",
    )
    provider.chmod(0o700)
    return provider


def _modal_provider(tmp_path: Path) -> Path:
    """Create a task-owned approval chooser that records any submitted key."""
    provider = tmp_path / "codex-fixture-modal"
    provider.write_text(
        "#!/bin/sh\nprintf '\\n\\n\\n\\n\\n\\n\\n\\n\\n\\n"
        "\\n\\n\\n\\n\\n\\nAllow Codex to run this command?\\n› 1. Yes\\n  2. No\\n'\n"
        "if IFS= read -r selection; then printf 'SELECTED=%s\\n' \"$selection\"; fi\n",
        encoding="utf-8",
    )
    provider.chmod(0o700)
    return provider


def _capture_pane(session: str) -> str:
    """Return the visible text of ``session``'s active pane.

    ``-J`` rejoins lines tmux hard-wrapped at the pane width, so a long prompt is
    read back as the single logical line it was typed as rather than split
    mid-word at the 80-column boundary.
    """
    assert _TMUX is not None
    proc = subprocess.run(  # noqa: S603 - fixed tmux args, test-only
        [_TMUX, "capture-pane", "-t", session, "-p", "-J"],
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.stdout


def _wait_for_idle_fixture(session: str) -> str:
    """Wait briefly for the task-owned provider to render its idle marker."""
    for _ in range(40):
        pane = _capture_pane(session)
        if "›" in pane:
            return pane
        time.sleep(0.025)
    return _capture_pane(session)


def _kill_session(session: str) -> None:
    """Tear down ``session`` if it still exists, ignoring absence."""
    assert _TMUX is not None
    subprocess.run(  # noqa: S603 - fixed tmux args, test-only
        [_TMUX, "kill-session", "-t", session],
        capture_output=True,
        text=True,
        check=False,
    )


@contextmanager
def _throwaway_session() -> Iterator[str]:
    """Yield a unique tmux session name and guarantee its teardown."""
    session = f"synapse-e2e-{uuid.uuid4().hex[:12]}"
    try:
        yield session
    finally:
        _kill_session(session)


def _normalise(text: str) -> str:
    """Collapse whitespace so tmux's pane line-wrapping does not defeat matching."""
    return " ".join(text.split())


def test_agent_tmux_starts_reports_and_injects_the_fixed_prompt(tmp_path: Path) -> None:
    """``agent-tmux`` stands up a pane, reports it live, and wakes it for real."""
    harmless_command = str(_harmless_provider(tmp_path))
    with _throwaway_session() as session:
        common = [
            "--identity",
            _IDENTITY,
            "--session",
            session,
            "--cwd",
            str(tmp_path),
            "--agent-command",
            harmless_command,
        ]

        started = run_cli("agent-tmux", "start", *common)
        assert started.ok(), started.output
        assert "started" in started.stdout

        health = run_cli("agent-tmux", "status", *common)
        assert health.ok(), health.output
        assert "online" in health.stdout
        assert "active" in health.stdout
        pane_before = _wait_for_idle_fixture(session)
        assert "›" in pane_before, repr(pane_before)

        woken = run_cli("agent-tmux", "wake", *common, "--submit-delay", "0.1")
        assert woken.ok(), woken.output
        assert "injected" in woken.stdout

        # The pane received the fixed routing prompt — payload-free by design, so a
        # remote sender cannot inject terminal text through the wake path.
        pane = _normalise(_capture_pane(session))
        assert _normalise(build_wake_prompt(_IDENTITY)) in pane
        assert _IDENTITY in pane
        assert "continue any active user-directed task" in pane
        assert "wait only when otherwise idle" in pane
        assert "; stop and wait." not in pane


def test_agent_tmux_status_reports_a_missing_session() -> None:
    """``status`` on a session that was never created reports it missing and fails."""
    with _throwaway_session() as session:
        health = run_cli(
            "agent-tmux",
            "status",
            "--identity",
            _IDENTITY,
            "--session",
            session,
            "--cwd",
            ".",
            "--agent-command",
            "codex-fixture",
        )
        # A missing session is a health failure, so status exits non-zero.
        assert not health.ok(), health.output
        assert "missing" in health.stdout


def test_agent_tmux_wake_queues_without_selecting_a_real_tmux_modal(tmp_path: Path) -> None:
    """A deterministic approval chooser receives no paste and no submit key."""
    modal_command = str(_modal_provider(tmp_path))
    with _throwaway_session() as session:
        common = [
            "--identity",
            _IDENTITY,
            "--session",
            session,
            "--cwd",
            str(tmp_path),
            "--agent-command",
            modal_command,
        ]
        started = run_cli("agent-tmux", "start", *common)
        assert started.ok(), started.output
        pane = _wait_for_idle_fixture(session)
        assert "Allow Codex" in pane

        woken = run_cli("agent-tmux", "wake", *common, "--submit-delay", "0")

        assert woken.ok(), woken.output
        assert "wake queued:" in woken.stdout
        pane = _capture_pane(session)
        assert "SELECTED=" not in pane
        assert _normalise(build_wake_prompt(_IDENTITY)) not in _normalise(pane)


def test_agent_tmux_refuses_cross_identity_reuse_of_a_live_session(tmp_path: Path) -> None:
    """Start, status, and wake all fail closed for a foreign live pane."""
    harmless_command = str(_harmless_provider(tmp_path))
    with _throwaway_session() as session:
        owner = [
            "--identity",
            "CORE/agent-a",
            "--session",
            session,
            "--cwd",
            str(tmp_path),
            "--agent-command",
            harmless_command,
        ]
        foreign = [
            "--identity",
            "FLEET/agent-b",
            "--session",
            session,
            "--cwd",
            str(tmp_path),
            "--agent-command",
            harmless_command,
        ]

        started = run_cli("agent-tmux", "start", *owner)
        assert started.ok(), started.output

        second_start = run_cli("agent-tmux", "start", *foreign)
        foreign_status = run_cli("agent-tmux", "status", *foreign)
        foreign_wake = run_cli("agent-tmux", "wake", *foreign, "--submit-delay", "0")

        assert not second_start.ok()
        assert "binding mismatch" in second_start.stdout
        assert not foreign_status.ok()
        assert "session binding: refused" in foreign_status.stdout
        assert not foreign_wake.ok()
        assert "refusing wake injection" in foreign_wake.stdout
        pane = _normalise(_capture_pane(session))
        assert _normalise(build_wake_prompt("FLEET/agent-b")) not in pane


def test_agent_tmux_wait_starts_and_verifies_a_missing_provider(tmp_path: Path) -> None:
    """``wait`` establishes its provider before it can register with the hub."""
    harmless_command = str(_harmless_provider(tmp_path))
    with _throwaway_session() as session:
        waited = run_cli(
            "agent-tmux",
            "wait",
            "--identity",
            _IDENTITY,
            "--session",
            session,
            "--cwd",
            str(tmp_path),
            "--agent-command",
            harmless_command,
            "--max-wakes",
            "0",
        )
        assert waited.ok(), waited.output

        health = run_cli(
            "agent-tmux",
            "status",
            "--identity",
            _IDENTITY,
            "--session",
            session,
            "--cwd",
            str(tmp_path),
            "--agent-command",
            harmless_command,
        )
        assert health.ok(), health.output
        assert "tmux session: online" in health.stdout
        assert "agent pane: active" in health.stdout


def test_codex_tmux_alias_injects_the_same_fixed_prompt(tmp_path: Path) -> None:
    """``codex-tmux`` is the compatibility alias and wakes a pane identically.

    The Codex-named surface spells the launch override ``--codex-command`` rather
    than ``--agent-command``, but the fixed wake prompt it injects is identical.
    """
    harmless_command = str(_harmless_provider(tmp_path))
    with _throwaway_session() as session:
        common = [
            "--identity",
            _IDENTITY,
            "--session",
            session,
            "--cwd",
            str(tmp_path),
            "--codex-command",
            harmless_command,
        ]

        started = run_cli("codex-tmux", "start", *common)
        assert started.ok(), started.output
        pane_before = _wait_for_idle_fixture(session)
        assert "›" in pane_before, repr(pane_before)

        woken = run_cli("codex-tmux", "wake", *common, "--submit-delay", "0.1")
        assert woken.ok(), woken.output
        assert "injected" in woken.stdout

        pane = _normalise(_capture_pane(session))
        assert _normalise(build_wake_prompt(_IDENTITY)) in pane
