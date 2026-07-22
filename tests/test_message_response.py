# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — semantic chat-response contract tests
"""Pin semantic responses to durable chats without changing mailbox ACK."""

from __future__ import annotations

from pathlib import Path

import pytest

from hub_e2e_helpers import close_agents, connect_agent, running_hub
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind, record_chat
from synapse_channel.core.message_response import (
    SEMANTIC_RESPONSE_STATUSES,
    validate_semantic_response,
)
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType


def _chat(sender: str = "ALPHA", target: str = "BETA") -> dict[str, object]:
    return {
        "sender": sender,
        "target": target,
        "type": MessageType.CHAT,
        "payload": "question",
        "timestamp": 1.0,
        "msg_id": 1,
    }


def test_validator_accepts_ordinary_chat_and_every_closed_status(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    seq = record_chat(store, _chat())
    try:
        assert validate_semantic_response(store, _chat()) is None
        for status in SEMANTIC_RESPONSE_STATUSES:
            response = {
                **_chat(sender="operator:dash", target="ALPHA"),
                "response_to_seq": seq,
                "response_status": status,
            }
            assert validate_semantic_response(store, response) is None
    finally:
        store.close()


@pytest.mark.parametrize(
    ("fields", "reason"),
    [
        ({"response_to_seq": 1}, "needs both"),
        ({"response_status": "acknowledged"}, "needs both"),
        ({"response_to_seq": True, "response_status": "acknowledged"}, "positive integer"),
        ({"response_to_seq": 1, "response_status": "seen"}, "not recognised"),
    ],
)
def test_validator_refuses_partial_or_open_ended_shapes(
    tmp_path: Path,
    fields: dict[str, object],
    reason: str,
) -> None:
    store = EventStore(tmp_path / "events.db")
    try:
        assert reason in str(validate_semantic_response(store, {**_chat(), **fields}))
    finally:
        store.close()


def test_validator_binds_target_to_referenced_sender_and_needs_a_store(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    seq = record_chat(store, _chat())
    response = {
        **_chat(sender="operator:dash", target="WRONG"),
        "response_to_seq": seq,
        "response_status": "acknowledged",
    }
    try:
        assert "target must match" in str(validate_semantic_response(store, response))
        response["target"] = "ALPHA"
        response["response_to_seq"] = seq + 100
        assert "does not name" in str(validate_semantic_response(store, response))
        assert "requires the durable" in str(validate_semantic_response(None, response))
    finally:
        store.close()


async def test_live_hub_records_and_delivers_semantic_response_by_exact_seq(
    tmp_path: Path,
) -> None:
    store = EventStore(tmp_path / "events.db")
    async with running_hub(SynapseHub(journal=store, private_directed_messages=True)) as (_, uri):
        alpha = await connect_agent("ALPHA", uri)
        beta = await connect_agent("BETA", uri)
        try:
            await alpha.agent.chat("can you review?", target="BETA")
            original = await beta.recorder.wait_for(
                lambda message: (
                    message.get("type") == MessageType.CHAT
                    and message.get("payload") == "can you review?"
                )
            )
            message_seq = int(original["seq"])
            await beta.agent.send_message(
                MessageType.CHAT,
                target="ALPHA",
                payload="Acknowledged.",
                response_to_seq=message_seq,
                response_status="acknowledged",
            )
            response = await alpha.recorder.wait_for(
                lambda message: (
                    message.get("type") == MessageType.CHAT
                    and message.get("response_to_seq") == message_seq
                )
            )
            assert response["sender"] == "BETA"
            assert response["target"] == "ALPHA"
            assert response["response_status"] == "acknowledged"

            await beta.agent.send_message(
                MessageType.CHAT,
                target="WRONG",
                payload="Spoofed response.",
                response_to_seq=message_seq,
                response_status="completed",
            )
            refusal = await beta.recorder.wait_for(
                lambda message: (
                    message.get("type") == MessageType.ERROR
                    and "target must match" in str(message.get("payload"))
                )
            )
            assert refusal["target"] == "BETA"
        finally:
            await close_agents(alpha, beta)

    chats = [event for event in store.read_all() if event.kind == EventKind.CHAT]
    store.close()
    assert len(chats) == 2
    assert chats[1].payload["response_to_seq"] == chats[0].seq
    assert chats[1].payload["response_status"] == "acknowledged"
