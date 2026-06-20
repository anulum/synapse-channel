# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the on-channel model worker

from __future__ import annotations

import time

import pytest

from synapse_channel.chat_backends import OpenAIChatClient, RuleBasedClient
from synapse_channel.llm_worker import (
    DEFAULT_OLLAMA_BASE_URL,
    OPENAI_DEFAULT_BASE_URL,
    SynapseLLMWorker,
    is_service_message,
)


class FakeAgent:
    """Stand-in for SynapseAgent capturing chat output."""

    def __init__(self, *, ready: bool = True, connect_exc: Exception | None = None) -> None:
        self.running = True
        self.chats: list[tuple[str, str]] = []
        self.cards: list[tuple[str, ...]] = []
        self._ready = ready
        self._connect_exc = connect_exc

    async def connect(self) -> None:
        if self._connect_exc is not None:
            raise self._connect_exc

    async def wait_until_ready(self, timeout: float = 5.0) -> bool:
        return self._ready

    async def chat(self, payload: str, *, target: str = "all") -> None:
        self.chats.append((target, payload))

    async def advertise(
        self,
        *,
        description: str = "",
        skills: tuple[str, ...] | list[str] = (),
        task_classes: tuple[str, ...] | list[str] = (),
        model: str = "",
        meta: dict[str, object] | None = None,
    ) -> None:
        self.cards.append(tuple(task_classes))


class FakeBackend:
    """Chat backend returning a canned reply or raising on demand."""

    def __init__(self, reply: str = "ok", exc: Exception | None = None) -> None:
        self.reply = reply
        self.exc = exc

    def generate(self, *, system_prompt: str, user_prompt: str) -> str:
        if self.exc is not None:
            raise self.exc
        return self.reply


def _worker(**kwargs: object) -> SynapseLLMWorker:
    params: dict[str, object] = {"name": "ALPHA", "provider": "rule"}
    params.update(kwargs)
    return SynapseLLMWorker(**params)  # type: ignore[arg-type]


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


def test_build_client_openai_keeps_base(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
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


def test_build_client_uses_api_key_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_KEY", "secret-token")
    worker = _worker(provider="openai", api_key_env="MY_KEY")
    assert isinstance(worker.client, OpenAIChatClient)
    assert worker.client.api_key == "secret-token"


def test_build_client_rejects_unknown_provider() -> None:
    with pytest.raises(RuntimeError, match="Unsupported provider"):
        _worker(provider="telepathy")


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
    worker = _worker(name="ALPHA")
    worker.agent = FakeAgent()  # type: ignore[assignment]
    worker.client = FakeBackend(reply="  done   now  ")
    await worker._process_item({"sender": "USER", "payload": "go"})
    assert worker.agent.chats == [("all", "done now")]  # type: ignore[attr-defined]
    assert worker.last_reply_ts > 0


async def test_process_item_targets_sender_in_private_mode() -> None:
    worker = _worker(name="ALPHA", reply_target_mode="sender")
    worker.agent = FakeAgent()  # type: ignore[assignment]
    worker.client = FakeBackend(reply="hi")
    await worker._process_item({"sender": "USER", "payload": "go"})
    assert worker.agent.chats == [("USER", "hi")]  # type: ignore[attr-defined]


async def test_process_item_reports_backend_error() -> None:
    worker = _worker(name="ALPHA")
    worker.agent = FakeAgent()  # type: ignore[assignment]
    worker.client = FakeBackend(exc=RuntimeError("model down"))
    await worker._process_item({"sender": "USER", "payload": "go"})
    target, text = worker.agent.chats[0]  # type: ignore[attr-defined]
    assert "model error -> model down" in text


async def test_process_item_throttles(monkeypatch: pytest.MonkeyPatch) -> None:
    worker = _worker(name="ALPHA", min_reply_interval=5.0)
    worker.agent = FakeAgent()  # type: ignore[assignment]
    worker.client = FakeBackend(reply="hi")
    worker.last_reply_ts = time.time()  # force a throttle wait

    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr("synapse_channel.llm_worker.asyncio.sleep", fake_sleep)
    await worker._process_item({"sender": "USER", "payload": "go"})
    assert slept and slept[0] > 0


# --- _worker_loop ------------------------------------------------------------


async def test_worker_loop_processes_then_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    worker = _worker(name="ALPHA")
    worker.agent = FakeAgent()  # type: ignore[assignment]
    processed: list[dict[str, str]] = []

    async def fake_process(item: dict[str, str]) -> None:
        processed.append(item)
        worker.agent.running = False  # stop after the first item

    monkeypatch.setattr(worker, "_process_item", fake_process)
    await worker.inbox.put({"sender": "USER", "payload": "go"})
    await worker._worker_loop()
    assert processed == [{"sender": "USER", "payload": "go"}]


# --- run ---------------------------------------------------------------------


async def test_run_completes_when_connection_finishes() -> None:
    worker = _worker(name="ALPHA")
    worker.agent = FakeAgent(ready=True)  # type: ignore[assignment]
    await worker.run()  # connect returns -> worker task cancelled -> run returns


async def test_run_warns_on_handshake_timeout(capsys: pytest.CaptureFixture[str]) -> None:
    worker = _worker(name="ALPHA")
    worker.agent = FakeAgent(ready=False)  # type: ignore[assignment]
    await worker.run()
    assert "handshake timeout" in capsys.readouterr().out


async def test_run_reports_connection_error(capsys: pytest.CaptureFixture[str]) -> None:
    worker = _worker(name="ALPHA")
    worker.agent = FakeAgent(connect_exc=RuntimeError("dropped"))  # type: ignore[assignment]
    await worker.run()
    assert "worker stopped: dropped" in capsys.readouterr().out


def test_worker_forwards_token_to_its_agent() -> None:
    worker = _worker(name="ALPHA", token="s3cret")
    assert worker.agent.token == "s3cret"


def test_worker_default_token_is_none() -> None:
    worker = _worker(name="ALPHA")
    assert worker.agent.token is None


async def test_run_advertises_capability_card_when_ready() -> None:
    worker = _worker(name="ALPHA", task_classes=("chat", "reason"))
    fake = FakeAgent(ready=True)
    worker.agent = fake  # type: ignore[assignment]
    await worker.run()
    assert fake.cards == [("chat", "reason")]  # advertised its task classes


async def test_run_skips_advertise_on_handshake_timeout() -> None:
    worker = _worker(name="ALPHA")
    fake = FakeAgent(ready=False)
    worker.agent = fake  # type: ignore[assignment]
    await worker.run()
    assert fake.cards == []  # not advertised when the handshake timed out
