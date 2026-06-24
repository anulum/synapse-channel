# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the long-running process commands (hub/worker/team/supervisor)

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import pytest

from synapse_channel import cli, cli_processes
from synapse_channel.client.agent import DEFAULT_HUB_URI
from synapse_channel.client.llm_worker import DEFAULT_OLLAMA_BASE_URL
from synapse_channel.core.hub import SynapseHub

# --- parser ------------------------------------------------------------------


def test_parser_hub_defaults() -> None:
    args = cli.build_parser().parse_args(["hub"])
    assert args.host == "localhost"
    assert args.db is None
    assert args.func is cli_processes._cmd_hub


def test_parser_worker_custom() -> None:
    args = cli.build_parser().parse_args(
        ["worker", "--name", "REASON", "--provider", "rule", "--min-reply-interval", "1.5"]
    )
    assert args.name == "REASON"
    assert args.provider == "rule"
    assert args.min_reply_interval == 1.5


def test_parser_supervisor() -> None:
    args = cli.build_parser().parse_args(["supervisor", "--idle-seconds", "60", "--interval", "5"])
    assert args.idle_seconds == 60.0
    assert args.interval == 5.0
    assert args.func is cli_processes._cmd_supervisor


def test_parser_worker_task_class() -> None:
    worker = cli.build_parser().parse_args(
        ["worker", "--task-class", "reason", "--task-class", "heavy"]
    )
    assert worker.task_class == ["reason", "heavy"]


def test_parser_worker_tiered_provider_and_heavy_model() -> None:
    args = cli.build_parser().parse_args(["worker", "--provider", "tiered", "--heavy-model", "big"])
    assert args.provider == "tiered"
    assert args.heavy_model == "big"


def test_parser_hub_caps() -> None:
    args = cli.build_parser().parse_args(["hub", "--max-clients", "8", "--max-msg-kb", "32"])
    assert args.max_clients == 8
    assert args.max_msg_kb == 32


def test_parser_hub_per_agent_quotas() -> None:
    args = cli.build_parser().parse_args(
        ["hub", "--max-claims-per-agent", "10", "--max-offers-per-agent", "5"]
    )
    assert args.max_claims_per_agent == 10
    assert args.max_offers_per_agent == 5


def test_parser_hub_metrics_query_token_ok() -> None:
    assert cli.build_parser().parse_args(["hub"]).metrics_query_token_ok is False
    opted = cli.build_parser().parse_args(["hub", "--metrics-query-token-ok"])
    assert opted.metrics_query_token_ok is True


# --- main dispatch through the process handlers ------------------------------


def test_run_executes_coroutine() -> None:
    marker: list[bool] = []

    async def noop() -> None:
        marker.append(True)

    cli_processes._run(noop())
    assert marker == [True]


def test_main_routes_to_team(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_processes, "run_team", lambda **kwargs: 9)
    assert cli.main(["team", "--no-workers"]) == 9


def test_main_routes_to_hub(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_processes, "_run", lambda coro: coro.close())
    assert cli.main(["hub", "--port", "9000"]) == 0


def test_main_resolves_token_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYNAPSE_TOKEN", "env-tok")
    captured: dict[str, Any] = {}

    def fake(args: argparse.Namespace) -> int:
        captured["token"] = args.token
        return 0

    monkeypatch.setattr(cli_processes, "_cmd_worker", fake)
    assert cli.main(["worker"]) == 0
    assert captured["token"] == "env-tok"


# --- hub handler -------------------------------------------------------------


def _hub_ns(**overrides: Any) -> argparse.Namespace:
    base: dict[str, Any] = {
        "host": "localhost",
        "port": 8876,
        "db": None,
        "rate": 0.0,
        "burst": 20.0,
        "max_history": 10000,
        "relay_log": None,
        "relay_max_lines": 5000,
        "max_clients": 64,
        "max_msg_kb": 1024,
        "max_claims_per_agent": 128,
        "max_offers_per_agent": 64,
        "token": None,
        "metrics": False,
        "auth_timeout": 10.0,
        "metrics_token": None,
        "metrics_query_token_ok": False,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


def test_cmd_hub_runs_and_handles_interrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_processes, "_run", lambda coro: coro.close())
    ns = _hub_ns()
    assert cli_processes._cmd_hub(ns) == 0

    def interrupt(coro: Any) -> None:
        coro.close()
        raise KeyboardInterrupt

    monkeypatch.setattr(cli_processes, "_run", interrupt)
    assert cli_processes._cmd_hub(ns) == 0


