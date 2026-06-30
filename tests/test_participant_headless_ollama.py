# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the headless Ollama participant driver
"""Tests for :mod:`synapse_channel.participants.headless_ollama`.

Every turn is driven through an injected fake runner, so the suite exercises the driver's
argv construction, prompt composition, output distillation, and failure handling without ever
invoking a real local model.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Sequence

import pytest

from synapse_channel.participants.envelope import TurnRequest
from synapse_channel.participants.headless_ollama import (
    OllamaParticipant,
    build_ollama_argv,
    compose_ollama_prompt,
)
from synapse_channel.participants.participant import ParticipantChannel


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


# --- compose_ollama_prompt ------------------------------------------------------------


def test_compose_prompt_without_context_is_unchanged() -> None:
    assert compose_ollama_prompt("", "do the thing") == "do the thing"


def test_compose_prompt_prepends_context_under_a_separator() -> None:
    composed = compose_ollama_prompt("role: tester", "answer this")
    assert composed.startswith("role: tester")
    assert composed.endswith("answer this")
    assert "TASK" in composed


# --- build_ollama_argv ----------------------------------------------------------------


def test_argv_default_runs_model_with_hide_thinking() -> None:
    argv = build_ollama_argv(prompt="hi", model="gemma3:1b")
    assert argv == ["ollama", "run", "gemma3:1b", "--hidethinking", "hi"]


def test_argv_without_hide_thinking() -> None:
    argv = build_ollama_argv(prompt="hi", model="llama3", hide_thinking=False)
    assert argv == ["ollama", "run", "llama3", "hi"]


def test_argv_custom_binary() -> None:
    argv = build_ollama_argv(prompt="hi", model="m", binary="/opt/ollama")
    assert argv[0] == "/opt/ollama"
    assert argv[-1] == "hi"


# --- run_turn -------------------------------------------------------------------------


def _request() -> TurnRequest:
    # A resume_session is supplied to prove the driver ignores it (Ollama run is stateless).
    return TurnRequest(
        topic_id="t", prompt="say pong", context="role: tester", resume_session="ignored"
    )


def test_run_turn_distils_reply_and_prepends_context() -> None:
    runner = _FakeRunner(stdout="pong\n\n")
    seat = OllamaParticipant("SC/ollama-a", model="gemma3:1b", runner=runner)
    result = seat.run_turn(_request())
    assert result["answer"] == "pong"
    assert result["is_error"] is False
    assert result["channel"] == "headless"
    assert result["cost_usd"] == 0.0
    # A local turn carries no session, even when the request supplied a resume token.
    assert result["session"] == ""
    # The model is named and the context is folded into the positional prompt.
    assert "gemma3:1b" in runner.calls[0]
    prompt_arg = runner.calls[0][-1]
    assert "role: tester" in prompt_arg
    assert "say pong" in prompt_arg
    # stdin is closed with an empty string so ollama does not block on it.
    assert runner.inputs[0] == ""


def test_run_turn_timeout_is_error() -> None:
    def runner(args: Sequence[str], **_: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd=list(args), timeout=1.0)

    seat = OllamaParticipant("SC/ollama-a", model="m", runner=runner, timeout=1.0)
    result = seat.run_turn(_request())
    assert result["is_error"] is True
    assert "timeout" in result["reason"]


def test_run_turn_os_error_is_error() -> None:
    def runner(args: Sequence[str], **_: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("no ollama")

    seat = OllamaParticipant("SC/ollama-a", model="m", runner=runner)
    result = seat.run_turn(_request())
    assert result["is_error"] is True
    assert "failed to run" in result["reason"]


def test_run_turn_nonzero_exit_without_answer_is_error() -> None:
    runner = _FakeRunner(stdout="", stderr="Error: pull model manifest", returncode=1)
    seat = OllamaParticipant("SC/ollama-a", model="no-such", runner=runner)
    result = seat.run_turn(_request())
    assert result["is_error"] is True
    assert "exited 1" in result["reason"]
    assert "pull model manifest" in result["reason"]


def test_run_turn_nonzero_exit_with_answer_is_trusted() -> None:
    runner = _FakeRunner(stdout="answered anyway\n", stderr="warn", returncode=1)
    seat = OllamaParticipant("SC/ollama-a", model="m", runner=runner)
    result = seat.run_turn(_request())
    assert result["is_error"] is False
    assert result["answer"] == "answered anyway"


def test_run_turn_nonzero_exit_empty_stderr_notes_no_output() -> None:
    runner = _FakeRunner(stdout="", stderr="", returncode=2)
    seat = OllamaParticipant("SC/ollama-a", model="m", runner=runner)
    assert "no output" in seat.run_turn(_request())["reason"]


# --- identity / channel / health ------------------------------------------------------


def test_identity_and_channel() -> None:
    seat = OllamaParticipant("SC/ollama-a", model="m")
    assert seat.identity == "SC/ollama-a"
    assert seat.channel is ParticipantChannel.HEADLESS


def test_health_available_names_binary_and_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/local/bin/ollama")
    health = OllamaParticipant("SC/ollama-a", model="gemma3:1b").health()
    assert health.available is True
    assert "/usr/local/bin/ollama" in health.detail
    assert "gemma3:1b" in health.detail


def test_health_unavailable_when_binary_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    health = OllamaParticipant("SC/ollama-a", model="m", binary="nope").health()
    assert health.available is False
    assert "not found on PATH" in health.detail


# --- async surface --------------------------------------------------------------------


async def test_take_turn_wraps_run_turn() -> None:
    runner = _FakeRunner(stdout="async pong\n")
    seat = OllamaParticipant("SC/ollama-a", model="m", runner=runner)
    result = await seat.take_turn(_request())
    assert result["answer"] == "async pong"
