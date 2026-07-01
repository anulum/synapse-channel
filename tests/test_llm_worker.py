# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the on-channel model worker

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from http_server_helpers import LocalHttpResponder
from hub_e2e_helpers import AgentHandle, _free_port, close_agents, connect_agent, running_hub
from synapse_channel.client.chat_backends import OpenAIChatClient, RuleBasedClient
from synapse_channel.client.llm_worker import (
    DEFAULT_OLLAMA_BASE_URL,
    OPENAI_DEFAULT_BASE_URL,
    SYSTEM_PROMPT,
    SynapseLLMWorker,
    is_service_message,
)
from synapse_channel.core.hub import SynapseHub


@contextmanager
def _env_var(name: str, value: str | None) -> Iterator[None]:
    previous = os.environ.get(name)
    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = previous


def _worker(**kwargs: object) -> SynapseLLMWorker:
    params: dict[str, object] = {"name": "ALPHA", "provider": "rule"}
    params.update(kwargs)
    return SynapseLLMWorker(**params)  # type: ignore[arg-type]


async def _start_worker_agent(worker: SynapseLLMWorker) -> asyncio.Task[None]:
    task = asyncio.create_task(worker.agent.connect())
    if not await worker.agent.wait_until_ready(3.0):
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        raise TimeoutError("worker agent did not connect")
    return task


async def _stop_worker_agent(worker: SynapseLLMWorker, task: asyncio.Task[None]) -> None:
    worker.agent.running = False
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def _worker_and_observer(
    uri: str, **kwargs: object
) -> tuple[SynapseLLMWorker, asyncio.Task[None], AgentHandle]:
    observer = await connect_agent("OBSERVER", uri)
    kwargs.setdefault("min_reply_interval", 0.0)
    worker = _worker(uri=uri, **kwargs)
    task = await _start_worker_agent(worker)
    return worker, task, observer


async def _wait_for_worker_chat(observer: AgentHandle, sender: str = "ALPHA") -> dict[str, object]:
    return await observer.recorder.wait_for(
        lambda message: message.get("type") == "chat" and message.get("sender") == sender
    )


# --- is_service_message ------------------------------------------------------


@pytest.mark.parametrize(
    ("sender", "payload", "msg_type", "expected"),
    [
        ("SynapseHub", "x", "chat", True),
        ("NODE_LITE", "x", "chat", True),
        ("NODE_CORE", "x", "chat", True),
        ("A", "x", "presence_update", True),
        ("A", "[ACK] seen", "chat", True),
        ("A", "[ROUTE] go", "chat", True),
        ("A", "  [MAIN] hi", "chat", True),
        ("A", "real message", "chat", False),
    ],
)
def test_is_service_message(sender: str, payload: str, msg_type: str, expected: bool) -> None:
    assert is_service_message(sender, payload, msg_type) is expected


# --- _build_client -----------------------------------------------------------


def test_build_client_rule() -> None:
    worker = _worker(provider="rule")
    assert isinstance(worker.client, RuleBasedClient)


def test_build_client_openai_keeps_base() -> None:
    with _env_var("OPENAI_API_KEY", None):
        worker = _worker(provider="openai", base_url=OPENAI_DEFAULT_BASE_URL)
    assert isinstance(worker.client, OpenAIChatClient)
    assert worker.client.base_url == OPENAI_DEFAULT_BASE_URL
    assert worker.client.api_key == "ollama"  # falls back when env is empty


def test_build_client_ollama_redirects_openai_default() -> None:
    worker = _worker(provider="ollama", base_url=OPENAI_DEFAULT_BASE_URL)
    assert isinstance(worker.client, OpenAIChatClient)
    assert worker.client.base_url == DEFAULT_OLLAMA_BASE_URL


def test_build_client_ollama_keeps_custom_base() -> None:
    worker = _worker(provider="ollama", base_url="http://gpu:11434/v1")
    assert isinstance(worker.client, OpenAIChatClient)
    assert worker.client.base_url == "http://gpu:11434/v1"


def test_build_client_uses_api_key_from_env() -> None:
    with _env_var("MY_KEY", "secret-token"):
        worker = _worker(provider="openai", api_key_env="MY_KEY")
    assert isinstance(worker.client, OpenAIChatClient)
    assert worker.client.api_key == "secret-token"


def test_build_client_rejects_unknown_provider() -> None:
    with pytest.raises(RuntimeError, match="Unsupported provider"):
        _worker(provider="telepathy")


