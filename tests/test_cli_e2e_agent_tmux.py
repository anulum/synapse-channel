# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
"""End-to-end journey: the tmux wake transport drives a real tmux pane.

``synapse agent-tmux`` bridges a Synapse wake to a terminal coding agent by typing
a fixed prompt into the agent's tmux pane. The unit suite exercises the module
with an injected command runner; this journey instead starts a real throwaway tmux
session, runs ``start``/``status``/``wake`` as the packaged CLI against it, and
captures the pane to prove the fixed, payload-free prompt actually lands. ``codex``
is never launched — a harmless ``cat`` pane stands in — so no provider CLI is
needed. The whole file skips when ``tmux`` is not installed.
"""

from __future__ import annotations

import shutil
import subprocess
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
# A shell-echoing pane command that keeps the session alive without launching a
# real coding agent; the injected keystrokes echo so capture-pane can read them.
_HARMLESS_COMMAND = "cat"


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
    with _throwaway_session() as session:
        common = [
            "--identity",
            _IDENTITY,
            "--session",
            session,
            "--cwd",
            str(tmp_path),
            "--agent-command",
            _HARMLESS_COMMAND,
        ]

        started = run_cli("agent-tmux", "start", *common)
        assert started.ok(), started.output
        assert "started" in started.stdout

        health = run_cli("agent-tmux", "status", *common)
        assert health.ok(), health.output
        assert "online" in health.stdout
        assert "active" in health.stdout

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
            _HARMLESS_COMMAND,
        )
        # A missing session is a health failure, so status exits non-zero.
        assert not health.ok(), health.output
        assert "missing" in health.stdout


def test_codex_tmux_alias_injects_the_same_fixed_prompt(tmp_path: Path) -> None:
    """``codex-tmux`` is the compatibility alias and wakes a pane identically.

    The Codex-named surface spells the launch override ``--codex-command`` rather
    than ``--agent-command``, but the fixed wake prompt it injects is identical.
    """
    with _throwaway_session() as session:
        common = [
            "--identity",
            _IDENTITY,
            "--session",
            session,
            "--cwd",
            str(tmp_path),
            "--codex-command",
            _HARMLESS_COMMAND,
        ]

        started = run_cli("codex-tmux", "start", *common)
        assert started.ok(), started.output

        woken = run_cli("codex-tmux", "wake", *common, "--submit-delay", "0.1")
        assert woken.ok(), woken.output
        assert "injected" in woken.stdout

        pane = _normalise(_capture_pane(session))
        assert _normalise(build_wake_prompt(_IDENTITY)) in pane
