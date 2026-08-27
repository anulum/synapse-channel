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
actually lands. Ordinary tests use harmless local fixtures and need no provider.
One explicit opt-in smoke test launches the real Codex TUI to cover its asynchronous
startup boundary. The whole file skips when ``tmux`` is not installed.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from cli_e2e_helpers import _candidate_environment, _stop, isolated_hub, run_cli
from synapse_channel.agent_tmux import AgentTmuxConfig, build_wake_prompt, registry_path

_TMUX = shutil.which("tmux")
_CODEX = shutil.which("codex")
pytestmark = pytest.mark.skipif(_TMUX is None, reason="tmux is not installed")

_IDENTITY = "E2EAGENT"


def _harmless_provider(tmp_path: Path) -> Path:
    """Create a task-owned Codex-shaped idle composer without provider access."""
    provider = tmp_path / "codex-fixture"
    provider.write_text(
        "#!/bin/sh\nprintf '\\033[999B› \\n'\nwhile IFS= read -r line; do\n"
        "  printf '%s\\n› \\n' \"$line\"\ndone\n",
        encoding="utf-8",
    )
    provider.chmod(0o700)
    return provider


def _recording_provider(tmp_path: Path) -> tuple[Path, Path]:
    """Create an idle composer that records each actually submitted line."""
    provider = tmp_path / "codex-fixture-recording"
    submissions = tmp_path / "codex-fixture-recording.submissions"
    provider.write_text(
        "#!/bin/sh\nprintf '\\033[999B› \\n'\nwhile IFS= read -r line; do\n"
        '  printf \'%s\\n\' "$line" >> "$0.submissions"\n'
        "  printf '%s\\n› \\n' \"$line\"\ndone\n",
        encoding="utf-8",
    )
    provider.chmod(0o700)
    return provider, submissions


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
        assert "consumption observed" in woken.stdout

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


def test_agent_tmux_wait_ignores_priority_broadcast_before_exact_wake(tmp_path: Path) -> None:
    """A real hub and tmux pane prove global priority traffic cannot inject."""
    provider, submissions = _recording_provider(tmp_path)
    harmless_command = str(provider)
    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o700)
    with _throwaway_session() as session, isolated_hub(tmp_path) as hub:
        common = [
            "--identity",
            _IDENTITY,
            "--session",
            session,
            "--cwd",
            str(tmp_path),
            "--agent-command",
            harmless_command,
            "--submit-delay",
            "0",
            "--uri",
            hub.uri,
        ]
        waiter = subprocess.Popen(  # noqa: S603 - candidate CLI, test-only
            [
                sys.executable,
                "-u",
                "-m",
                "synapse_channel.cli",
                "agent-tmux",
                "wait",
                *common,
                "--max-wakes",
                "1",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=_candidate_environment({"XDG_RUNTIME_DIR": str(runtime)}),
        )
        try:
            deadline = time.monotonic() + 8.0
            while time.monotonic() < deadline:
                who = run_cli("who", uri=hub.uri)
                if f"{_IDENTITY}-pane-rx" in who.stdout and "›" in _capture_pane(session):
                    break
                time.sleep(0.05)
            else:
                raise AssertionError("pane bridge did not become ready")

            broadcast = run_cli(
                "send",
                "global priority",
                "--name",
                "A",
                "--target",
                "all",
                "--priority",
                uri=hub.uri,
            )
            assert broadcast.ok(), broadcast.output
            time.sleep(0.25)
            assert waiter.poll() is None
            assert "Synapse wake for" not in _capture_pane(session)
            assert not submissions.exists()

            directed = run_cli(
                "send",
                "exact wake",
                "--name",
                "A",
                "--target",
                _IDENTITY,
                uri=hub.uri,
            )
            assert directed.ok(), directed.output
            output, _ = waiter.communicate(timeout=8.0)
            assert waiter.returncode == 0, output
            pane = _normalise(_capture_pane(session))
            assert _normalise(build_wake_prompt(_IDENTITY)) in pane
            submitted = submissions.read_text(encoding="utf-8").splitlines()
            assert submitted == [build_wake_prompt(_IDENTITY)]
        finally:
            if waiter.poll() is None:
                _stop(waiter)


def test_agent_tmux_wait_submits_a_persisted_staged_prompt_without_repasting(
    tmp_path: Path,
) -> None:
    """A real tmux pane and CLI prove retry is at-most-once after paste."""
    assert _TMUX is not None
    provider, submissions = _recording_provider(tmp_path)
    harmless_command = str(provider)
    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o700)
    cli_env = {"XDG_RUNTIME_DIR": str(runtime)}
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
            "--submit-delay",
            "0",
        ]
        started = run_cli("agent-tmux", "start", *common, env=cli_env)
        assert started.ok(), started.output
        assert "›" in _wait_for_idle_fixture(session)

        prompt = build_wake_prompt(_IDENTITY)
        buffer_name = f"synapse-e2e-staged-{uuid.uuid4().hex[:8]}"
        set_buffer = subprocess.run(  # noqa: S603 - fixed tmux args, test-only
            [_TMUX, "set-buffer", "-b", buffer_name, "--", prompt],
            capture_output=True,
            text=True,
            check=False,
        )
        assert set_buffer.returncode == 0, set_buffer.stderr
        paste_buffer = subprocess.run(  # noqa: S603 - fixed tmux args, test-only
            [_TMUX, "paste-buffer", "-b", buffer_name, "-d", "-p", "-t", session],
            capture_output=True,
            text=True,
            check=False,
        )
        assert paste_buffer.returncode == 0, paste_buffer.stderr
        assert _normalise(prompt) in _normalise(_capture_pane(session))

        config = AgentTmuxConfig(
            identity=_IDENTITY,
            session=session,
            cwd=tmp_path,
            agent_command=(harmless_command,),
            registry_dir=runtime / "synapse-agent-tmux",
            submit_delay=0,
        )
        registry_path(config).write_text(
            json.dumps(
                {
                    "identity": _IDENTITY,
                    "session": session,
                    "cwd": str(tmp_path),
                    "pending_wake": True,
                    "wake_prompt_staged": True,
                }
            ),
            encoding="utf-8",
        )

        waited = run_cli(
            "agent-tmux",
            "wait",
            *common,
            "--max-wakes",
            "1",
            env=cli_env,
        )

        assert waited.ok(), waited.output
        assert submissions.read_text(encoding="utf-8").splitlines() == [prompt]
        payload = json.loads(registry_path(config).read_text(encoding="utf-8"))
        assert payload["pending_wake"] is False
        assert payload["wake_prompt_staged"] is False


