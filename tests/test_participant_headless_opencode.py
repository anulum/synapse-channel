# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li

import json
import os
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from synapse_channel.participants.envelope import TurnRequest
from synapse_channel.participants.headless_opencode import (
    OpenCodeParticipant,
    build_opencode_argv,
    compose_opencode_prompt,
)


class FakeRunner:
    def __init__(self, *, version: str = "1.17.20", returncode: int = 0) -> None:
        self.version = version
        self.returncode = returncode
        self.calls: list[tuple[list[str], Mapping[str, str] | None]] = []

    def __call__(
        self,
        args: Sequence[str],
        *,
        capture_output: bool,
        text: bool,
        check: bool,
        timeout: float | None,
        input: str | None = None,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        del capture_output, text, check, timeout, input, cwd
        self.calls.append((list(args), env))
        if list(args)[1:] == ["--version"]:
            return subprocess.CompletedProcess(args, 0, self.version + "\n", "")
        lines = [
            json.dumps(
                {
                    "type": "text",
                    "timestamp": 1,
                    "sessionID": "ses-1",
                    "part": {"type": "text", "text": "answer"},
                }
            ),
            json.dumps(
                {
                    "type": "step_finish",
                    "timestamp": 2,
                    "sessionID": "ses-1",
                    "part": {
                        "type": "step-finish",
                        "reason": "stop",
                        "cost": 0,
                        "tokens": {"input": 2, "output": 1},
                    },
                }
            ),
        ]
        return subprocess.CompletedProcess(
            args, self.returncode, "\n".join(lines), "private stderr"
        )


def test_argv_supports_local_resume_attach_and_thinking_without_auto(tmp_path: Path) -> None:
    argv = build_opencode_argv(
        prompt="p",
        directory=tmp_path,
        model="provider/model",
        resume_session="ses-old",
        attach="https://example.test",
        thinking=True,
    )
    assert argv[:4] == ["opencode", "run", "--format", "json"]
    assert ["--session", "ses-old"] == argv[argv.index("--session") : argv.index("--session") + 2]
    assert "--attach" in argv
    assert "--thinking" in argv
    assert "--auto" not in argv


def test_turn_negotiates_exact_version_and_parses_result(tmp_path: Path) -> None:
    runner = FakeRunner()
    participant = OpenCodeParticipant(
        "seat/opencode", directory=tmp_path, model="provider/model", runner=runner
    )
    result = participant.run_turn(TurnRequest("topic", "prompt", context="rules"))
    assert result["answer"] == "answer"
    assert result["session"] == "ses-1"
    assert result["model"] == ""
    assert "----- TASK -----" in runner.calls[1][0][-1]


def test_version_drift_refuses_turn_before_prompt_process(tmp_path: Path) -> None:
    runner = FakeRunner(version="1.18.0")
    participant = OpenCodeParticipant("seat/opencode", directory=tmp_path, runner=runner)
    result = participant.run_turn(TurnRequest("topic", "prompt"))
    assert result["is_error"] is True
    assert "outside verified schema 1.17.20" in result["reason"]
    assert len(runner.calls) == 1


def test_attach_password_is_environment_only(tmp_path: Path) -> None:
    password = tmp_path / "password"
    password.write_text("hidden\n")
    os.chmod(password, 0o600)
    runner = FakeRunner()
    participant = OpenCodeParticipant(
        "seat/opencode",
        directory=tmp_path,
        attach="https://example.test",
        password_file=str(password),
        runner=runner,
    )
    result = participant.run_turn(TurnRequest("topic", "prompt"))
    assert result["is_error"] is False
    argv, environment = runner.calls[1]
    assert "hidden" not in argv
    assert environment is not None
    assert environment["OPENCODE_SERVER_PASSWORD"] == "hidden"
    assert environment["OPENCODE_SERVER_USERNAME"] == "opencode"


def test_prompt_composition_is_stable() -> None:
    assert compose_opencode_prompt("", "ask") == "ask"
    assert compose_opencode_prompt("context", "ask").endswith("----- TASK -----\n\nask")


def test_identity_channel_and_health_are_exact_version_gated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(shutil, "which", lambda _binary: "/bin/opencode")
    participant = OpenCodeParticipant("seat/opencode", directory=tmp_path, runner=FakeRunner())
    assert participant.identity == "seat/opencode"
    assert participant.channel.value == "headless"
    assert participant.health().available is True
    drift = OpenCodeParticipant(
        "seat/drift", directory=tmp_path, runner=FakeRunner(version="1.18.0")
    )
    assert drift.health().available is False
    monkeypatch.setattr(shutil, "which", lambda _binary: None)
    assert participant.health().available is False


class ExceptionalRunner(FakeRunner):
    def __init__(self, error: BaseException) -> None:
        super().__init__()
        self.error = error

    def __call__(
        self,
        args: Sequence[str],
        *,
        capture_output: bool,
        text: bool,
        check: bool,
        timeout: float | None,
        input: str | None = None,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        if list(args)[1:] == ["--version"]:
            return super().__call__(
                args,
                capture_output=capture_output,
                text=text,
                check=check,
                timeout=timeout,
                input=input,
                cwd=cwd,
                env=env,
            )
        raise self.error


@pytest.mark.parametrize(
    "error",
    [subprocess.TimeoutExpired(["opencode"], 1), OSError("start failed")],
)
def test_process_timeout_and_start_failure_are_typed_errors(
    tmp_path: Path, error: BaseException
) -> None:
    participant = OpenCodeParticipant(
        "seat/opencode", directory=tmp_path, runner=ExceptionalRunner(error)
    )
    result = participant.run_turn(TurnRequest("topic", "prompt"))
    assert result["is_error"] is True


def test_nonzero_exit_is_failed_even_if_stdout_contains_answer(tmp_path: Path) -> None:
    participant = OpenCodeParticipant(
        "seat/opencode", directory=tmp_path, runner=FakeRunner(returncode=3)
    )
    result = participant.run_turn(TurnRequest("topic", "prompt"))
    assert result["is_error"] is True
    assert result["answer"] == ""


@pytest.mark.asyncio
async def test_async_turn_stamps_configured_model(tmp_path: Path) -> None:
    participant = OpenCodeParticipant(
        "seat/opencode",
        directory=tmp_path,
        model="provider/model",
        runner=FakeRunner(),
    )
    result = await participant.take_turn(TurnRequest("topic", "prompt"))
    assert result["model"] == "provider/model"