def test_cmd_hub_with_db_opens_and_closes_event_store(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(cli_processes, "_run", lambda coro: coro.close())
    db = tmp_path / "events.db"
    assert cli_processes._cmd_hub(_hub_ns(db=str(db))) == 0
    # The persistent store was created (and closed) for the run.
    assert db.exists()


def test_cmd_hub_with_rate_limit_builds_limiter(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_run(coro: Any) -> None:
        coro.close()

    monkeypatch.setattr(cli_processes, "_run", fake_run)

    def spy_hub(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    monkeypatch.setattr("synapse_channel.cli_processes.SynapseHub", spy_hub)
    assert cli_processes._cmd_hub(_hub_ns(rate=5.0, burst=10.0)) == 0
    assert captured["rate_limiter"] is not None


def test_cmd_hub_wires_relay_log(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(cli_processes, "_run", lambda coro: coro.close())

    def spy_hub(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    monkeypatch.setattr("synapse_channel.cli_processes.SynapseHub", spy_hub)
    log = tmp_path / "relay.ndjson"
    assert cli_processes._cmd_hub(_hub_ns(relay_log=str(log), relay_max_lines=42)) == 0
    assert captured["relay_log"] == str(log)
    assert captured["relay_max_lines"] == 42


def test_cmd_hub_threads_per_agent_quotas(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(cli_processes, "_run", lambda coro: coro.close())

    def spy_hub(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    monkeypatch.setattr("synapse_channel.cli_processes.SynapseHub", spy_hub)
    assert cli_processes._cmd_hub(_hub_ns(max_claims_per_agent=7, max_offers_per_agent=3)) == 0
    assert captured["max_claims_per_agent"] == 7
    assert captured["max_offers_per_agent"] == 3


def test_cmd_hub_threads_metrics_query_token_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(cli_processes, "_run", lambda coro: coro.close())

    def spy_hub(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    monkeypatch.setattr("synapse_channel.cli_processes.SynapseHub", spy_hub)
    assert cli_processes._cmd_hub(_hub_ns(metrics_query_token_ok=True)) == 0
    assert captured["metrics_query_token_ok"] is True


def test_cmd_hub_with_token_builds_authenticator(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(cli_processes, "_run", lambda coro: coro.close())

    def spy_hub(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    monkeypatch.setattr("synapse_channel.cli_processes.SynapseHub", spy_hub)
    assert cli_processes._cmd_hub(_hub_ns(token="s3cret")) == 0
    assert captured["authenticator"] is not None


def test_cmd_hub_without_token_has_no_authenticator(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(cli_processes, "_run", lambda coro: coro.close())

    def spy_hub(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    monkeypatch.setattr("synapse_channel.cli_processes.SynapseHub", spy_hub)
    assert cli_processes._cmd_hub(_hub_ns()) == 0
    assert captured["authenticator"] is None


# --- worker handler ----------------------------------------------------------


def _worker_ns(**overrides: Any) -> argparse.Namespace:
    base: dict[str, Any] = {
        "name": "FAST",
        "prefix": "",
        "uri": DEFAULT_HUB_URI,
        "provider": "rule",
        "model": "llama3",
        "base_url": DEFAULT_OLLAMA_BASE_URL,
        "api_key_env": "OPENAI_API_KEY",
        "max_context": 8,
        "reply_target_mode": "all",
        "min_reply_interval": 0.7,
        "token": None,
        "task_class": None,
        "heavy_model": "",
    }
    base.update(overrides)
    return argparse.Namespace(**base)


def test_cmd_worker_runs_and_handles_interrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_processes, "_run", lambda coro: coro.close())
    assert cli_processes._cmd_worker(_worker_ns()) == 0

    def interrupt(coro: Any) -> None:
        coro.close()
        raise KeyboardInterrupt

    monkeypatch.setattr(cli_processes, "_run", interrupt)
    assert cli_processes._cmd_worker(_worker_ns()) == 0


def test_cmd_worker_applies_name_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, str] = {}

    class _StubWorker:
        def __init__(self, *, name: str, **_: Any) -> None:
            captured["name"] = name

        async def run(self) -> None:
            return None

    monkeypatch.setattr(cli_processes, "SynapseLLMWorker", _StubWorker)
    monkeypatch.setattr(cli_processes, "_run", lambda coro: coro.close())
    assert cli_processes._cmd_worker(_worker_ns(prefix="remanentia/", name="FAST")) == 0
    assert captured["name"] == "remanentia/FAST"


def test_cmd_worker_threads_token(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(cli_processes, "_run", lambda coro: coro.close())

    class FakeWorker:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

        async def run(self) -> None:
            return None

    monkeypatch.setattr("synapse_channel.cli_processes.SynapseLLMWorker", FakeWorker)
    assert cli_processes._cmd_worker(_worker_ns(token="w0rk")) == 0
    assert captured["token"] == "w0rk"


def test_cmd_worker_threads_task_classes(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(cli_processes, "_run", lambda coro: coro.close())

    class FakeWorker:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

        async def run(self) -> None:
            return None

    monkeypatch.setattr("synapse_channel.cli_processes.SynapseLLMWorker", FakeWorker)
    assert cli_processes._cmd_worker(_worker_ns(task_class=["reason"], heavy_model="big")) == 0
    assert captured["task_classes"] == ("reason",)
    assert captured["heavy_model"] == "big"
    # Without --task-class the worker advertises the default class.
    captured.clear()
    assert cli_processes._cmd_worker(_worker_ns()) == 0
    assert captured["task_classes"] == ("chat",)


def test_egress_warning_openai_flags_context_and_key() -> None:
    msg = cli_processes._egress_warning("openai", "https://api.openai.com/v1")
    assert msg is not None
    assert "SENDS" in msg and "API key" in msg
    assert "https://api.openai.com/v1" in msg


def test_egress_warning_openai_without_base_url_names_the_endpoint() -> None:
    assert "the configured endpoint" in (cli_processes._egress_warning("openai", "") or "")


def test_egress_warning_local_ollama_is_silent() -> None:
    assert cli_processes._egress_warning("ollama", DEFAULT_OLLAMA_BASE_URL) is None
    assert cli_processes._egress_warning("ollama", "http://127.0.0.1:11434") is None


def test_egress_warning_remote_ollama_warns_without_key() -> None:
    msg = cli_processes._egress_warning("ollama", "http://10.0.0.5:11434")
    assert msg is not None
    assert "SENDS" in msg and "API key" not in msg


def test_egress_warning_rule_backend_is_always_silent() -> None:
    # The rule backend never touches the network, even with a remote base_url set.
    assert cli_processes._egress_warning("rule", "http://10.0.0.5:11434") is None


def test_cmd_worker_prints_egress_warning_only_when_off_host(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli_processes, "_run", lambda coro: coro.close())

    class FakeWorker:
        def __init__(self, **_: Any) -> None:
            pass

        async def run(self) -> None:
            return None

    monkeypatch.setattr("synapse_channel.cli_processes.SynapseLLMWorker", FakeWorker)

    assert (
        cli_processes._cmd_worker(
            _worker_ns(provider="openai", base_url="https://api.openai.com/v1")
        )
        == 0
    )
    err = capsys.readouterr().err
    assert "WARNING" in err and "SENDS" in err

    # A local backend starts silently.
    assert cli_processes._cmd_worker(_worker_ns(provider="rule")) == 0
    assert "WARNING" not in capsys.readouterr().err


# --- team handler ------------------------------------------------------------


def test_cmd_team_returns_runner_code(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_processes, "run_team", lambda **kwargs: 4)
    ns = argparse.Namespace(
        port=8876, no_workers=False, fast_model=None, reason_model=None, prefix=""
    )
    assert cli_processes._cmd_team(ns) == 4


def test_cmd_team_threads_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(cli_processes, "run_team", lambda **kwargs: captured.update(kwargs) or 0)
    ns = argparse.Namespace(
        port=8876, no_workers=False, fast_model=None, reason_model=None, prefix="proj/"
    )
    assert cli_processes._cmd_team(ns) == 0
    assert captured["prefix"] == "proj/"


# --- supervisor handler ------------------------------------------------------


def test_cmd_supervisor_runs_and_handles_interrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_processes, "_run", lambda coro: coro.close())
    ns = argparse.Namespace(
        uri="ws://h", name="SUPERVISOR", idle_seconds=300.0, interval=30.0, token=None
    )
    assert cli_processes._cmd_supervisor(ns) == 0

    def interrupt(coro: Any) -> None:
        coro.close()
        raise KeyboardInterrupt

    monkeypatch.setattr(cli_processes, "_run", interrupt)
    assert cli_processes._cmd_supervisor(ns) == 0
