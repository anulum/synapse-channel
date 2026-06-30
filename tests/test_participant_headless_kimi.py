# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the headless Kimi participant driver
"""Tests for :mod:`synapse_channel.participants.headless_kimi`.

Every turn is driven through an injected fake runner, so the suite exercises the driver's
argv construction, prompt composition, stream parsing, stderr session extraction, and failure
handling without ever invoking a real model.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Sequence

import pytest

from synapse_channel.participants.envelope import TurnRequest
from synapse_channel.participants.headless_kimi import (
    KimiParticipant,
    build_kimi_argv,
    compose_kimi_prompt,
)
from synapse_channel.participants.participant import ParticipantChannel

_STDERR = "\nTo resume this session: kimi -r sess-77\n"


def _stream(answer: str = "pong") -> str:
    return json.dumps({"role": "assistant", "content": answer}) + "\n"


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


# --- compose_kimi_prompt --------------------------------------------------------------


def test_compose_prompt_without_context_is_unchanged() -> None:
    assert compose_kimi_prompt("", "do the thing") == "do the thing"


def test_compose_prompt_prepends_context_under_a_separator() -> None:
    composed = compose_kimi_prompt("role: tester", "answer this")
    assert composed.startswith("role: tester")
    assert composed.endswith("answer this")
    assert "TASK" in composed


# --- build_kimi_argv ------------------------------------------------------------------


def test_argv_fresh_turn_requests_stream_json_in_plan_mode() -> None:
    argv = build_kimi_argv(prompt="hi")
    assert argv == [
        "kimi",
        "--print",
        "--output-format",
        "stream-json",
        "--plan",
        "-p",
        "hi",
    ]


def test_argv_without_plan_mode_drops_the_plan_flag() -> None:
    argv = build_kimi_argv(prompt="hi", plan_mode=False)
    assert "--plan" not in argv
    assert argv[-2:] == ["-p", "hi"]


def test_argv_with_model() -> None:
    argv = build_kimi_argv(prompt="hi", model="kimi-k2")
    assert "--model" in argv and argv[argv.index("--model") + 1] == "kimi-k2"
    assert argv[-2:] == ["-p", "hi"]


def test_argv_resume_adds_resume_flag() -> None:
    argv = build_kimi_argv(prompt="more", resume_session="sess-7")
    assert "-r" in argv and argv[argv.index("-r") + 1] == "sess-7"
    # The prompt stays last and positional after its -p flag.
    assert argv[-2:] == ["-p", "more"]


def test_argv_custom_binary() -> None:
    argv = build_kimi_argv(prompt="hi", binary="/opt/kimi")
    assert argv[0] == "/opt/kimi"


# --- run_turn -------------------------------------------------------------------------


def _request() -> TurnRequest:
    return TurnRequest(topic_id="t", prompt="say pong", context="role: tester")


def test_run_turn_parses_stream_and_extracts_session_from_stderr() -> None:
    runner = _FakeRunner(stdout=_stream(answer="pong"), stderr=_STDERR)
    seat = KimiParticipant("SC/kimi-a", runner=runner)
    result = seat.run_turn(_request())
    assert result["answer"] == "pong"
    assert result["session"] == "sess-77"
    assert result["is_error"] is False
    assert result["channel"] == "headless"
    assert result["cost_usd"] == 0.0
    # Context is folded into the positional prompt (Kimi has no system channel).
    prompt_arg = runner.calls[0][-1]
    assert "role: tester" in prompt_arg
    assert "say pong" in prompt_arg
    # stdin is closed with an empty string so kimi does not block on it.
    assert runner.inputs[0] == ""


def test_run_turn_timeout_is_error() -> None:
    def runner(args: Sequence[str], **_: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd=list(args), timeout=1.0)

    seat = KimiParticipant("SC/kimi-a", runner=runner, timeout=1.0)
    result = seat.run_turn(_request())
    assert result["is_error"] is True
    assert "timeout" in result["reason"]


def test_run_turn_os_error_is_error() -> None:
    def runner(args: Sequence[str], **_: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("no kimi")

    seat = KimiParticipant("SC/kimi-a", runner=runner)
    result = seat.run_turn(_request())
    assert result["is_error"] is True
    assert "failed to run" in result["reason"]


def test_run_turn_nonzero_exit_without_answer_is_error() -> None:
    runner = _FakeRunner(stdout="", stderr="bad", returncode=1)
    seat = KimiParticipant("SC/kimi-a", runner=runner)
    result = seat.run_turn(_request())
    assert result["is_error"] is True
    assert "exited 1" in result["reason"]
    assert "bad" in result["reason"]


def test_run_turn_nonzero_exit_with_answer_is_trusted() -> None:
    runner = _FakeRunner(stdout=_stream(answer="answered anyway"), stderr="warn", returncode=1)
    seat = KimiParticipant("SC/kimi-a", runner=runner)
    result = seat.run_turn(_request())
    assert result["is_error"] is False
    assert result["answer"] == "answered anyway"


def test_run_turn_nonzero_exit_empty_stderr_notes_no_output() -> None:
    runner = _FakeRunner(stdout="", stderr="", returncode=2)
    seat = KimiParticipant("SC/kimi-a", runner=runner)
    assert "no output" in seat.run_turn(_request())["reason"]


# --- identity / channel / health ------------------------------------------------------


def test_identity_and_channel() -> None:
    seat = KimiParticipant("SC/kimi-a")
    assert seat.identity == "SC/kimi-a"
    assert seat.channel is ParticipantChannel.HEADLESS


def test_health_available_when_binary_resolves(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/kimi")
    health = KimiParticipant("SC/kimi-a").health()
    assert health.available is True
    assert "/usr/bin/kimi" in health.detail


def test_health_unavailable_when_binary_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    health = KimiParticipant("SC/kimi-a", binary="nope").health()
    assert health.available is False
    assert "not found on PATH" in health.detail


# --- async surface --------------------------------------------------------------------


async def test_take_turn_wraps_run_turn() -> None:
    runner = _FakeRunner(stdout=_stream(answer="async pong"), stderr=_STDERR)
    seat = KimiParticipant("SC/kimi-a", runner=runner)
    result = await seat.take_turn(_request())
    assert result["answer"] == "async pong"
    assert result["session"] == "sess-77"
