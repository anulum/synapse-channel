# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the unified command-line entry point

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import pytest

from synapse_channel import cli
from synapse_channel.client.agent import DEFAULT_HUB_URI
from synapse_channel.client.llm_worker import DEFAULT_OLLAMA_BASE_URL
from synapse_channel.core.hub import SynapseHub

# --- parser ------------------------------------------------------------------


def test_parser_hub_defaults() -> None:
    args = cli.build_parser().parse_args(["hub"])
    assert args.host == "localhost"
    assert args.db is None
    assert args.func is cli._cmd_hub


def test_parser_worker_custom() -> None:
    args = cli.build_parser().parse_args(
        ["worker", "--name", "REASON", "--provider", "rule", "--min-reply-interval", "1.5"]
    )
    assert args.name == "REASON"
    assert args.provider == "rule"
    assert args.min_reply_interval == 1.5


# --- main dispatch -----------------------------------------------------------


def test_run_executes_coroutine() -> None:
    marker: list[bool] = []

    async def noop() -> None:
        marker.append(True)

    cli._run(noop())
    assert marker == [True]


def test_main_without_command_prints_help() -> None:
    assert cli.main([]) == 1


def test_main_version_exits(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "update_notice", lambda: None)  # no network in tests
    with pytest.raises(SystemExit):
        cli.main(["--version"])
    assert "synapse-channel" in capsys.readouterr().out


def test_main_version_prints_update_notice(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "update_notice", lambda: "  → 9.9.9 is available")
    with pytest.raises(SystemExit):
        cli.main(["--version"])
    captured = capsys.readouterr()
    assert "synapse-channel" in captured.out
    assert "9.9.9 is available" in captured.err  # the notice goes to stderr


def test_main_routes_to_team(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "run_team", lambda **kwargs: 9)
    assert cli.main(["team", "--no-workers"]) == 9


def test_main_routes_to_hub(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_run", lambda coro: coro.close())
    assert cli.main(["hub", "--port", "9000"]) == 0


# --- hub / worker handlers ---------------------------------------------------


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
        "token": None,
        "metrics": False,
        "auth_timeout": 10.0,
        "metrics_token": None,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


def test_cmd_hub_runs_and_handles_interrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_run", lambda coro: coro.close())
    ns = _hub_ns()
    assert cli._cmd_hub(ns) == 0

    def interrupt(coro: Any) -> None:
        coro.close()
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "_run", interrupt)
    assert cli._cmd_hub(ns) == 0


