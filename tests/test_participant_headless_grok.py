# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the headless Grok participant driver
"""Tests for :mod:`synapse_channel.participants.headless_grok`.

Every turn is driven through an injected fake runner, so the suite exercises the driver's argv
construction (verified against ``grok --help``), stream parsing, and failure handling without
ever invoking the real Grok CLI. Fake stdout uses the verified native Grok
``thought`` / ``text`` / ``end`` streaming-json shape.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Sequence

import pytest

from synapse_channel.participants.envelope import TurnRequest
from synapse_channel.participants.headless_grok import (
    GrokParticipant,
    build_grok_argv,
)
from synapse_channel.participants.participant import ParticipantChannel


def _stream(answer: str = "pong", *, session: str = "gs-1") -> str:
    """Build a minimal native Grok streaming-json transcript."""
    lines = [
        json.dumps({"type": "thought", "data": "reasoning "}),
        json.dumps({"type": "text", "data": answer}),
        json.dumps(
            {
                "type": "end",
                "stopReason": "EndTurn",
                "sessionId": session,
                "requestId": "req-test",
            }
        ),
    ]
    return "\n".join(lines) + "\n"


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


# --- build_grok_argv ------------------------------------------------------------------


def test_argv_fresh_turn_requests_streaming_json_and_plan_mode() -> None:
    argv = build_grok_argv(prompt="hi")
    assert argv == [
        "grok",
        "--single",
        "hi",
        "--output-format",
        "streaming-json",
        "--permission-mode",
        "plan",
    ]


def test_argv_with_model_rules_and_resume() -> None:
    argv = build_grok_argv(prompt="hi", model="grok-4", rules="role: tester", resume_session="s9")
    assert "--model" in argv and argv[argv.index("--model") + 1] == "grok-4"
    # Context rides the system-prompt append, not the user prompt.
    assert "--rules" in argv and argv[argv.index("--rules") + 1] == "role: tester"
    assert "--resume" in argv and argv[argv.index("--resume") + 1] == "s9"
    # The single-turn prompt stays positional right after --single.
    assert argv[1:3] == ["--single", "hi"]


def test_argv_custom_binary_and_permission_mode() -> None:
    argv = build_grok_argv(prompt="hi", binary="/opt/grok", permission_mode="default")
    assert argv[0] == "/opt/grok"
    assert argv[argv.index("--permission-mode") + 1] == "default"


# --- run_turn -------------------------------------------------------------------------


def _request() -> TurnRequest:
    return TurnRequest(topic_id="t", prompt="say pong", context="role: tester")


def test_run_turn_parses_stream_and_routes_context_to_rules() -> None:
    runner = _FakeRunner(stdout=_stream(answer="pong", session="gs-9"))
    seat = GrokParticipant("SC/grok-a", runner=runner)
    result = seat.run_turn(_request())
    assert result["answer"] == "pong"
    assert result["session"] == "gs-9"
    assert result["is_error"] is False
    assert result["channel"] == "headless"
    # Context goes to --rules, never into the positional --single prompt.
    argv = list(runner.calls[0])
    assert argv[argv.index("--rules") + 1] == "role: tester"
    assert argv[argv.index("--single") + 1] == "say pong"
    assert runner.inputs[0] == ""


def test_run_turn_timeout_is_error() -> None:
    def runner(args: Sequence[str], **_: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd=list(args), timeout=1.0)

    seat = GrokParticipant("SC/grok-a", runner=runner, timeout=1.0)
    result = seat.run_turn(_request())
    assert result["is_error"] is True
    assert "timeout" in result["reason"]


def test_run_turn_os_error_is_error() -> None:
    def runner(args: Sequence[str], **_: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("no grok")

    seat = GrokParticipant("SC/grok-a", runner=runner)
    result = seat.run_turn(_request())
    assert result["is_error"] is True
    assert "failed to run" in result["reason"]


def test_run_turn_nonzero_exit_without_answer_is_error() -> None:
    runner = _FakeRunner(stdout="", stderr="boom", returncode=1)
    seat = GrokParticipant("SC/grok-a", runner=runner)
    result = seat.run_turn(_request())
    assert result["is_error"] is True
    assert "exited 1" in result["reason"]
    assert "provider diagnostic withheld" in result["reason"]
    assert "boom" not in result["reason"]


def test_run_turn_nonzero_exit_with_answer_is_trusted() -> None:
    runner = _FakeRunner(stdout=_stream(answer="answered anyway"), stderr="warn", returncode=1)
    seat = GrokParticipant("SC/grok-a", runner=runner)
    result = seat.run_turn(_request())
    assert result["is_error"] is False
    assert result["answer"] == "answered anyway"


def test_run_turn_nonzero_exit_empty_stderr_notes_no_output() -> None:
    runner = _FakeRunner(stdout="", stderr="", returncode=2)
    seat = GrokParticipant("SC/grok-a", runner=runner)
    assert "no diagnostic output" in seat.run_turn(_request())["reason"]


# --- identity / channel / health ------------------------------------------------------


def test_identity_and_channel() -> None:
    seat = GrokParticipant("SC/grok-a")
    assert seat.identity == "SC/grok-a"
    assert seat.channel is ParticipantChannel.HEADLESS


def test_health_available_when_binary_resolves(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: "/home/u/.local/bin/grok")
    health = GrokParticipant("SC/grok-a").health()
    assert health.available is True
    assert "/home/u/.local/bin/grok" in health.detail


def test_health_unavailable_when_binary_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    health = GrokParticipant("SC/grok-a", binary="nope").health()
    assert health.available is False
    assert "not found on PATH" in health.detail


# --- async surface --------------------------------------------------------------------


async def test_take_turn_wraps_run_turn() -> None:
    runner = _FakeRunner(stdout=_stream(answer="async pong"))
    seat = GrokParticipant("SC/grok-a", runner=runner)
    result = await seat.take_turn(_request())
    assert result["answer"] == "async pong"
