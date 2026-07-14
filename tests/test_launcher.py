# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the local team launcher

from __future__ import annotations

import json
import subprocess
import sys
import time
from collections.abc import Callable

import pytest

from http_server_helpers import LocalHttpResponder
from hub_e2e_helpers import _free_port
from synapse_channel.client.launcher import (
    FALLBACK_MODEL,
    FAST_MODEL_PREFERENCES,
    REASON_MODEL_PREFERENCES,
    _shutdown,
    build_hub_command,
    build_worker_command,
    detect_model,
    plan_team,
    run_team,
)


def _detect_with_models(preferred: list[str], models: list[str]) -> str | None:
    body = json.dumps({"models": [{"name": name} for name in models]}).encode("utf-8")
    with LocalHttpResponder(body=body) as server:
        detected = detect_model(preferred, base_url=server.url)
        assert [(request.method, request.path) for request in server.requests] == [
            ("GET", "/api/tags")
        ]
    return detected


def _python_command(source: str) -> list[str]:
    return [sys.executable, "-c", source]


def _launching_popen_factory(
    commands: list[list[str]],
) -> tuple[Callable[..., subprocess.Popen[str]], list[subprocess.Popen[str]]]:
    iterator = iter(commands)
    launched: list[subprocess.Popen[str]] = []

    def popen(_argv: list[str], **_kwargs: object) -> subprocess.Popen[str]:
        proc = subprocess.Popen(
            next(iterator), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
        )
        launched.append(proc)
        return proc

    return popen, launched


# --- detect_model ------------------------------------------------------------


def test_detect_model_match_with_tag_returns_full_name() -> None:
    got = _detect_with_models(["gemma3:4b"], ["gemma3:4b", "llama3:latest"])
    assert got == "gemma3:4b"


def test_detect_model_match_without_tag_returns_family() -> None:
    got = _detect_with_models(["llama3"], ["llama3:latest"])
    assert got == "llama3"


def test_detect_model_substring_match() -> None:
    got = _detect_with_models(["gemma"], ["my-gemma:1b"])
    assert got == "my-gemma"


def test_detect_model_falls_back_to_first_installed() -> None:
    got = _detect_with_models(["absent"], ["foo:1b"])
    assert got == "foo"


def test_detect_model_none_when_empty() -> None:
    assert _detect_with_models(["x"], []) is None


def test_detect_model_none_on_error() -> None:
    assert detect_model(["x"], base_url=f"http://127.0.0.1:{_free_port()}") is None


# --- command builders --------------------------------------------------------


def test_build_hub_command() -> None:
    cmd = build_hub_command(8876)
    assert cmd[1:] == ["-m", "synapse_channel.cli", "hub", "--port", "8876"]


def test_build_worker_command() -> None:
    cmd = build_worker_command("FAST", "llama3", "ws://localhost:8876")
    assert "worker" in cmd
    assert "--name" in cmd and "FAST" in cmd
    assert "--model" in cmd and "llama3" in cmd
    assert "ws://localhost:8876" in cmd


# --- plan_team ---------------------------------------------------------------


def test_plan_team_hub_only_when_no_workers() -> None:
    specs = plan_team(8876, no_workers=True)
    assert [label for label, _ in specs] == ["hub"]


def test_plan_team_single_worker_when_models_match() -> None:
    specs = plan_team(8876, detect=lambda prefs: "m")
    assert [label for label, _ in specs] == ["hub", "FAST"]


def test_plan_team_two_workers_when_models_differ() -> None:
    def detect(prefs: list[str]) -> str:
        return "fast" if prefs == FAST_MODEL_PREFERENCES else "reason"

    specs = plan_team(8876, detect=detect)
    assert [label for label, _ in specs] == ["hub", "FAST", "REASON"]
    reason_cmd = specs[2][1]
    assert "reason" in reason_cmd


def test_plan_team_respects_explicit_models() -> None:
    specs = plan_team(8876, fast_model="x", reason_model="y")
    fast_cmd = specs[1][1]
    reason_cmd = specs[2][1]
    assert "x" in fast_cmd
    assert "y" in reason_cmd


def test_plan_team_uses_fallback_when_detection_empty() -> None:
    specs = plan_team(8876, detect=lambda prefs: None)
    assert [label for label, _ in specs] == ["hub", "FAST"]
    assert FALLBACK_MODEL in specs[1][1]