def test_build_client_tiered_returns_router() -> None:
    from synapse_channel.client.routing import TieredChatClient

    worker = _worker(provider="tiered", model="small", heavy_model="big")
    assert isinstance(worker.client, TieredChatClient)


def test_heavy_model_defaults_to_model() -> None:
    worker = _worker(provider="rule", model="m")
    assert worker.heavy_model == "m"


# --- _should_reply -----------------------------------------------------------


def test_should_reply_rules() -> None:
    worker = _worker(name="ALPHA")
    assert worker._should_reply(sender="ALPHA", payload="x", target="all") is False
    assert worker._should_reply(sender="B", payload="x", target="ALPHA") is True
    assert worker._should_reply(sender="B", payload="hey alpha?", target="all") is True
    assert worker._should_reply(sender="USER", payload="x", target="all") is True
    assert worker._should_reply(sender="B", payload="x", target="all") is False


# --- on_message --------------------------------------------------------------


async def test_on_message_queues_addressed_message() -> None:
    worker = _worker(name="ALPHA")
    await worker.on_message(
        {"type": "chat", "sender": "USER", "payload": "status?", "target": "all"}
    )
    assert worker.inbox.qsize() == 1
    assert list(worker.context)[-1] == ("USER", "status?")


async def test_on_message_ignores_non_chat() -> None:
    worker = _worker()
    await worker.on_message({"type": "welcome", "sender": "SynapseHub"})
    assert worker.inbox.qsize() == 0


async def test_on_message_ignores_self_service_and_empty() -> None:
    worker = _worker(name="ALPHA")
    await worker.on_message({"type": "chat", "sender": "ALPHA", "payload": "mine"})
    await worker.on_message({"type": "chat", "sender": "SynapseHub", "payload": "sys"})
    await worker.on_message({"type": "chat", "sender": "USER", "payload": "   "})
    assert worker.inbox.qsize() == 0


async def test_on_message_records_context_without_reply() -> None:
    worker = _worker(name="ALPHA")
    # A peer message not addressed to ALPHA is remembered but not queued.
    await worker.on_message({"type": "chat", "sender": "B", "payload": "fyi", "target": "all"})
    assert worker.inbox.qsize() == 0
    assert list(worker.context)[-1] == ("B", "fyi")


# --- _build_user_prompt ------------------------------------------------------


def test_build_user_prompt_includes_transcript_and_latest() -> None:
    worker = _worker(name="ALPHA")
    worker.context.append(("B", "earlier"))
    prompt = worker._build_user_prompt("USER", "do it")
    assert "B: earlier" in prompt
    assert "Latest from USER: do it" in prompt


# --- _process_item -----------------------------------------------------------


async def test_process_item_sends_reply_to_room() -> None:
    payload = {"choices": [{"message": {"content": "  done   now  "}}]}
    with LocalHttpResponder(body=json.dumps(payload).encode("utf-8")) as server:
        async with running_hub(SynapseHub()) as (_hub, uri):
            worker, task, observer = await _worker_and_observer(
                uri,
                name="ALPHA",
                provider="openai",
                base_url=f"{server.url}/v1",
            )
            try:
                await worker._process_item({"sender": "USER", "payload": "go"})
                message = await _wait_for_worker_chat(observer)
            finally:
                await _stop_worker_agent(worker, task)
                await close_agents(observer)

    assert message["target"] == "all"
    assert message["payload"] == "done now"
    assert worker.last_reply_ts > 0
    request = server.requests[0]
    body = json.loads(request.body.decode("utf-8"))
    assert body["messages"][0]["content"] == SYSTEM_PROMPT


async def test_process_item_targets_sender_in_private_mode() -> None:
    async with running_hub(SynapseHub()) as (_hub, uri):
        worker, task, observer = await _worker_and_observer(
            uri,
            name="ALPHA",
            provider="rule",
            reply_target_mode="sender",
        )
        try:
            await worker._process_item({"sender": "USER", "payload": "go"})
            message = await _wait_for_worker_chat(observer)
        finally:
            await _stop_worker_agent(worker, task)
            await close_agents(observer)

    assert message["target"] == "USER"
    assert message["payload"] == "message received via Synapse. I am active on-channel."


