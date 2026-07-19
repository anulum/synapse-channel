# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — production-surface tests for callback None/awaitable compat (K3-B1)

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any, cast

import pytest

from hub_e2e_helpers import read_until_type, running_hub, send_json
from synapse_channel.client.agent import SynapseAgent
from synapse_channel.client.agent_dispatch import (
    AgentDispatchMixin,
    MessageCallback,
    invoke_message_callback,
)
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.protocol import WIRE_PROTOCOL_VERSION, MessageType


class _DispatchHost(AgentDispatchMixin):
    """Minimal production mixin host for unit-surface dispatch tests."""

    def __init__(self, callback: Any = None, *, name: str = "A") -> None:
        self.callback = callback
        self.hub_id = "unknown"
        self.hub_protocol_version: int | None = None
        self.mailbox = False
        self.mailbox_advance = None
        self.mailbox_for = ""
        self.name = name
        self.on_lease_granted = None
        self.owner_lease = ""
        self.ready_event = asyncio.Event()
        self.verbose = False
        self._mailbox_since_seq = 0

    async def ack(self, seq: int, *, mailbox_for: str = "") -> bool:
        return True

    async def _track_mailbox_frame(self, data: dict[str, Any]) -> None:
        return None


def _welcome_frame() -> str:
    return json.dumps(
        {
            "type": MessageType.WELCOME,
            "hub_id": "syn-test",
            "protocol_version": WIRE_PROTOCOL_VERSION,
            "payload": "Welcome",
        }
    )


def _chat_frame(sender: str, payload: str) -> str:
    return json.dumps(
        {
            "type": MessageType.CHAT,
            "sender": sender,
            "target": "all",
            "payload": payload,
            "seq": 1,
        }
    )


async def test_invoke_awaits_async_callback() -> None:
    seen: list[dict[str, Any]] = []

    async def cb(data: dict[str, Any]) -> None:
        seen.append(data)

    await invoke_message_callback(cb, {"type": "chat", "payload": "x"})
    assert seen == [{"type": "chat", "payload": "x"}]


async def test_invoke_accepts_sync_none_callback() -> None:
    seen: list[dict[str, Any]] = []

    def cb(data: dict[str, Any]) -> None:
        seen.append(data)

    await invoke_message_callback(cb, {"type": "chat", "payload": "y"})
    assert seen == [{"type": "chat", "payload": "y"}]


async def test_invoke_propagates_sync_exception() -> None:
    def cb(_data: dict[str, Any]) -> None:
        raise RuntimeError("sync boom")

    with pytest.raises(RuntimeError, match="sync boom"):
        await invoke_message_callback(cb, {"type": "chat"})


async def test_invoke_propagates_async_exception() -> None:
    async def cb(_data: dict[str, Any]) -> None:
        raise ValueError("async boom")

    with pytest.raises(ValueError, match="async boom"):
        await invoke_message_callback(cb, {"type": "chat"})


async def test_dispatch_sync_callback_multiple_frames_after_ready() -> None:
    seen: list[str] = []

    def cb(data: dict[str, Any]) -> None:
        if data.get("type") == MessageType.CHAT:
            seen.append(str(data.get("payload")))

    host = _DispatchHost(cast(MessageCallback, cb))
    dispatch = cast(Any, host)._dispatch
    await dispatch(_welcome_frame())
    assert host.ready_event.is_set()
    await dispatch(_chat_frame("B", "one"))
    await dispatch(_chat_frame("B", "two"))
    assert seen == ["one", "two"]


async def test_dispatch_async_callbacks_remain_ordered() -> None:
    order: list[str] = []

    async def cb(data: dict[str, Any]) -> None:
        if data.get("type") != MessageType.CHAT:
            return
        payload = str(data.get("payload"))
        order.append(f"start:{payload}")
        await asyncio.sleep(0)
        order.append(f"end:{payload}")

    host = _DispatchHost(cb)
    dispatch = cast(Any, host)._dispatch
    await dispatch(_welcome_frame())
    await dispatch(_chat_frame("B", "a"))
    await dispatch(_chat_frame("B", "b"))
    assert order == ["start:a", "end:a", "start:b", "end:b"]


async def test_dispatch_no_callback_still_sets_ready() -> None:
    host = _DispatchHost(None)
    dispatch = cast(Any, host)._dispatch
    await dispatch(_welcome_frame())
    assert host.ready_event.is_set()
    await dispatch(_chat_frame("B", "ignored"))


async def test_malformed_json_never_reaches_callback() -> None:
    seen: list[Any] = []

    def cb(data: dict[str, Any]) -> None:
        seen.append(data)

    host = _DispatchHost(cast(MessageCallback, cb), name="A")
    host.verbose = False
    dispatch = cast(Any, host)._dispatch
    await dispatch(b"{not-json")
    await dispatch("not json either")
    assert seen == []


async def test_live_agent_sync_callback_survives_multiple_inbound_chats() -> None:
    """Real SynapseAgent + hub: sync callback processes frames after readiness."""
    seen: list[str] = []

    def on_message(data: dict[str, Any]) -> None:
        if data.get("type") == MessageType.CHAT and data.get("sender") != "listener":
            seen.append(str(data.get("payload")))

    async with running_hub(SynapseHub(hub_id="syn-k3b1")) as (_hub, uri):
        agent = SynapseAgent(
            "listener",
            on_message_callback=cast(MessageCallback, on_message),
            uri=uri,
            verbose=False,
            heartbeat_interval=60.0,
        )
        task = asyncio.create_task(agent.connect())
        try:
            await asyncio.wait_for(agent.ready_event.wait(), timeout=5.0)
            from websockets.asyncio.client import connect

            async with connect(uri) as peer:
                await read_until_type(peer, "welcome")
                await send_json(peer, sender="peer", type="chat", target="all", payload="first")
                await send_json(peer, sender="peer", type="chat", target="all", payload="second")
                for _ in range(50):
                    if seen == ["first", "second"]:
                        break
                    await asyncio.sleep(0.05)
            assert seen == ["first", "second"]
            assert agent.ready_event.is_set()
            assert not task.done()
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