def test_plan_team_prefixes_worker_names() -> None:
    def detect(prefs: list[str]) -> str:
        return "fast" if prefs == FAST_MODEL_PREFERENCES else "reason"

    specs = plan_team(8876, prefix="remanentia/", detect=detect)
    assert [label for label, _ in specs] == ["hub", "remanentia/FAST", "remanentia/REASON"]
    # The prefixed name is also what the worker registers under on the hub.
    assert "remanentia/FAST" in specs[1][1]
    assert "remanentia/REASON" in specs[2][1]
    # The reasoning preference list is still consulted.
    assert REASON_MODEL_PREFERENCES  # sanity: constant is populated


# --- _shutdown ---------------------------------------------------------------


def test_shutdown_terminates_running_kills_stubborn_and_skips_exited() -> None:
    running = subprocess.Popen(_python_command("import time; time.sleep(30)"), text=True)
    stubborn = subprocess.Popen(
        _python_command(
            "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)"
        ),
        text=True,
    )
    already_done = subprocess.Popen(_python_command(""), text=True)
    already_done.wait(timeout=2)

    try:
        _shutdown([("a", running), ("b", stubborn), ("c", already_done)], timeout_seconds=0.05)
        assert running.poll() is not None
        assert stubborn.poll() is not None
        assert already_done.poll() == 0
    finally:
        for proc in (running, stubborn):
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=2)


# --- run_team ----------------------------------------------------------------


def test_run_team_returns_exit_code_of_first_dead_child(
    capsys: pytest.CaptureFixture[str],
) -> None:
    popen, launched = _launching_popen_factory(
        [
            _python_command("import time; time.sleep(30)"),
            _python_command("import sys; sys.exit(3)"),
        ]
    )
    result = run_team(
        9999,
        prefix="--help$(touch injected)",
        popen=popen,
        sleep=lambda _seconds: time.sleep(0.05),
        detect=lambda prefs: "m",
    )
    assert result == 3
    assert all(proc.poll() is not None for proc in launched)
    out = capsys.readouterr().out
    assert "--uri=ws://localhost:9999 --name=USER" in out
    assert "--target='--help$(touch injected)FAST' -- \"status?\"" in out


def test_run_team_polls_again_while_children_alive() -> None:
    popen, launched = _launching_popen_factory(
        [
            _python_command("import time; time.sleep(30)"),
            _python_command("import sys, time; time.sleep(0.08); sys.exit(5)"),
        ]
    )
    result = run_team(
        9999,
        popen=popen,
        sleep=lambda _seconds: time.sleep(0.05),
        detect=lambda prefs: "m",
    )
    assert result == 5
    assert all(proc.poll() is not None for proc in launched)


def test_run_team_maps_zero_exit_to_one() -> None:
    popen, launched = _launching_popen_factory([_python_command("")])
    result = run_team(
        9999,
        no_workers=True,
        popen=popen,
        sleep=lambda _seconds: time.sleep(0.02),
    )
    assert result == 1
    assert all(proc.poll() is not None for proc in launched)


def test_run_team_handles_keyboard_interrupt() -> None:
    popen, launched = _launching_popen_factory([_python_command("import time; time.sleep(30)")])

    def interrupting_sleep(seconds: float) -> None:
        raise KeyboardInterrupt

    result = run_team(
        9999,
        no_workers=True,
        popen=popen,
        sleep=interrupting_sleep,
    )
    assert result == 0
    assert all(proc.poll() is not None for proc in launched)


def test_shutdown_escalates_to_kill_on_an_unexpected_terminate_error() -> None:
    """An OS error from terminate is not swallowed silently — the child is killed."""

    class ExplodingProc:
        def __init__(self) -> None:
            self.killed = False
            self.waits = 0

        def poll(self) -> None:
            return None

        def terminate(self) -> None:
            msg = "terminate failed"
            raise RuntimeError(msg)

        def kill(self) -> None:
            self.killed = True

        def wait(self, timeout: float) -> int:
            self.waits += 1
            return 0

    exploding = ExplodingProc()
    _shutdown([("x", exploding)], timeout_seconds=0.05)  # type: ignore[list-item]
    assert exploding.killed
    assert exploding.waits == 1
