# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — a live hub hands a mailbox to a successor or closes it, once
"""A real hub serves ``mailbox_handover`` to the name, its custodian and a granted operator."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from hub_e2e_helpers import AgentHandle, close_agents, connect_agent, running_hub
from synapse_channel.core.acl import MAILBOX_HANDOVER, MESSAGE, AclPolicy, AclRule
from synapse_channel.core.handlers import mailbox_handover as handler
from synapse_channel.core.handlers.messaging import ChatRouting, route_chat
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType

OLD = "P/old"
NEW = "P/new"
BOSS = "P/boss"


async def _request(handle: AgentHandle, **fields: Any) -> dict[str, Any]:
    """Send one handover request and return the result frame that answers it."""
    seen = len(handle.recorder.messages)
    await handle.agent.send_message(MessageType.MAILBOX_HANDOVER, **fields)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 3.0
    while loop.time() < deadline:
        for frame in handle.recorder.messages[seen:]:
            if frame.get("type") == MessageType.MAILBOX_HANDOVER_RESULT:
                return frame
        await asyncio.sleep(0.01)
    raise TimeoutError("hub returned no mailbox_handover_result")


async def _send(handle: AgentHandle, target: str, text: str) -> None:
    """Send one directed chat and wait until the sender sees it journalled back."""
    await handle.agent.send_message(MessageType.CHAT, target=target, payload=text)


def _hub(store: EventStore | None, **options: Any) -> SynapseHub:
    custodians = {"P": BOSS}
    return SynapseHub(journal=store, mailbox_custodians=custodians, **options)


async def _wait_pending(store: EventStore, count: int) -> None:
    """Wait until ``count`` directed chats to ``OLD`` are journalled."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 3.0
    while loop.time() < deadline:
        chats = [
            event
            for event in store.read_since(0, kinds=(EventKind.CHAT,))
            if event.payload.get("target") == OLD
        ]
        if len(chats) >= count:
            return
        await asyncio.sleep(0.01)
    raise TimeoutError("chats to the old mailbox were not journalled")