@pytest.mark.skipif(
    os.environ.get("SYNAPSE_REAL_CODEX_TMUX_SMOKE") != "1" or _CODEX is None,
    reason="set SYNAPSE_REAL_CODEX_TMUX_SMOKE=1 with Codex installed",
)
def test_agent_tmux_submits_the_wake_in_a_real_codex_composer(tmp_path: Path) -> None:
    """A real Codex TUI consumes the fixed prompt and restores its composer."""
    assert _CODEX is not None
    repository = Path(__file__).resolve().parents[1]
    identity = "SYNAPSE-CHANNEL/codex-real-tmux-smoke"
    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o700)
    cli_env = {"XDG_RUNTIME_DIR": str(runtime)}
    with _throwaway_session() as session:
        common = [
            "--identity",
            identity,
            "--session",
            session,
            "--cwd",
            str(repository),
            "--agent-command",
            f"{_CODEX} --no-alt-screen",
        ]
        started = run_cli("agent-tmux", "start", *common, env=cli_env)
        assert started.ok(), started.output

        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline:
            if "› Ask Codex to do anything" in _capture_pane(session):
                break
            time.sleep(0.05)
        else:
            raise AssertionError("real Codex composer did not become idle")

        woken = run_cli(
            "agent-tmux",
            "wake",
            *common,
            "--submit-delay",
            "0.4",
            env=cli_env,
        )
        assert woken.ok(), woken.output
        if "consumption observed" not in woken.stdout:
            assert "queued" in woken.stdout or "unacknowledged" in woken.stdout
            recovered = run_cli(
                "agent-tmux",
                "wait",
                *common,
                "--submit-delay",
                "0.2",
                "--pane-probe-interval",
                "0.2",
                "--max-wakes",
                "1",
                env=cli_env,
                timeout=60.0,
            )
            assert recovered.ok(), recovered.output

        prompt = _normalise(build_wake_prompt(identity))
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            pane = _capture_pane(session)
            normalised_pane = _normalise(pane)
            if prompt in normalised_pane and "› Ask Codex to do anything" in pane:
                assert normalised_pane.count(prompt) == 1
                break
            time.sleep(0.05)
        else:
            raise AssertionError("real Codex did not consume the wake and restore its composer")

        config = AgentTmuxConfig(
            identity=identity,
            session=session,
            cwd=repository,
            agent_command=(_CODEX, "--no-alt-screen"),
            registry_dir=runtime / "synapse-agent-tmux",
        )
        payload = json.loads(registry_path(config).read_text(encoding="utf-8"))
        assert payload["last_inject_returncode"] == 0
        assert payload["pending_wake"] is False
        assert payload["wake_prompt_staged"] is False


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