def test_cmd_hub_with_db_opens_and_closes_event_store(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(cli, "_run", lambda coro: coro.close())
    db = tmp_path / "events.db"
    assert cli._cmd_hub(_hub_ns(db=str(db))) == 0
    # The persistent store was created (and closed) for the run.
    assert db.exists()


def test_cmd_hub_with_rate_limit_builds_limiter(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_run(coro: Any) -> None:
        coro.close()

    monkeypatch.setattr(cli, "_run", fake_run)

    def spy_hub(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    monkeypatch.setattr("synapse_channel.cli.SynapseHub", spy_hub)
    assert cli._cmd_hub(_hub_ns(rate=5.0, burst=10.0)) == 0
    assert captured["rate_limiter"] is not None


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
    monkeypatch.setattr(cli, "_run", lambda coro: coro.close())
    assert cli._cmd_worker(_worker_ns()) == 0

    def interrupt(coro: Any) -> None:
        coro.close()
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "_run", interrupt)
    assert cli._cmd_worker(_worker_ns()) == 0


def test_cmd_team_returns_runner_code(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "run_team", lambda **kwargs: 4)
    ns = argparse.Namespace(
        port=8876, no_workers=False, fast_model=None, reason_model=None, prefix=""
    )
    assert cli._cmd_team(ns) == 4


def test_cmd_worker_applies_name_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, str] = {}

    class _StubWorker:
        def __init__(self, *, name: str, **_: Any) -> None:
            captured["name"] = name

        async def run(self) -> None:
            return None

    monkeypatch.setattr(cli, "SynapseLLMWorker", _StubWorker)
    monkeypatch.setattr(cli, "_run", lambda coro: coro.close())
    assert cli._cmd_worker(_worker_ns(prefix="remanentia/", name="FAST")) == 0
    assert captured["name"] == "remanentia/FAST"


def test_cmd_team_threads_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(cli, "run_team", lambda **kwargs: captured.update(kwargs) or 0)
    ns = argparse.Namespace(
        port=8876, no_workers=False, fast_model=None, reason_model=None, prefix="proj/"
    )
    assert cli._cmd_team(ns) == 0
    assert captured["prefix"] == "proj/"


# --- hub relay-log wiring ----------------------------------------------------


def test_cmd_hub_wires_relay_log(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(cli, "_run", lambda coro: coro.close())

    def spy_hub(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    monkeypatch.setattr("synapse_channel.cli.SynapseHub", spy_hub)
    log = tmp_path / "relay.ndjson"
    assert cli._cmd_hub(_hub_ns(relay_log=str(log), relay_max_lines=42)) == 0
    assert captured["relay_log"] == str(log)
    assert captured["relay_max_lines"] == 42


# --- connect authentication threading ----------------------------------------


def test_parser_token_options() -> None:
    parser = cli.build_parser()
    assert parser.parse_args(["hub", "--token", "h"]).token == "h"
    assert parser.parse_args(["worker", "--token", "w"]).token == "w"
    assert parser.parse_args(["send", "msg", "--token", "s"]).token == "s"
    assert parser.parse_args(["listen", "--token", "l"]).token == "l"
    assert parser.parse_args(["board", "--token", "b"]).token == "b"


def test_cmd_hub_with_token_builds_authenticator(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(cli, "_run", lambda coro: coro.close())

    def spy_hub(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    monkeypatch.setattr("synapse_channel.cli.SynapseHub", spy_hub)
    assert cli._cmd_hub(_hub_ns(token="s3cret")) == 0
    assert captured["authenticator"] is not None


def test_cmd_hub_without_token_has_no_authenticator(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(cli, "_run", lambda coro: coro.close())

    def spy_hub(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    monkeypatch.setattr("synapse_channel.cli.SynapseHub", spy_hub)
    assert cli._cmd_hub(_hub_ns()) == 0
    assert captured["authenticator"] is None


def test_cmd_worker_threads_token(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(cli, "_run", lambda coro: coro.close())

    class FakeWorker:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

        async def run(self) -> None:
            return None

    monkeypatch.setattr("synapse_channel.cli.SynapseLLMWorker", FakeWorker)
    assert cli._cmd_worker(_worker_ns(token="w0rk")) == 0
    assert captured["token"] == "w0rk"


# --- supervisor --------------------------------------------------------------


def test_parser_supervisor() -> None:
    args = cli.build_parser().parse_args(["supervisor", "--idle-seconds", "60", "--interval", "5"])
    assert args.idle_seconds == 60.0
    assert args.interval == 5.0
    assert args.func is cli._cmd_supervisor


def test_cmd_supervisor_runs_and_handles_interrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_run", lambda coro: coro.close())
    ns = argparse.Namespace(
        uri="ws://h", name="SUPERVISOR", idle_seconds=300.0, interval=30.0, token=None
    )
    assert cli._cmd_supervisor(ns) == 0

    def interrupt(coro: Any) -> None:
        coro.close()
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "_run", interrupt)
    assert cli._cmd_supervisor(ns) == 0


# --- worker task classes and tiers -------------------------------------------


def test_parser_worker_task_class() -> None:
    worker = cli.build_parser().parse_args(
        ["worker", "--task-class", "reason", "--task-class", "heavy"]
    )
    assert worker.task_class == ["reason", "heavy"]


def test_cmd_worker_threads_task_classes(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(cli, "_run", lambda coro: coro.close())

    class FakeWorker:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

        async def run(self) -> None:
            return None

    monkeypatch.setattr("synapse_channel.cli.SynapseLLMWorker", FakeWorker)
    assert cli._cmd_worker(_worker_ns(task_class=["reason"], heavy_model="big")) == 0
    assert captured["task_classes"] == ("reason",)
    assert captured["heavy_model"] == "big"
    # Without --task-class the worker advertises the default class.
    captured.clear()
    assert cli._cmd_worker(_worker_ns()) == 0
    assert captured["task_classes"] == ("chat",)


def test_parser_worker_tiered_provider_and_heavy_model() -> None:
    args = cli.build_parser().parse_args(["worker", "--provider", "tiered", "--heavy-model", "big"])
    assert args.provider == "tiered"
    assert args.heavy_model == "big"


# --- relay parser flags ------------------------------------------------------


def test_parser_relay_for_flag() -> None:
    relay = cli.build_parser().parse_args(["relay", "feed.ndjson", "--for", "B"])
    assert relay.for_name == "B"


def test_parser_relay_project() -> None:
    args = cli.build_parser().parse_args(["relay", "feed.ndjson", "--project", "quantum"])
    assert args.project == "quantum"


# --- A1: token via env / file -----------------------------------------------


def test_resolve_token_prefers_cli() -> None:
    assert cli._resolve_token(argparse.Namespace(token="cli", token_file=None)) == "cli"


def test_resolve_token_from_file(tmp_path: Path) -> None:
    f = tmp_path / "tok"
    f.write_text("file-tok\n", encoding="utf-8")
    assert cli._resolve_token(argparse.Namespace(token=None, token_file=str(f))) == "file-tok"


def test_resolve_token_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYNAPSE_TOKEN", "env-tok")
    assert cli._resolve_token(argparse.Namespace(token=None, token_file=None)) == "env-tok"


def test_resolve_token_precedence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    f = tmp_path / "tok"
    f.write_text("file-tok", encoding="utf-8")
    monkeypatch.setenv("SYNAPSE_TOKEN", "env-tok")
    assert cli._resolve_token(argparse.Namespace(token="cli", token_file=str(f))) == "cli"
    assert cli._resolve_token(argparse.Namespace(token=None, token_file=str(f))) == "file-tok"


def test_resolve_token_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SYNAPSE_TOKEN", raising=False)
    assert cli._resolve_token(argparse.Namespace(token=None, token_file=None)) is None


def test_resolve_token_missing_file(tmp_path: Path) -> None:
    ns = argparse.Namespace(token=None, token_file=str(tmp_path / "nope"))
    with pytest.raises(FileNotFoundError):
        cli._resolve_token(ns)


def test_resolve_token_no_token_file_attr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYNAPSE_TOKEN", "env-tok")
    assert cli._resolve_token(argparse.Namespace(token=None)) == "env-tok"


def test_parser_adds_token_file_to_token_commands() -> None:
    args = cli.build_parser().parse_args(["send", "hi", "--token-file", "/x"])
    assert args.token_file == "/x"


def test_main_resolves_token_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYNAPSE_TOKEN", "env-tok")
    captured: dict[str, Any] = {}

    def fake(args: argparse.Namespace) -> int:
        captured["token"] = args.token
        return 0

    monkeypatch.setattr(cli, "_cmd_worker", fake)
    assert cli.main(["worker"]) == 0
    assert captured["token"] == "env-tok"


def test_parser_hub_caps() -> None:
    args = cli.build_parser().parse_args(["hub", "--max-clients", "8", "--max-msg-kb", "32"])
    assert args.max_clients == 8
    assert args.max_msg_kb == 32