async def test_process_item_reports_backend_error() -> None:
    with LocalHttpResponder(body=b"model down", status=500) as server:
        async with running_hub(SynapseHub()) as (_hub, uri):
            worker, task, observer = await _worker_and_observer(
                uri,
                name="ALPHA",
                provider="openai",
                base_url=f"{server.url}/v1",
            )
            try:
                await worker._process_item({"sender": "USER", "payload": "go"})
                message = await _wait_for_worker_chat(observer)
            finally:
                await _stop_worker_agent(worker, task)
                await close_agents(observer)

    assert message["target"] == "all"
    assert "model error -> chat backend HTTP 500" in str(message["payload"])


async def test_process_item_throttles() -> None:
    async with running_hub(SynapseHub()) as (_hub, uri):
        worker, task, observer = await _worker_and_observer(
            uri,
            name="ALPHA",
            provider="rule",
            min_reply_interval=0.05,
        )
        worker.last_reply_ts = time.time()
        started = time.monotonic()
        try:
            await worker._process_item({"sender": "USER", "payload": "go"})
            await _wait_for_worker_chat(observer)
        finally:
            await _stop_worker_agent(worker, task)
            await close_agents(observer)

    assert time.monotonic() - started >= 0.03


# --- _worker_loop ------------------------------------------------------------


async def test_worker_loop_processes_queued_message() -> None:
    async with running_hub(SynapseHub()) as (_hub, uri):
        worker, task, observer = await _worker_and_observer(uri, name="ALPHA", provider="rule")
        loop_task = asyncio.create_task(worker._worker_loop())
        try:
            await worker.inbox.put({"sender": "USER", "payload": "go"})
            message = await _wait_for_worker_chat(observer)
        finally:
            worker.agent.running = False
            loop_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await loop_task
            await _stop_worker_agent(worker, task)
            await close_agents(observer)

    assert message["payload"] == "message received via Synapse. I am active on-channel."


# --- run ---------------------------------------------------------------------


async def test_run_completes_when_connection_finishes() -> None:
    worker = _worker(name="ALPHA", uri=f"ws://127.0.0.1:{_free_port()}", ready_timeout=0.1)
    await worker.run()


async def test_run_warns_on_handshake_timeout(capsys: pytest.CaptureFixture[str]) -> None:
    worker = _worker(name="ALPHA", uri=f"ws://127.0.0.1:{_free_port()}", ready_timeout=0.1)
    await worker.run()
    assert "handshake timeout" in capsys.readouterr().out


async def test_run_reports_connection_error(capsys: pytest.CaptureFixture[str]) -> None:
    worker = _worker(name="ALPHA", uri="not-a-websocket-uri", ready_timeout=0.1)
    await worker.run()
    assert "worker stopped:" in capsys.readouterr().out


def test_worker_forwards_token_to_its_agent() -> None:
    worker = _worker(name="ALPHA", token="s3cret")
    assert worker.agent.token == "s3cret"


def test_worker_default_token_is_none() -> None:
    worker = _worker(name="ALPHA")
    assert worker.agent.token is None


async def test_run_advertises_capability_card_when_ready() -> None:
    async with running_hub(SynapseHub()) as (_hub, uri):
        observer = await connect_agent("OBSERVER", uri)
        worker = _worker(name="ALPHA", uri=uri, task_classes=("chat", "reason"))
        run_task = asyncio.create_task(worker.run())
        try:
            message = await observer.recorder.wait_for(
                lambda item: (
                    item.get("type") == "capability_advertised"
                    and item.get("card", {}).get("agent") == "ALPHA"
                )
            )
        finally:
            worker.agent.running = False
            run_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await run_task
            await close_agents(observer)

    assert message["card"]["task_classes"] == ["chat", "reason"]


async def test_run_skips_advertise_on_handshake_timeout(
    capsys: pytest.CaptureFixture[str],
) -> None:
    worker = _worker(name="ALPHA", uri=f"ws://127.0.0.1:{_free_port()}", ready_timeout=0.1)
    await worker.run()
    assert "handshake timeout" in capsys.readouterr().out


async def test_run_cancels_the_worker_loop_when_the_connection_ends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The surviving task is cancelled the moment its sibling completes."""
    worker = _worker(name="ALPHA", uri=f"ws://127.0.0.1:{_free_port()}", ready_timeout=0.1)

    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def endless_loop() -> None:
        started.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    monkeypatch.setattr(worker, "_worker_loop", endless_loop)
    await worker.run()
    assert started.is_set()
    assert cancelled.is_set()
