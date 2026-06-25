# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the long-running process commands (hub/worker/team/supervisor)

from __future__ import annotations

from collections.abc import Coroutine
from pathlib import Path
from typing import Any

import pytest

from cli_processes_helpers import _hub_ns
from synapse_channel import cli_processes
from synapse_channel.core.hub import (
    InsecureBindError,
    SynapseHub,
)
from synapse_channel.core.ratelimit import RateLimiter


def _close_runner(coro: Coroutine[Any, Any, None]) -> None:
    coro.close()


def test_cmd_hub_runs_and_handles_interrupt() -> None:
    ns = _hub_ns()
    assert cli_processes._cmd_hub(ns, runner=_close_runner) == 0

    def interrupt(coro: Coroutine[Any, Any, None]) -> None:
        coro.close()
        raise KeyboardInterrupt

    assert cli_processes._cmd_hub(ns, runner=interrupt) == 0


def test_cmd_hub_refuses_insecure_bind(capsys: pytest.CaptureFixture[str]) -> None:
    def refuse(coro: Coroutine[Any, Any, None]) -> None:
        coro.close()
        raise InsecureBindError("Refusing to bind: Synapse Hub bound to ... no token.")

    assert cli_processes._cmd_hub(_hub_ns(host="0.0.0.0"), runner=refuse) == 2
    assert "Refusing to bind" in capsys.readouterr().err


def test_cmd_hub_threads_insecure_off_loopback() -> None:
    built: dict[str, Any] = {}

    def build_hub(**kwargs: Any) -> SynapseHub:
        built.update(kwargs)
        return SynapseHub(**kwargs)

    assert (
        cli_processes._cmd_hub(
            _hub_ns(insecure_off_loopback=True), runner=_close_runner, hub_factory=build_hub
        )
        == 0
    )
    assert built["insecure_off_loopback"] is True


def test_cmd_hub_with_db_opens_and_closes_event_store(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    assert cli_processes._cmd_hub(_hub_ns(db=str(db)), runner=_close_runner) == 0
    # The persistent store was created (and closed) for the run.
    assert db.exists()


def test_cmd_hub_with_rate_limit_builds_limiter() -> None:
    captured: dict[str, Any] = {}

    def build_hub(**kwargs: Any) -> SynapseHub:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    assert (
        cli_processes._cmd_hub(
            _hub_ns(rate=5.0, burst=10.0), runner=_close_runner, hub_factory=build_hub
        )
        == 0
    )
    assert captured["rate_limiter"] is not None


def test_cmd_hub_wires_relay_log(tmp_path: Path) -> None:
    captured: dict[str, Any] = {}

    def build_hub(**kwargs: Any) -> SynapseHub:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    log = tmp_path / "relay.ndjson"
    assert (
        cli_processes._cmd_hub(
            _hub_ns(relay_log=str(log), relay_max_lines=42),
            runner=_close_runner,
            hub_factory=build_hub,
        )
        == 0
    )
    assert captured["relay_log"] == str(log)
    assert captured["relay_max_lines"] == 42


def test_cmd_hub_threads_per_agent_quotas() -> None:
    captured: dict[str, Any] = {}

    def build_hub(**kwargs: Any) -> SynapseHub:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    assert (
        cli_processes._cmd_hub(
            _hub_ns(max_claims_per_agent=7, max_offers_per_agent=3, max_paths_per_claim=9),
            runner=_close_runner,
            hub_factory=build_hub,
        )
        == 0
    )
    assert captured["max_claims_per_agent"] == 7
    assert captured["max_offers_per_agent"] == 3
    assert captured["max_paths_per_claim"] == 9


def test_cmd_hub_threads_metrics_query_token_ok() -> None:
    captured: dict[str, Any] = {}

    def build_hub(**kwargs: Any) -> SynapseHub:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    assert (
        cli_processes._cmd_hub(
            _hub_ns(metrics_query_token_ok=True), runner=_close_runner, hub_factory=build_hub
        )
        == 0
    )
    assert captured["metrics_query_token_ok"] is True


def test_cmd_hub_threads_max_unauth_clients() -> None:
    captured: dict[str, Any] = {}

    def build_hub(**kwargs: Any) -> SynapseHub:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    assert (
        cli_processes._cmd_hub(
            _hub_ns(max_unauth_clients=8), runner=_close_runner, hub_factory=build_hub
        )
        == 0
    )
    assert captured["max_unauth_clients"] == 8


def test_cmd_hub_builds_host_rate_limiter_when_enabled() -> None:
    captured: dict[str, Any] = {}

    def build_hub(**kwargs: Any) -> SynapseHub:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    assert (
        cli_processes._cmd_hub(
            _hub_ns(host_rate=5.0, host_burst=12.0),
            runner=_close_runner,
            hub_factory=build_hub,
        )
        == 0
    )
    assert isinstance(captured["host_rate_limiter"], RateLimiter)


def test_cmd_hub_host_rate_limiter_off_by_default() -> None:
    captured: dict[str, Any] = {}

    def build_hub(**kwargs: Any) -> SynapseHub:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    assert cli_processes._cmd_hub(_hub_ns(), runner=_close_runner, hub_factory=build_hub) == 0
    assert captured["host_rate_limiter"] is None


def test_cmd_hub_threads_compact_hint_threshold() -> None:
    captured: dict[str, Any] = {}

    def build_hub(**kwargs: Any) -> SynapseHub:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    assert (
        cli_processes._cmd_hub(
            _hub_ns(compact_hint_threshold=42), runner=_close_runner, hub_factory=build_hub
        )
        == 0
    )
    assert captured["compact_hint_threshold"] == 42


def test_cmd_hub_threads_takeover_cooldown() -> None:
    captured: dict[str, Any] = {}

    def build_hub(**kwargs: Any) -> SynapseHub:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    assert (
        cli_processes._cmd_hub(
            _hub_ns(takeover_cooldown=5.5), runner=_close_runner, hub_factory=build_hub
        )
        == 0
    )
    assert captured["takeover_cooldown"] == 5.5


def test_cmd_hub_configures_logging() -> None:
    captured: dict[str, Any] = {}
    assert (
        cli_processes._cmd_hub(
            _hub_ns(log_format="json", log_level="DEBUG"),
            runner=_close_runner,
            logging_configurator=lambda **kw: captured.update(kw),
        )
        == 0
    )
    assert captured == {"log_format": "json", "level": "DEBUG"}


def test_cmd_hub_with_token_builds_authenticator() -> None:
    captured: dict[str, Any] = {}

    def build_hub(**kwargs: Any) -> SynapseHub:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    assert (
        cli_processes._cmd_hub(_hub_ns(token="s3cret"), runner=_close_runner, hub_factory=build_hub)
        == 0
    )
    assert captured["authenticator"] is not None


def test_cmd_hub_without_token_has_no_authenticator() -> None:
    captured: dict[str, Any] = {}

    def build_hub(**kwargs: Any) -> SynapseHub:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    assert cli_processes._cmd_hub(_hub_ns(), runner=_close_runner, hub_factory=build_hub) == 0
    assert captured["authenticator"] is None
