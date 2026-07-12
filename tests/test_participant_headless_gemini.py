# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — headless Gemini driver regressions

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence

import pytest

from synapse_channel.participants.envelope import TurnRequest
from synapse_channel.participants.headless_gemini import (
    DEFAULT_APPROVAL_MODE,
    GeminiParticipant,
    build_gemini_argv,
    compose_gemini_prompt,
)
from synapse_channel.participants.participant import ParticipantChannel


def _stream(answer: str = "pong") -> str:
    lines = [
        {"type": "init", "timestamp": "t", "session_id": "sess-1", "model": "gemini-2.5-pro"},
        {
            "type": "message",
            "timestamp": "t",
            "role": "assistant",
            "content": answer,
            "delta": True,
        },
        {"type": "result", "timestamp": "t", "status": "success", "stats": {}},
    ]
    return "\n".join(json.dumps(line) for line in lines)


class _FakeRunner:
    def __init__(
        self,
        *,
        stdout: str = "",
        stderr: str = "",
        returncode: int = 0,
        raises: BaseException | None = None,
    ) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        self.raises = raises
        self.argv: list[str] = []

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
        self.argv = list(args)
        if self.raises is not None:
            raise self.raises
        return subprocess.CompletedProcess(
            args=list(args),
            returncode=self.returncode,
            stdout=self.stdout,
            stderr=self.stderr,
        )


def test_compose_prompt_without_context_is_identity() -> None:
    assert compose_gemini_prompt("", "question") == "question"


def test_compose_prompt_prepends_context_under_separator() -> None:
    combined = compose_gemini_prompt("ground rules", "question")
    assert combined.startswith("ground rules")
    assert combined.endswith("question")
    assert "----- TASK -----" in combined


def test_build_argv_defaults_to_stream_json_and_plan_mode() -> None:
    argv = build_gemini_argv(prompt="hi")
    assert argv == [
        "gemini",
        "--prompt",
        "hi",
        "--output-format",
        "stream-json",
        "--approval-mode",
        "plan",
    ]


def test_build_argv_appends_model_and_resume_only_when_set() -> None:
    argv = build_gemini_argv(
        prompt="hi",
        binary="/opt/gemini",
        model="gemini-2.5-pro",
        resume_session="latest",
        approval_mode=DEFAULT_APPROVAL_MODE,
    )
    assert argv[0] == "/opt/gemini"
    assert argv[-4:] == ["--model", "gemini-2.5-pro", "--resume", "latest"]


def test_health_reports_missing_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda _name: None)
    health = GeminiParticipant("seat/gemini").health()
    assert not health.available
    assert "not found" in health.detail


def test_health_reports_resolved_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda _name: "/usr/bin/gemini")
    health = GeminiParticipant("seat/gemini").health()
    assert health.available
    assert health.detail == "gemini binary at /usr/bin/gemini"
    assert health.channel is ParticipantChannel.HEADLESS


def test_identity_and_channel_properties() -> None:
    participant = GeminiParticipant("seat/gemini")
    assert participant.identity == "seat/gemini"
    assert participant.channel is ParticipantChannel.HEADLESS


def test_run_turn_parses_stream_and_folds_context() -> None:
    runner = _FakeRunner(stdout=_stream())
    participant = GeminiParticipant("seat/gemini", runner=runner)
    result = participant.run_turn(
        TurnRequest(topic_id="t1", prompt="ping?", context="rules", resume_session="latest")
    )
    assert result["answer"] == "pong"
    assert not result["is_error"]
    assert result["session"] == "sess-1"
    assert runner.argv[1] == "--prompt"
    assert runner.argv[2].startswith("rules")
    assert runner.argv[2].endswith("ping?")
    assert runner.argv[-2:] == ["--resume", "latest"]


def test_run_turn_nonzero_exit_with_no_answer_is_error() -> None:
    runner = _FakeRunner(stdout="", stderr="boom", returncode=55)
    participant = GeminiParticipant("seat/gemini", runner=runner)
    result = participant.run_turn(TurnRequest(topic_id="t1", prompt="ping?"))
    assert result["is_error"]
    assert "exited 55" in result["reason"]
    assert "boom" in result["reason"]


def test_run_turn_nonzero_exit_with_answer_keeps_answer() -> None:
    runner = _FakeRunner(stdout=_stream(), returncode=1)
    participant = GeminiParticipant("seat/gemini", runner=runner)
    result = participant.run_turn(TurnRequest(topic_id="t1", prompt="ping?"))
    assert result["answer"] == "pong"
    assert not result["is_error"]


def test_run_turn_timeout_is_error_result() -> None:
    runner = _FakeRunner(raises=subprocess.TimeoutExpired(cmd=["gemini"], timeout=1.0))
    participant = GeminiParticipant("seat/gemini", runner=runner, timeout=1.0)
    result = participant.run_turn(TurnRequest(topic_id="t1", prompt="ping?"))
    assert result["is_error"]


def test_run_turn_oserror_is_error_result() -> None:
    runner = _FakeRunner(raises=OSError("no such binary"))
    participant = GeminiParticipant("seat/gemini", runner=runner)
    result = participant.run_turn(TurnRequest(topic_id="t1", prompt="ping?"))
    assert result["is_error"]


@pytest.mark.asyncio
async def test_take_turn_wraps_run_turn_and_stamps_model() -> None:
    runner = _FakeRunner(stdout=_stream())
    participant = GeminiParticipant("seat/gemini", model="gemini-2.5-pro", runner=runner)
    result = await participant.take_turn(TurnRequest(topic_id="t1", prompt="ping?"))
    assert result["answer"] == "pong"
    assert result["model"] == "gemini-2.5-pro"
    assert runner.argv[-2:] == ["--model", "gemini-2.5-pro"]
