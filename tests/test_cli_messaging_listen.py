# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the messaging CLI commands (send/wait/listen)

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Coroutine
from typing import Any, cast

import pytest

from hub_e2e_helpers import AgentHandle, _free_port, close_agents, connect_agent, running_hub
from synapse_channel import cli_messaging
from synapse_channel.cli_messaging_types import AgentFactory
from synapse_channel.core.auth import TokenAuthenticator
from synapse_channel.core.hub import SynapseHub


async def _wait_for_presence(observer: AgentHandle, name: str) -> None:
    await observer.recorder.wait_for(
        lambda message: message.get("type") == "presence_update" and message.get("agent") == name
    )


async def test_listen_prints_chat_and_presence(capsys: pytest.CaptureFixture[str]) -> None:
    async with running_hub(SynapseHub()) as (_hub, uri):
        observer = await connect_agent("OBSERVER", uri)
        listen_task = asyncio.create_task(
            cli_messaging._listen(uri=uri, name="USER", max_messages=3)
        )
        fast = await connect_agent("FAST", uri)
        try:
            await _wait_for_presence(observer, "USER")
            await fast.agent.chat("hi", target="all")
            await fast.agent.chat("again", target="all")
            code = await listen_task
        finally:
            await close_agents(fast, observer)

    assert code == 0
    out = capsys.readouterr().out
    assert "FAST: hi" in out
    assert "[presence] joined" in out


async def test_listen_neutralises_terminal_controls(capsys: pytest.CaptureFixture[str]) -> None:
    payload = "hello\x1b]52;c;YQ==\x07\nspoof"
    async with running_hub(SynapseHub()) as (_hub, uri):
        observer = await connect_agent("OBSERVER", uri)
        listen_task = asyncio.create_task(
            cli_messaging._listen(
                uri=uri,
                name="B-listener",
                for_name="B",
                max_messages=1,
            )
        )
        sender = await connect_agent("A", uri)
        try:
            await _wait_for_presence(observer, "B-listener")
            await sender.agent.chat(payload, target="B")
            code = await listen_task
        finally:
            await close_agents(sender, observer)

    out = capsys.readouterr().out
    assert code == 0
    assert r"A: hello\x1b]52;c;YQ==\x07\nspoof" in out
    assert "\x1b" not in out
    assert "\x07" not in out


def test_cmd_listen_dispatches_real_command(capsys: pytest.CaptureFixture[str]) -> None:
    ns = argparse.Namespace(
        uri=f"ws://127.0.0.1:{_free_port()}",
        name="USER",
        token=None,
        for_name=None,
        ready_timeout=0.1,
    )
    assert cli_messaging._cmd_listen(ns) == 1
    assert "Could not reach hub" in capsys.readouterr().out


async def test_listen_threads_token_to_agent() -> None:
    token = "s3cret"
    async with running_hub(SynapseHub(authenticator=TokenAuthenticator([token]))) as (_hub, uri):
        code = await cli_messaging._listen(
            uri=uri,
            name="U",
            token=token,
            max_messages=0,
        )

    assert code == 0


async def test_listen_for_filters_to_inbox(capsys: pytest.CaptureFixture[str]) -> None:
    async with running_hub(SynapseHub()) as (_hub, uri):
        observer = await connect_agent("OBSERVER", uri)
        listen_task = asyncio.create_task(
            cli_messaging._listen(uri=uri, name="B-listener", for_name="B", max_messages=2)
        )
        sender = await connect_agent("A", uri)
        try:
            await _wait_for_presence(observer, "B-listener")
            await sender.agent.chat("just C", target="C")
            await sender.agent.chat("everyone", target="all")
            await sender.agent.chat("you two", target="B,C")
            code = await listen_task
        finally:
            await close_agents(sender, observer)

    assert code == 0
    out = capsys.readouterr().out
    assert "everyone" in out
    assert "you two" in out
    assert "just C" not in out
    assert "presence" not in out


def test_cmd_listen_handles_keyboard_interrupt(capsys: pytest.CaptureFixture[str]) -> None:
    def stop(coro: Coroutine[Any, Any, int]) -> int:
        coro.close()
        raise KeyboardInterrupt

    async def listen_once(**_: Any) -> int:
        return 0

    ns = argparse.Namespace(
        uri="ws://127.0.0.1:1",
        name="USER",
        token=None,
        for_name=None,
        ready_timeout=0.1,
    )
    assert cli_messaging._cmd_listen(ns, listen_runner=listen_once, async_runner=stop) == 0
    assert "[USER] stopped listening." in capsys.readouterr().out


