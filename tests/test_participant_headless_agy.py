# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the headless Antigravity CLI participant with a fake runner
"""Drive the Antigravity CLI participant with captured streams, never a real model call."""

from __future__ import annotations

import inspect
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from synapse_channel.participants.envelope import TurnRequest
from synapse_channel.participants.headless_agy import (
    DEFAULT_MODE,
    AgyParticipant,
    build_agy_argv,
)
from synapse_channel.participants.participant import ParticipantChannel

_FIXTURES = Path(__file__).parent / "fixtures" / "agy_stream"


def _capture(name: str) -> str:
    return (_FIXTURES / name).read_text(encoding="utf-8")


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
        self.input: str | None = None

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
        self.input = input
        if self.raises is not None:
            raise self.raises
        return subprocess.CompletedProcess(
            args=list(args), returncode=self.returncode, stdout=self.stdout, stderr=self.stderr
        )


def test_build_argv_defaults_to_stream_json_and_plan_mode() -> None:
    assert DEFAULT_MODE == "plan"
    assert build_agy_argv(prompt="hi") == [
        "agy",
        "--print",
        "hi",
        "--output-format",
        "stream-json",
        "--mode",
        "plan",
    ]


def test_build_argv_appends_model_and_a_joined_conversation_only_when_set() -> None:
    argv = build_agy_argv(
        prompt="hi",
        binary="/opt/agy",
        model="Gemini 4 Carbon (High)",
        conversation="-not-a-flag",
        mode="accept-edits",
    )
    assert argv[0] == "/opt/agy"
    assert argv[5:7] == ["--mode", "accept-edits"]
    assert argv[-3:] == ["--model", "Gemini 4 Carbon (High)", "--conversation=-not-a-flag"]
    assert "--dangerously-skip-permissions" not in argv


def test_health_reports_missing_and_resolved_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda _name: None)
    missing = AgyParticipant("seat/agy").health()
    assert not missing.available
    assert "not found" in missing.detail
    monkeypatch.setattr("shutil.which", lambda _name: "/usr/bin/agy")
    found = AgyParticipant("seat/agy").health()
    assert found.available
    assert found.detail == "agy binary at /usr/bin/agy; account entitlement not probed"
    assert found.channel is ParticipantChannel.HEADLESS


def test_the_default_runner_is_subprocess_run() -> None:
    default = inspect.signature(AgyParticipant).parameters["runner"].default
    assert default is subprocess.run


def test_identity_and_channel_properties() -> None:
    participant = AgyParticipant("seat/agy")
    assert participant.identity == "seat/agy"
    assert participant.channel is ParticipantChannel.HEADLESS


def test_run_turn_parses_a_captured_reply_and_folds_context_and_resume() -> None:
    runner = _FakeRunner(stdout=_capture("cli_1_5_10_resumed.jsonl"))
    participant = AgyParticipant("seat/agy", runner=runner)
    result = participant.run_turn(
        TurnRequest(topic_id="t1", prompt="ping?", context="rules", resume_session="conv-9")
    )
    assert result["answer"] == "AGY-OK"
    assert not result["is_error"]
    assert result["session"] == "ba770272-6683-4b63-b4ea-2a5e2a065078"
    assert (result["input_tokens"], result["output_tokens"]) == (17753, 1173)
    assert runner.argv[1] == "--print"
    assert runner.argv[2].startswith("rules")
    assert runner.argv[2].endswith("ping?")
    assert runner.argv[-1] == "--conversation=conv-9"
    assert runner.input == ""


def test_run_turn_reports_a_turn_ended_by_denied_tools_as_an_error() -> None:
    runner = _FakeRunner(
        stdout=_capture("cli_1_5_10_denied_command.jsonl"),
        stderr="no output produced — a tool required the command permission",
    )
    result = AgyParticipant("seat/agy", runner=runner).run_turn(
        TurnRequest(topic_id="t1", prompt="ping?")
    )
    assert result["is_error"]
    assert not result["abstained"]
    assert result["reason"] == "no_answer"
    assert result["stop_reason"] == "the provider produced no answer; denied actions: command"
    assert (result["input_tokens"], result["output_tokens"]) == (69832, 2827)


def test_run_turn_nonzero_exit_with_no_answer_is_an_error_without_the_diagnostic() -> None:
    runner = _FakeRunner(stdout="", stderr="secret detail", returncode=55)
    result = AgyParticipant("seat/agy", runner=runner).run_turn(
        TurnRequest(topic_id="t1", prompt="ping?")
    )
    assert result["is_error"]
    assert "exited 55" in result["reason"]
    assert "secret detail" not in result["reason"]


@pytest.mark.parametrize(
    "raises",
    [subprocess.TimeoutExpired(cmd=["agy"], timeout=1.0), OSError("no such binary")],
)
def test_run_turn_timeout_or_start_failure_is_an_error_result(raises: BaseException) -> None:
    participant = AgyParticipant("seat/agy", runner=_FakeRunner(raises=raises), timeout=1.0)
    result = participant.run_turn(TurnRequest(topic_id="t1", prompt="ping?"))
    assert result["is_error"]


async def test_take_turn_wraps_run_turn_and_stamps_model() -> None:
    runner = _FakeRunner(stdout=_capture("cli_1_5_10_reply.jsonl"))
    participant = AgyParticipant("seat/agy", model="Gemini 4 Carbon (High)", runner=runner)
    result = await participant.take_turn(TurnRequest(topic_id="t1", prompt="ping?"))
    assert result["answer"] == "AGY-OK"
    assert result["model"] == "Gemini 4 Carbon (High)"
    assert runner.argv[-2:] == ["--model", "Gemini 4 Carbon (High)"]
