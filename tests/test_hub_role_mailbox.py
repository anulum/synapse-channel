# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — a live hub keeps role mail in the role's mailbox across holders
"""A role's mail survives its holders: the next holder gets what nobody acknowledged."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from hub_e2e_helpers import AgentHandle, Recorder, close_agents, connect_agent, running_hub
from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType

ROLE = "P/reviewer"
KEY = "role:P/reviewer"


async def _holder(name: str, uri: str, *, since_seq: int) -> AgentHandle:
    """Connect a mailbox client that binds ``ROLE`` and resumes after ``since_seq``."""
    recorder = Recorder()
    agent = SynapseAgent(
        name,
        recorder,
        uri=uri,
        heartbeat_interval=60.0,
        verbose=False,
        roles=(ROLE,),
        mailbox=True,
        mailbox_since_seq=since_seq,
    )
    handle = AgentHandle(agent=agent, recorder=recorder, task=asyncio.create_task(agent.connect()))
    if not await agent.wait_until_ready(3.0):
        await handle.close()
        raise TimeoutError(f"{name} did not receive the hub welcome")
    return handle


async def _next_who(handle: AgentHandle, seen: int) -> dict[str, Any]:
    """Return the first WHO snapshot recorded after the first ``seen`` messages."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 3.0
    while loop.time() < deadline:
        for message in handle.recorder.messages[seen:]:
            if message.get("type") == MessageType.WHO_SNAPSHOT:
                return message
        await asyncio.sleep(0.01)
    raise TimeoutError("the hub sent no WHO snapshot")


async def _role_pending(handle: AgentHandle, expected: int) -> dict[str, Any]:
    """Request WHO until the role mailbox shows ``expected`` pending chats."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 3.0
    while loop.time() < deadline:
        seen = len(handle.recorder.messages)
        await handle.agent.request_who()
        snapshot = await _next_who(handle, seen)
        if snapshot["mailbox_pending"].get(KEY) == expected:
            return snapshot
        await asyncio.sleep(0.02)
    raise TimeoutError(f"the role mailbox never showed {expected} pending")


async def _journalled(store: EventStore, payload: str) -> int:
    """Return the sequence of the journalled chat carrying ``payload``."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 3.0
    while loop.time() < deadline:
        for event in store.read_since(0, kinds=(EventKind.CHAT,)):
            if event.payload.get("payload") == payload:
                return event.seq
        await asyncio.sleep(0.01)
    raise TimeoutError(f"{payload!r} was not journalled")


def _journalled_chat(text: str) -> dict[str, Any]:
    """Return a chat payload as a hub journals it, addressed to ``ROLE``."""
    return {"sender": "X/peer", "target": ROLE, "type": MessageType.CHAT, "payload": text}


def _chats(handle: AgentHandle, payload: str) -> list[dict[str, Any]]:
    return [
        message
        for message in handle.recorder.messages
        if message.get("type") == MessageType.CHAT and message.get("payload") == payload
    ]


async def test_a_new_holder_receives_role_mail_its_predecessor_never_acknowledged(
    tmp_path: Path,
) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(SynapseHub(journal=store)) as (_hub, uri):
            peer = await connect_agent("X/peer", uri)
            first: AgentHandle | None = None
            second: AgentHandle | None = None
            try:
                await peer.agent.send_message(MessageType.CHAT, target=ROLE, payload="review me")
                seq = await _journalled(store, "review me")
                # The holder's own cursor is already past the chat: only the role
                # mailbox can bring it back.
                first = await _holder("P/claude-a", uri, since_seq=store.max_seq())
                replayed = await first.recorder.wait_for(
                    lambda message: message.get("payload") == "review me"
                )
                assert (replayed["replayed"], replayed["seq"]) == (True, seq)
                await _role_pending(peer, 0)
                await close_agents(first)
                first = None

                second = await _holder("P/claude-b", uri, since_seq=0)
                await _role_pending(second, 0)
                assert _chats(second, "review me") == []
            finally:
                await close_agents(*(h for h in (first, second) if h is not None), peer)
        marks = store.read_since(0, kinds=(EventKind.MAILBOX_WATERMARK,))
    finally:
        store.close()
    role_marks = [mark.payload for mark in marks if mark.payload["identity"] == KEY]
    assert role_marks == [{"identity": KEY, "through_seq": seq, "source": "ack"}]