# --- decryption-key handling and encrypted-payload rendering --------------------


async def test_listen_reports_an_unreadable_decryption_key(
    tmp_path: object, capsys: pytest.CaptureFixture[str]
) -> None:
    from pathlib import Path

    missing = Path(str(tmp_path)) / "absent.key"
    code = await cli_messaging._listen(
        uri=f"ws://127.0.0.1:{_free_port()}",
        name="LISTENER",
        decrypt_key_file=str(missing),
    )
    assert code == 1
    assert "decryption key failed:" in capsys.readouterr().out


def test_render_chat_payload_marks_encrypted_without_a_key() -> None:
    from synapse_channel.cli_messaging_listen import _render_chat_payload

    data = {"payload": "", "encrypted": {"v": 1}}
    assert _render_chat_payload(data, None) == "<encrypted payload>"
    # a hub-provided placeholder payload wins over the generic marker
    assert _render_chat_payload({"payload": "[enc]", "encrypted": {"v": 1}}, None) == "[enc]"
    # plain chat renders its payload regardless of a configured key
    assert _render_chat_payload({"payload": "hello"}, b"k" * 32) == "hello"


def test_render_chat_payload_round_trips_and_reports_a_wrong_key() -> None:
    from synapse_channel.cli_messaging_listen import _render_chat_payload
    from synapse_channel.core.payload_crypto import PayloadContext, encrypt_payload

    key = b"k" * 32
    context = PayloadContext(
        message_type="chat", sender="PEER", target="LISTENER", channel="", task_id=""
    )
    envelope = encrypt_payload(
        "secret text",
        key,
        key_id="k1",
        recipients=["LISTENER"],
        context=context,
    )
    data = {
        "type": "chat",
        "payload": "<masked>",
        "encrypted": dict(envelope),
        "sender": "PEER",
        "target": "LISTENER",
        "channel": "",
        "task_id": "",
    }
    assert _render_chat_payload(data, key) == "secret text"
    wrong = _render_chat_payload(data, b"x" * 32)
    assert wrong.startswith("<encrypted payload:")


async def test_listen_max_reached_with_no_open_connection(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Hitting the message cap with the socket already gone skips the close call."""

    class _NoSocketAgent:
        def __init__(self, name: str, callback: Any, **_kwargs: Any) -> None:
            self.name = name
            self.callback = callback
            self.running = True
            self.connection = None
            self.last_close_code: int | None = None
            self.last_close_reason = ""

        async def connect(self) -> None:
            await self.callback({"type": "chat", "sender": "A", "payload": "hi", "target": "all"})

        async def wait_until_ready(self, timeout: float) -> bool:
            del timeout
            return True

    code = await cli_messaging._listen(
        uri="ws://unused",
        name="L",
        max_messages=1,
        agent_factory=cast("AgentFactory", _NoSocketAgent),
    )
    assert code == 0
    assert "A: hi" in capsys.readouterr().out


async def test_listen_exits_nonzero_on_post_welcome_name_conflict(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Close 4009 after welcome must not leave a silent exit-0 dead listener."""
    async with running_hub(SynapseHub()) as (_hub, uri):
        holder = await connect_agent("DUP", uri)
        try:
            code = await cli_messaging._listen(uri=uri, name="DUP", ready_timeout=3.0)
        finally:
            await close_agents(holder)

    assert code == 1
    out = capsys.readouterr().out
    assert "code 4009" in out or "name conflict" in out
    assert "name already online" in out


async def test_listen_exits_nonzero_when_hub_closes_mid_stream(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A hub-initiated death after a healthy listen session is terminal (nonzero)."""

    class _DiesAfterReady:
        def __init__(self, name: str, callback: Any, **_kwargs: Any) -> None:
            del callback
            self.name = name
            self.running = True
            self.connection = None
            self.last_close_code: int | None = None
            self.last_close_reason = ""
            self._ready = asyncio.Event()

        async def connect(self) -> None:
            self._ready.set()
            # Die after the post-welcome grace so the mid-stream path is exercised
            # (not only closed_after_ready).
            await asyncio.sleep(0.4)
            self.last_close_code = 4009
            self.last_close_reason = "name conflict"
            self.running = False

        async def wait_until_ready(self, timeout: float) -> bool:
            del timeout
            await self._ready.wait()
            return True

    code = await cli_messaging._listen(
        uri="ws://unused",
        name="L",
        ready_timeout=1.0,
        agent_factory=cast("AgentFactory", _DiesAfterReady),
    )
    assert code == 1
    out = capsys.readouterr().out
    assert "code 4009" in out or "name conflict" in out
