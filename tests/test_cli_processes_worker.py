# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the long-running process commands (hub/worker/team/supervisor)

from __future__ import annotations

from typing import Any

import pytest

from cli_processes_helpers import _worker_ns
from synapse_channel import cli_processes
from synapse_channel.client.llm_worker import DEFAULT_OLLAMA_BASE_URL, SynapseLLMWorker


def test_cmd_worker_configures_logging(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(cli_processes, "_run", lambda coro: coro.close())
    monkeypatch.setattr(cli_processes, "configure_logging", lambda **kw: captured.update(kw))
    assert cli_processes._cmd_worker(_worker_ns(log_format="json", log_level="ERROR")) == 0
    assert captured == {"log_format": "json", "level": "ERROR"}


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

    async def record_run(self: SynapseLLMWorker) -> None:
        captured["name"] = self.name

    monkeypatch.setattr(SynapseLLMWorker, "run", record_run)
    assert cli_processes._cmd_worker(_worker_ns(prefix="remanentia/", name="FAST")) == 0
    assert captured["name"] == "remanentia/FAST"


def test_cmd_worker_threads_token(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    async def record_run(self: SynapseLLMWorker) -> None:
        captured["token"] = self.agent.token

    monkeypatch.setattr(SynapseLLMWorker, "run", record_run)
    assert cli_processes._cmd_worker(_worker_ns(token="w0rk")) == 0
    assert captured["token"] == "w0rk"


def test_cmd_worker_threads_task_classes(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    async def record_run(self: SynapseLLMWorker) -> None:
        captured["task_classes"] = self.task_classes
        captured["heavy_model"] = self.heavy_model

    monkeypatch.setattr(SynapseLLMWorker, "run", record_run)
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
    async def no_op_run(self: SynapseLLMWorker) -> None:
        return None

    monkeypatch.setattr(SynapseLLMWorker, "run", no_op_run)
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
