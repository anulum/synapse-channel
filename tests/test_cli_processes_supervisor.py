# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — dedicated tests for the supervisor process CLI command

from __future__ import annotations

import argparse
import inspect
from collections.abc import Coroutine
from typing import Any

import pytest

from synapse_channel import cli_processes_supervisor
from synapse_channel.cli_processes_runtime import _run


class _FakeSupervisor:
    """Records the constructor kwargs and yields a harmless coroutine to run."""

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs

    def run(self) -> Coroutine[Any, Any, None]:
        async def _noop() -> None:
            return None

        return _noop()


def _closing_runner(calls: list[Coroutine[Any, Any, None]]) -> Any:
    """Return a runner that records and closes the coroutine without awaiting it."""

    def _runner(coro: Coroutine[Any, Any, None]) -> None:
        calls.append(coro)
        coro.close()

    return _runner


def _full_namespace() -> argparse.Namespace:
    return argparse.Namespace(
        name="SUP",
        uri="ws://hub.test:8876",
        idle_seconds=12.0,
        predictive_stall=False,
        history_multiplier=2.0,
        min_history_samples=7,
        min_predictive_idle_seconds=90.0,
        interval=3.0,
        token="tok",
        ready_timeout=4.0,
    )


def _minimal_namespace() -> argparse.Namespace:
    # Deliberately omits the predictive-stall knobs so the getattr defaults apply.
    return argparse.Namespace(
        name="SUP",
        uri="ws://hub.test:8876",
        idle_seconds=12.0,
        interval=3.0,
        token=None,
        ready_timeout=4.0,
    )


def test_builds_supervisor_from_full_args_and_returns_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    built: list[_FakeSupervisor] = []

    def _factory(**kwargs: Any) -> _FakeSupervisor:
        supervisor = _FakeSupervisor(**kwargs)
        built.append(supervisor)
        return supervisor

    monkeypatch.setattr(cli_processes_supervisor, "SupervisorWorker", _factory)
    calls: list[Coroutine[Any, Any, None]] = []
    code = cli_processes_supervisor._cmd_supervisor(
        _full_namespace(), runner=_closing_runner(calls)
    )
    assert code == 0
    assert len(calls) == 1
    kwargs = built[0].kwargs
    assert kwargs["name"] == "SUP"
    assert kwargs["uri"] == "ws://hub.test:8876"
    assert kwargs["idle_seconds"] == pytest.approx(12.0)
    assert kwargs["predictive_stall"] is False
    assert kwargs["history_multiplier"] == pytest.approx(2.0)
    assert kwargs["min_history_samples"] == 7
    assert kwargs["min_predictive_idle_seconds"] == pytest.approx(90.0)
    assert kwargs["interval"] == pytest.approx(3.0)
    assert kwargs["token"] == "tok"
    assert kwargs["ready_timeout"] == pytest.approx(4.0)


def test_missing_predictive_knobs_fall_back_to_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    built: list[_FakeSupervisor] = []

    def _factory(**kwargs: Any) -> _FakeSupervisor:
        supervisor = _FakeSupervisor(**kwargs)
        built.append(supervisor)
        return supervisor

    monkeypatch.setattr(cli_processes_supervisor, "SupervisorWorker", _factory)
    calls: list[Coroutine[Any, Any, None]] = []
    code = cli_processes_supervisor._cmd_supervisor(
        _minimal_namespace(), runner=_closing_runner(calls)
    )
    assert code == 0
    kwargs = built[0].kwargs
    assert kwargs["predictive_stall"] is True
    assert kwargs["history_multiplier"] == pytest.approx(3.0)
    assert kwargs["min_history_samples"] == 4
    assert kwargs["min_predictive_idle_seconds"] == pytest.approx(60.0)


def test_keyboard_interrupt_prints_stopped_notice(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        cli_processes_supervisor,
        "SupervisorWorker",
        lambda **kwargs: _FakeSupervisor(**kwargs),
    )

    def _interrupting_runner(coro: Coroutine[Any, Any, None]) -> None:
        coro.close()
        raise KeyboardInterrupt

    code = cli_processes_supervisor._cmd_supervisor(_full_namespace(), runner=_interrupting_runner)
    assert code == 0
    assert "[SUP] supervisor stopped by user." in capsys.readouterr().out


def test_default_runner_is_the_shared_run_helper() -> None:
    default = (
        inspect.signature(cli_processes_supervisor._cmd_supervisor).parameters["runner"].default
    )
    assert default is _run
