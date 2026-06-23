# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the local team launcher

from __future__ import annotations

import json
from typing import Any

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


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


def _opener_for(models: list[str]) -> Any:
    def opener(url: str, timeout: float) -> _FakeResponse:
        return _FakeResponse({"models": [{"name": name} for name in models]})

    return opener


class FakeProc:
    def __init__(
        self,
        poll_results: list[int | None],
        *,
        pid: int = 1,
        terminate_exc: Exception | None = None,
    ) -> None:
        self._poll = list(poll_results)
        self.pid = pid
        self.terminated = False
        self.killed = False
        self.waited = False
        self._terminate_exc = terminate_exc

    def poll(self) -> int | None:
        return self._poll.pop(0) if len(self._poll) > 1 else self._poll[0]

    def terminate(self) -> None:
        self.terminated = True
        if self._terminate_exc is not None:
            raise self._terminate_exc

    def wait(self, timeout: float | None = None) -> None:
        self.waited = True

    def kill(self) -> None:
        self.killed = True


# --- detect_model ------------------------------------------------------------


def test_detect_model_match_with_tag_returns_full_name() -> None:
    got = detect_model(["gemma3:4b"], opener=_opener_for(["gemma3:4b", "llama3:latest"]))
    assert got == "gemma3:4b"


def test_detect_model_match_without_tag_returns_family() -> None:
    got = detect_model(["llama3"], opener=_opener_for(["llama3:latest"]))
    assert got == "llama3"


def test_detect_model_substring_match() -> None:
    got = detect_model(["gemma"], opener=_opener_for(["my-gemma:1b"]))
    assert got == "my-gemma"


def test_detect_model_falls_back_to_first_installed() -> None:
    got = detect_model(["absent"], opener=_opener_for(["foo:1b"]))
    assert got == "foo"


def test_detect_model_none_when_empty() -> None:
    assert detect_model(["x"], opener=_opener_for([])) is None


def test_detect_model_none_on_error() -> None:
    def boom(url: str, timeout: float) -> _FakeResponse:
        raise OSError("no server")

    assert detect_model(["x"], opener=boom) is None


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


def test_shutdown_terminates_running_kills_on_error_and_skips_exited() -> None:
    running = FakeProc([None])
    stubborn = FakeProc([None], terminate_exc=RuntimeError("won't stop"))
    already_done = FakeProc([0])
    _shutdown([("a", running), ("b", stubborn), ("c", already_done)])  # type: ignore[list-item]
    assert running.terminated and running.waited
    assert stubborn.killed
    assert already_done.terminated is False


# --- run_team ----------------------------------------------------------------


def _popen_factory(procs: list[FakeProc]) -> Any:
    iterator = iter(procs)

    def fake_popen(argv: list[str], **kwargs: object) -> FakeProc:
        return next(iterator)

    return fake_popen


def test_run_team_returns_exit_code_of_first_dead_child() -> None:
    hub = FakeProc([None])
    fast = FakeProc([3])
    result = run_team(
        9999,
        popen=_popen_factory([hub, fast]),
        sleep=lambda s: None,
        detect=lambda prefs: "m",
    )
    assert result == 3
    assert hub.terminated  # cleaned up in finally


def test_run_team_polls_again_while_children_alive() -> None:
    hub = FakeProc([None, None])
    fast = FakeProc([None, 5])  # alive on first pass, dead on the second
    result = run_team(
        9999,
        popen=_popen_factory([hub, fast]),
        sleep=lambda s: None,
        detect=lambda prefs: "m",
    )
    assert result == 5


def test_run_team_maps_zero_exit_to_one() -> None:
    hub = FakeProc([0])
    result = run_team(
        9999,
        no_workers=True,
        popen=_popen_factory([hub]),
        sleep=lambda s: None,
    )
    assert result == 1


def test_run_team_handles_keyboard_interrupt() -> None:
    hub = FakeProc([None])

    def interrupting_sleep(seconds: float) -> None:
        raise KeyboardInterrupt

    result = run_team(
        9999,
        no_workers=True,
        popen=_popen_factory([hub]),
        sleep=interrupting_sleep,
    )
    assert result == 0
    assert hub.terminated