async def test_a_custodian_hands_pending_mail_to_a_successor_once(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(_hub(store)) as (_hub_instance, uri):
            peer = await connect_agent("X/peer", uri)
            boss = await connect_agent(BOSS, uri)
            successor = await connect_agent(NEW, uri)
            try:
                await _send(peer, OLD, "first")
                await _send(peer, "P/other", "not for old")
                await _send(peer, OLD, "second")
                await _wait_pending(store, 2)
                result = await _request(boss, **{"from": OLD, "to": NEW, "reason": "ended"})
                assert result["status"] == "applied"
                assert (result["forwarded"], result["remaining"]) == (2, False)
                assert result["mailbox"] == OLD and result["successor"] == NEW
                forwarded = await successor.recorder.wait_for(
                    lambda frame: (
                        frame.get("handover_from") == OLD and frame.get("payload") == "second"
                    )
                )
                assert forwarded["original_sender"] == "X/peer"
                assert forwarded["sender"] == BOSS
                repeat = await _request(
                    boss,
                    **{
                        "from": OLD,
                        "to": NEW,
                        "reason": "ended",
                        "through_seq": result["through_seq"],
                    },
                )
                assert repeat["status"] == "already_applied"
                again = await _request(boss, **{"from": OLD, "to": NEW, "reason": "ended"})
                assert (again["status"], again["reason"]) == ("refused", "nothing_pending")
            finally:
                await close_agents(peer, boss, successor)
        records = store.read_since(0, kinds=(EventKind.MAILBOX_HANDOVER,))
        marks = store.read_since(0, kinds=(EventKind.MAILBOX_WATERMARK,))
    finally:
        store.close()
    assert len(records) == 1
    assert records[0].payload["actor"] == BOSS
    assert len(records[0].payload["forwarded"]) == 2
    assert marks[-1].payload == {
        "identity": OLD,
        "through_seq": records[0].payload["through_seq"],
        "source": "handover",
    }


async def test_the_name_itself_closes_its_mail_and_a_stranger_is_refused(
    tmp_path: Path,
) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(_hub(store)) as (_hub_instance, uri):
            peer = await connect_agent("X/peer", uri)
            try:
                await _send(peer, OLD, "unread")
                await _send(peer, OLD, "unread too")
                await _wait_pending(store, 2)
                refused = await _request(peer, **{"from": OLD, "reason": "not mine"})
                assert (refused["status"], refused["reason"]) == ("refused", "not_authorised")
                invalid = await _request(peer, **{"from": OLD})
                assert (invalid["status"], invalid["reason"]) == ("refused", "invalid_request")
            finally:
                await close_agents(peer)
            owner = await connect_agent(OLD, uri)
            try:
                closed = await _request(owner, **{"from": OLD, "reason": "done with it"})
                assert (closed["status"], closed["forwarded"]) == ("applied", 0)
                assert closed["successor"] is None
                stale = await _request(owner, **{"from": OLD, "reason": "again", "through_seq": 1})
                assert (stale["status"], stale["reason"]) == ("refused", "nothing_pending")
            finally:
                await close_agents(owner)
        marks = store.read_since(0, kinds=(EventKind.MAILBOX_WATERMARK,))
    finally:
        store.close()
    assert marks[-1].payload["source"] == "closed"


async def test_a_granted_operator_may_act_without_a_custodian_entry(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "hub.db")
    policy = AclPolicy(
        [
            AclRule(MAILBOX_HANDOVER, "agent", "Q/*", "OPS", "mailbox operator"),
            AclRule(MESSAGE, "agent", "*", "", "anyone may message"),
        ]
    )
    try:
        async with running_hub(_hub(store, acl_policy=policy)) as (_hub_instance, uri):
            peer = await connect_agent("X/peer", uri)
            operator = await connect_agent("OPS/operator", uri)
            try:
                await _send(peer, "Q/gone", "unread")
                await _send(peer, OLD, "unread too")
                await _wait_pending(store, 1)
                applied = await _request(operator, **{"from": "Q/gone", "reason": "dormant"})
                assert applied["status"] == "applied"
                refused = await _request(operator, **{"from": OLD, "reason": "dormant"})
                assert refused["reason"] == "not_authorised"
            finally:
                await close_agents(peer, operator)
    finally:
        store.close()


async def test_a_hub_without_a_journal_has_no_mailbox_to_hand_over() -> None:
    async with running_hub(SynapseHub()) as (_hub_instance, uri):
        owner = await connect_agent(OLD, uri)
        try:
            result = await _request(owner, **{"from": OLD, "reason": "nothing"})
        finally:
            await close_agents(owner)
    assert (result["status"], result["reason"]) == ("refused", "no_journal")


async def test_one_request_forwards_at_most_the_limit_and_says_more_remains(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(handler, "MAX_FORWARDS", 1)
    store = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(_hub(store)) as (_hub_instance, uri):
            peer = await connect_agent("X/peer", uri)
            boss = await connect_agent(BOSS, uri)
            try:
                await _send(peer, OLD, "first")
                await _send(peer, OLD, "second")
                await _wait_pending(store, 2)
                part = await _request(boss, **{"from": OLD, "to": NEW, "reason": "ended"})
                assert (part["forwarded"], part["remaining"]) == (1, True)
                rest = await _request(boss, **{"from": OLD, "to": NEW, "reason": "ended"})
                assert rest["status"] == "applied" and rest["forwarded"] == 1
                assert rest["through_seq"] > part["through_seq"]
            finally:
                await close_agents(peer, boss)
    finally:
        store.close()


async def test_a_refused_forward_stops_the_run_and_covers_only_what_went(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_route = route_chat
    allowed = {"count": 1}

    async def route_once(*args: Any, **kwargs: Any) -> ChatRouting:
        if allowed["count"] == 0:
            return ChatRouting(refusal="quota")
        allowed["count"] -= 1
        return await real_route(*args, **kwargs)

    monkeypatch.setattr(handler, "route_chat", route_once)
    store = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(_hub(store)) as (_hub_instance, uri):
            peer = await connect_agent("X/peer", uri)
            boss = await connect_agent(BOSS, uri)
            try:
                await _send(peer, OLD, "first")
                await _send(peer, OLD, "second")
                await _wait_pending(store, 2)
                part = await _request(boss, **{"from": OLD, "to": NEW, "reason": "ended"})
                assert (part["status"], part["forwarded"], part["remaining"]) == (
                    "applied",
                    1,
                    True,
                )
                blocked = await _request(boss, **{"from": OLD, "to": NEW, "reason": "ended"})
                assert (blocked["status"], blocked["reason"]) == ("refused", "forward_refused")
            finally:
                await close_agents(peer, boss)
        records = store.read_since(0, kinds=(EventKind.MAILBOX_HANDOVER,))
    finally:
        store.close()
    assert len(records) == 1


def test_the_access_mapping_names_the_mailbox_and_the_successor() -> None:
    accesses = handler.handover_access({"from": OLD, "to": f" {NEW} "})
    assert [(permission, target.value) for permission, target in accesses] == [
        (MESSAGE, OLD),
        (MESSAGE, NEW),
    ]
    assert len(handler.handover_access({"from": OLD, "to": " "})) == 1
    assert len(handler.handover_access({})) == 1
