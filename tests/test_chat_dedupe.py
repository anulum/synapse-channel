# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — K4-WF8: a retried chat is accepted once by a real hub
"""A chat retried with the same ``client_msg_id`` is stored and delivered once.

Reproduced before the change on a real hub with a journal: two sends of the same
chat reached the recipient twice and left two journal events. These tests drive the
public chat route of real hubs and read the real journal.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from hub_e2e_helpers import AgentHandle, close_agents, connect_agent, running_hub
from synapse_channel.core.chat_dedupe import (
    DEFAULT_CHAT_DEDUPE_CAPACITY,
    DEFAULT_CHAT_DEDUPE_WINDOW,
    ChatDedupe,
    chat_digest,
)
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind
from synapse_channel.core.metrics import collect_hub_metrics
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType


def _chats(handle: AgentHandle, payload: str) -> list[dict[str, Any]]:
    return [
        message
        for message in handle.recorder.messages
        if message.get("type") == MessageType.CHAT and message.get("payload") == payload
    ]


def _journal_chats(store: EventStore, payload: str) -> list[dict[str, Any]]:
    return [
        event.payload
        for event in store.iter_events(kinds=[EventKind.CHAT])
        if event.payload.get("payload") == payload
    ]


async def test_a_retry_is_answered_with_the_first_copy_and_delivered_once(
    tmp_path: Path,
) -> None:
    store = EventStore(tmp_path / "hub.db")
    hub = SynapseHub(journal=store)
    async with running_hub(hub) as (_hub, uri):
        alice = await connect_agent("P/alice", uri)
        bob = await connect_agent("P/bob", uri)
        try:
            await alice.agent.chat("deploy now", target="P/bob", client_msg_id="cm-1")
            first = await bob.recorder.wait_for(
                lambda m: m.get("type") == MessageType.CHAT and m.get("payload") == "deploy now"
            )
            await alice.agent.chat("deploy now", target="P/bob-rx", client_msg_id="cm-1")
            answer = await alice.recorder.wait_for(lambda m: m.get("duplicate") is True)
            await asyncio.sleep(0.2)
            delivered = _chats(bob, "deploy now")
        finally:
            await close_agents(alice, bob)
    metrics = {metric.name: metric.value for metric in collect_hub_metrics(hub)}
    journalled = _journal_chats(store, "deploy now")
    store.close()
    assert (answer["msg_id"], answer["seq"]) == (first["msg_id"], first["seq"])
    assert len(delivered) == 1
    assert len(journalled) == 1
    assert metrics["synapse_chat_duplicates_suppressed_total"] == 1
    assert metrics["synapse_chat_client_id_conflicts_total"] == 0


async def test_a_reused_id_for_a_different_message_is_refused() -> None:
    hub = SynapseHub()
    async with running_hub(hub) as (_hub, uri):
        alice = await connect_agent("P/alice", uri)
        bob = await connect_agent("P/bob", uri)
        try:
            await alice.agent.chat("first", target="P/bob", client_msg_id="cm-7")
            await bob.recorder.wait_for(lambda m: m.get("payload") == "first")
            await alice.agent.chat("second", target="P/bob", client_msg_id="cm-7")
            refusal = await alice.recorder.wait_for(lambda m: m.get("type") == MessageType.ERROR)
            await alice.agent.chat("third", target="P/bob")
            await bob.recorder.wait_for(lambda m: m.get("payload") == "third")
        finally:
            await close_agents(alice, bob)
    assert "client_msg_id 'cm-7' was already used for a different message" in refusal["payload"]
    assert _chats(bob, "second") == []
    assert hub.counters.chat_client_id_conflicts == 1


async def test_a_chat_nobody_received_is_routed_again_when_retried(tmp_path: Path) -> None:
    """A retry is a redelivery attempt until a copy reaches a live recipient."""
    store = EventStore(tmp_path / "hub.db")
    hub = SynapseHub(journal=store)
    async with running_hub(hub) as (_hub, uri):
        alice = await connect_agent("P/alice", uri)
        try:
            await alice.agent.chat("ping", target="P/bob", client_msg_id="cm-offline")
            await asyncio.sleep(0.2)
            bob = await connect_agent("P/bob", uri)
            await alice.agent.chat("ping", target="P/bob", client_msg_id="cm-offline")
            received = await bob.recorder.wait_for(lambda m: m.get("payload") == "ping")
            await alice.agent.chat("ping", target="P/bob", client_msg_id="cm-offline")
            answer = await alice.recorder.wait_for(lambda m: m.get("duplicate") is True)
        finally:
            await close_agents(alice, bob)
    journalled = _journal_chats(store, "ping")
    store.close()
    assert len(journalled) == 2  # the undelivered copy and the redelivery
    assert answer["msg_id"] == received["msg_id"]


async def test_the_memory_is_per_process(tmp_path: Path) -> None:
    """The journal cannot say whether a copy was received, so a restart forgets."""
    first_store = EventStore(tmp_path / "hub.db")
    async with running_hub(SynapseHub(journal=first_store)) as (_hub, uri):
        alice = await connect_agent("P/alice", uri)
        bob = await connect_agent("P/bob", uri)
        try:
            await alice.agent.chat("once", target="P/bob", client_msg_id="cm-restart")
            await bob.recorder.wait_for(lambda m: m.get("payload") == "once")
        finally:
            await close_agents(alice, bob)
    first_store.close()

    store = EventStore(tmp_path / "hub.db")
    hub = SynapseHub(journal=store)
    assert len(hub.chat_dedupe) == 0
    store.close()


async def test_channel_chats_are_remembered_only_when_accepted() -> None:
    hub = SynapseHub()
    async with running_hub(hub) as (_hub, uri):
        owner = await connect_agent("P/owner", uri)
        outsider = await connect_agent("P/outsider", uri)
        try:
            await owner.agent.channel_create("room")
            await owner.recorder.wait_for(lambda m: m.get("type") == MessageType.CHANNEL_RESULT)
            await outsider.agent.chat("hi", target="all", channel="room", client_msg_id="cm-c")
            await outsider.recorder.wait_for(lambda m: m.get("type") == MessageType.ERROR)
            outsider.recorder.messages.clear()
            await outsider.agent.chat("hi", target="all", channel="room", client_msg_id="cm-c")
            again = await outsider.recorder.wait_for(lambda m: m.get("type") == MessageType.ERROR)
            await owner.agent.chat("alone", target="all", channel="room", client_msg_id="cm-a")
            await owner.agent.chat("alone", target="all", channel="room", client_msg_id="cm-a")
            await asyncio.sleep(0.2)
            assert hub.counters.chat_duplicates_suppressed == 0  # it reached nobody
            await owner.agent.channel_invite("room", "P/outsider")
            await outsider.agent.channel_join("room")
            await outsider.recorder.wait_for(lambda m: m.get("type") == MessageType.CHANNEL_RESULT)
            await owner.agent.chat("note", target="all", channel="room", client_msg_id="cm-o")
            await outsider.recorder.wait_for(lambda m: m.get("payload") == "note")
            await owner.agent.chat("note", target="all", channel="room", client_msg_id="cm-o")
            duplicate = await owner.recorder.wait_for(lambda m: m.get("duplicate") is True)
        finally:
            await close_agents(owner, outsider)
    assert "not a member of channel 'room'" in again["payload"]
    assert duplicate["channel"] == "room"
    assert hub.counters.chat_duplicates_suppressed == 1


def test_retention_is_bounded_by_age_and_count() -> None:
    dedupe = ChatDedupe(capacity=2, window_seconds=10.0)
    frame = {"target": "P/b", "payload": "x", "msg_id": 3}
    digest = chat_digest(frame)
    dedupe.remember("P/a", "one", digest, frame, accepted_at=100.0)
    duplicate = dedupe.check("P/a", "one", digest, now=105.0)
    assert (duplicate.outcome, duplicate.original) == ("duplicate", {"msg_id": 3})
    assert dedupe.check("P/a", "one", "other-digest", now=105.0).outcome == "conflict"
    assert dedupe.check("P/a", "one", digest, now=111.0).outcome == "new"
    dedupe.remember("P/a", "two", digest, frame, accepted_at=100.0)
    dedupe.remember("P/a", "three", digest, frame, accepted_at=100.0)
    assert len(dedupe) == 2
    assert dedupe.check("P/a", "one", digest, now=101.0).outcome == "new"
    assert dedupe.check("P/other", "two", digest, now=101.0).outcome == "new"
    assert (DEFAULT_CHAT_DEDUPE_CAPACITY, DEFAULT_CHAT_DEDUPE_WINDOW) == (4096, 86_400.0)


def test_the_digest_ignores_hub_stamps_and_transport_aliases() -> None:
    sent = {"type": "chat", "sender": "P/a", "target": "P/b-rx", "payload": "x", "timestamp": 1}
    stored = {
        **sent,
        "target": "P/b",
        "msg_id": 4,
        "seq": 9,
        "hub_id": "h",
        "timestamp": 2,
        "forward_id": "f",
    }
    assert chat_digest(sent) == chat_digest(stored)
    assert chat_digest(sent) != chat_digest({**sent, "payload": "y"})


@pytest.mark.parametrize(("capacity", "window"), [(0, 10.0), (1, 0.0), (1, float("nan"))])
def test_the_memory_refuses_a_useless_configuration(capacity: int, window: float) -> None:
    with pytest.raises(ValueError, match="chat dedupe"):
        ChatDedupe(capacity=capacity, window_seconds=window)
