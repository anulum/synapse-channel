# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the headless Codex participant driver
"""Tests for :mod:`synapse_channel.participants.headless_codex`.

Every turn is driven through an injected fake runner, so the suite exercises the driver's
argv construction, prompt composition, stream parsing, and failure handling without ever
invoking a real model.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Sequence

import pytest

from synapse_channel.participants.envelope import TurnRequest
from synapse_channel.participants.headless_codex import (
    CodexParticipant,
    build_codex_argv,
    compose_codex_prompt,
)
from synapse_channel.participants.participant import ParticipantChannel


def _stream(answer: str = "pong", *, thread: str = "th-1") -> str:
    started = json.dumps({"type": "thread.started", "thread_id": thread})
    message = json.dumps(
        {"type": "item.completed", "item": {"type": "agent_message", "text": answer}}
    )
    completed = json.dumps({"type": "turn.completed", "usage": {"output_tokens": 4}})
    return "\n".join([started, message, completed]) + "\n"


class _FakeRunner:
    """Records argv and the stdin input, returns a scripted completed process."""

    def __init__(self, *, stdout: str = "", stderr: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        self.calls: list[Sequence[str]] = []
        self.inputs: list[str | None] = []

    def __call__(
        self,
        args: Sequence[str],
        *,
        capture_output: bool = False,
        text: bool = False,
        check: bool = False,
        timeout: float | None = None,
        input: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append(args)
        self.inputs.append(input)
        return subprocess.CompletedProcess(
            args=list(args), returncode=self.returncode, stdout=self.stdout, stderr=self.stderr
        )


# --- compose_codex_prompt -------------------------------------------------------------


def test_compose_prompt_without_context_is_unchanged() -> None:
    assert compose_codex_prompt("", "do the thing") == "do the thing"


def test_compose_prompt_prepends_context_under_a_separator() -> None:
    composed = compose_codex_prompt("role: tester", "answer this")
    assert composed.startswith("role: tester")
    assert composed.endswith("answer this")
    assert "TASK" in composed


# --- build_codex_argv -----------------------------------------------------------------


def test_argv_fresh_turn_requests_json_and_read_only_sandbox() -> None:
    argv = build_codex_argv(prompt="hi")
    assert argv == [
        "codex",
        "exec",
        "--json",
        "--skip-git-repo-check",
        "--sandbox",
        "read-only",
        "--ephemeral",
        "hi",
    ]


def test_argv_fresh_with_model_and_persist() -> None:
    argv = build_codex_argv(prompt="hi", model="gpt-x", persist_session=True)
    assert "--model" in argv and argv[argv.index("--model") + 1] == "gpt-x"
    assert "--ephemeral" not in argv
    assert argv[-1] == "hi"


def test_argv_resume_uses_resume_subcommand_without_sandbox() -> None:
    argv = build_codex_argv(prompt="more", resume_session="sess-7", persist_session=True)
    assert argv[:4] == ["codex", "exec", "resume", "--json"]
    assert "--sandbox" not in argv
    assert "--ephemeral" not in argv
    # Session id then prompt, both positional and last.
    assert argv[-2:] == ["sess-7", "more"]


def test_argv_resume_ephemeral_when_not_persisting() -> None:
    argv = build_codex_argv(prompt="p", resume_session="s", persist_session=False)
    assert "--ephemeral" in argv


def test_argv_resume_includes_model() -> None:
    argv = build_codex_argv(prompt="p", resume_session="s", model="gpt-x", persist_session=True)
    assert argv[:3] == ["codex", "exec", "resume"]
    assert "--model" in argv and argv[argv.index("--model") + 1] == "gpt-x"
    assert argv[-2:] == ["s", "p"]


def test_argv_custom_sandbox_and_binary() -> None:
    argv = build_codex_argv(prompt="hi", binary="/opt/codex", sandbox="workspace-write")
    assert argv[0] == "/opt/codex"
    assert argv[argv.index("--sandbox") + 1] == "workspace-write"


# --- run_turn -------------------------------------------------------------------------


def _request() -> TurnRequest:
    return TurnRequest(topic_id="t", prompt="say pong", context="role: tester")


def test_run_turn_parses_stream_and_prepends_context_to_prompt() -> None:
    runner = _FakeRunner(stdout=_stream(answer="pong", thread="th-9"))
    seat = CodexParticipant("SC/codex-a", runner=runner)
    result = seat.run_turn(_request())
    assert result["answer"] == "pong"
    assert result["session"] == "th-9"
    assert result["is_error"] is False
    assert result["channel"] == "headless"
    assert result["cost_usd"] == 0.0
    # Context is folded into the positional prompt (Codex has no system channel).
    prompt_arg = runner.calls[0][-1]
    assert "role: tester" in prompt_arg
    assert "say pong" in prompt_arg
    # stdin is closed with an empty string so codex does not block on it.
    assert runner.inputs[0] == ""


def test_run_turn_timeout_is_error() -> None:
    def runner(args: Sequence[str], **_: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd=list(args), timeout=1.0)

    seat = CodexParticipant("SC/codex-a", runner=runner, timeout=1.0)
    result = seat.run_turn(_request())
    assert result["is_error"] is True
    assert "timeout" in result["reason"]


def test_run_turn_os_error_is_error() -> None:
    def runner(args: Sequence[str], **_: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("no codex")

    seat = CodexParticipant("SC/codex-a", runner=runner)
    result = seat.run_turn(_request())
    assert result["is_error"] is True
    assert "failed to run" in result["reason"]


def test_run_turn_nonzero_exit_without_answer_is_error() -> None:
    runner = _FakeRunner(stdout="", stderr="bad", returncode=1)
    seat = CodexParticipant("SC/codex-a", runner=runner)
    result = seat.run_turn(_request())
    assert result["is_error"] is True
    assert "exited 1" in result["reason"]
    assert "bad" in result["reason"]


def test_run_turn_nonzero_exit_with_answer_is_trusted() -> None:
    runner = _FakeRunner(stdout=_stream(answer="answered anyway"), stderr="warn", returncode=1)
    seat = CodexParticipant("SC/codex-a", runner=runner)
    result = seat.run_turn(_request())
    assert result["is_error"] is False
    assert result["answer"] == "answered anyway"


def test_run_turn_nonzero_exit_empty_stderr_notes_no_output() -> None:
    runner = _FakeRunner(stdout="", stderr="", returncode=2)
    seat = CodexParticipant("SC/codex-a", runner=runner)
    assert "no output" in seat.run_turn(_request())["reason"]


# --- identity / channel / health ------------------------------------------------------


def test_identity_and_channel() -> None:
    seat = CodexParticipant("SC/codex-a")
    assert seat.identity == "SC/codex-a"
    assert seat.channel is ParticipantChannel.HEADLESS


def test_health_available_when_binary_resolves(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/codex")
    health = CodexParticipant("SC/codex-a").health()
    assert health.available is True
    assert "/usr/bin/codex" in health.detail


def test_health_unavailable_when_binary_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    health = CodexParticipant("SC/codex-a", binary="nope").health()
    assert health.available is False
    assert "not found on PATH" in health.detail


# --- async surface --------------------------------------------------------------------


async def test_take_turn_wraps_run_turn() -> None:
    runner = _FakeRunner(stdout=_stream(answer="async pong"))
    seat = CodexParticipant("SC/codex-a", runner=runner)
    result = await seat.take_turn(_request())
    assert result["answer"] == "async pong"