async def test_role_mail_waits_at_the_role_while_no_holder_is_connected(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(SynapseHub(journal=store)) as (_hub, uri):
            holder = await _holder("P/claude-a", uri, since_seq=0)
            await close_agents(holder)
            peer = await connect_agent("X/peer", uri)
            try:
                await peer.agent.send_message(MessageType.CHAT, target=ROLE, payload="one")
                await peer.agent.send_message(MessageType.CHAT, target=ROLE, payload="two")
                await _journalled(store, "two")
                snapshot = await _role_pending(peer, 2)
            finally:
                await close_agents(peer)
    finally:
        store.close()
    assert snapshot["mailbox_pending"].get("P/claude-a") == 0


async def test_a_custodian_closes_a_role_mailbox_by_its_key(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        hub = SynapseHub(journal=store, mailbox_custodians={"P": "P/boss"})
        async with running_hub(hub) as (_hub, uri):
            holder = await _holder("P/claude-a", uri, since_seq=0)
            await close_agents(holder)
            peer = await connect_agent("X/peer", uri)
            boss = await connect_agent("P/boss", uri)
            try:
                await peer.agent.send_message(MessageType.CHAT, target=ROLE, payload="stale")
                await _journalled(store, "stale")
                await _role_pending(boss, 1)
                close: dict[str, Any] = {"from": KEY, "reason": "old review queue"}
                await boss.agent.send_message(MessageType.MAILBOX_HANDOVER, **close)
                result = await boss.recorder.wait_for(
                    lambda message: message.get("type") == MessageType.MAILBOX_HANDOVER_RESULT
                )
                assert (result["status"], result["mailbox"]) == ("applied", KEY)
                await _role_pending(boss, 0)
            finally:
                await close_agents(peer, boss)
        marks = store.read_since(0, kinds=(EventKind.MAILBOX_WATERMARK,))
    finally:
        store.close()
    assert marks[-1].payload["identity"] == KEY
    assert marks[-1].payload["source"] == "closed"


async def test_role_mail_closed_under_the_plain_name_before_an_upgrade_stays_closed(
    tmp_path: Path,
) -> None:
    """An older hub counted role mail under the plain role name; closing it there carries over."""
    store = EventStore(tmp_path / "hub.db")
    try:
        for text in ("old one", "old two"):
            store.append(EventKind.CHAT, _journalled_chat(text))
        closed = store.max_seq()
        store.append(
            EventKind.MAILBOX_WATERMARK,
            {"identity": ROLE, "through_seq": closed, "source": "closed"},
        )
        store.append(EventKind.CHAT, _journalled_chat("new"))
        async with running_hub(SynapseHub(journal=store)) as (_hub, uri):
            holder = await _holder("P/claude-a", uri, since_seq=store.max_seq())
            try:
                await holder.recorder.wait_for(lambda message: message.get("payload") == "new")
                await _role_pending(holder, 0)
                stale = _chats(holder, "old one") + _chats(holder, "old two")
            finally:
                await close_agents(holder)
    finally:
        store.close()
    assert stale == []


async def test_a_role_binding_is_journalled_and_survives_a_restart(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(SynapseHub(journal=store)) as (_hub, uri):
            first = await _holder("P/claude-a", uri, since_seq=0)
            await close_agents(first)
            again = await _holder("P/claude-a", uri, since_seq=0)
            await close_agents(again)
            peer = await connect_agent("X/peer", uri)
            try:
                await peer.agent.send_message(MessageType.CHAT, target=ROLE, payload="queued")
                await _journalled(store, "queued")
            finally:
                await close_agents(peer)
        bindings = [event.payload for event in store.read_since(0, kinds=(EventKind.ROLE_BINDING,))]
        async with running_hub(SynapseHub(journal=store)) as (_restarted, uri):
            observer = await connect_agent("OBSERVER", uri)
            try:
                snapshot = await _role_pending(observer, 1)
            finally:
                await close_agents(observer)
    finally:
        store.close()
    assert bindings == [{"identity": "P/claude-a", "roles": [ROLE]}]
    assert snapshot["mailbox_pending"][KEY] == 1


async def test_a_custodian_role_held_by_a_new_session_closes_a_dormant_mailbox(
    tmp_path: Path,
) -> None:
    """The custodian map can name a role, so custody follows whoever holds it now."""
    store = EventStore(tmp_path / "hub.db")
    try:
        hub = SynapseHub(journal=store, mailbox_custodians={"P": ROLE})
        async with running_hub(hub) as (_hub, uri):
            peer = await connect_agent("X/peer", uri)
            try:
                await peer.agent.send_message(MessageType.CHAT, target="P/gone", payload="lost")
                await _journalled(store, "lost")
            finally:
                await close_agents(peer)
            stranger = await connect_agent("P/claude-z", uri)
            holder = await _holder("P/claude-a", uri, since_seq=store.max_seq())
            try:
                close: dict[str, Any] = {"from": "P/gone", "reason": "session ended"}
                await stranger.agent.send_message(MessageType.MAILBOX_HANDOVER, **close)
                refused = await stranger.recorder.wait_for(
                    lambda message: message.get("type") == MessageType.MAILBOX_HANDOVER_RESULT
                )
                await holder.agent.send_message(MessageType.MAILBOX_HANDOVER, **close)
                applied = await holder.recorder.wait_for(
                    lambda message: message.get("type") == MessageType.MAILBOX_HANDOVER_RESULT
                )
            finally:
                await close_agents(stranger, holder)
    finally:
        store.close()
    assert (refused["status"], refused["reason"]) == ("refused", "not_authorised")
    assert (applied["status"], applied["mailbox"]) == ("applied", "P/gone")
