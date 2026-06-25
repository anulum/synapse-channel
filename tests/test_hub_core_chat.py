# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL - tests for hub chat, presence, and unknown messages

from __future__ import annotations

from hub_helpers import FakeServerWS, _hub, _msg


async def test_chat_is_broadcast_and_recorded() -> None:
    hub = _hub()
    ws = FakeServerWS()
    await hub.register(ws)
    await hub.handle_message(_msg(sender="A", type="chat", payload="hello"), ws)

    relayed = [m for m in ws.decoded() if m.get("type") == "chat"]
    assert relayed[-1]["payload"] == "hello"
    assert relayed[-1]["hub_id"] == "syn-test"
    assert relayed[-1]["msg_id"] == 1
    assert hub.chat_history[-1]["payload"] == "hello"


async def test_chat_preserves_supplied_timestamp_and_increments_seq() -> None:
    hub = _hub()
    ws = FakeServerWS()
    await hub.register(ws)
    await hub.handle_message(_msg(sender="A", type="chat", payload="1", timestamp=1700.0), ws)
    await hub.handle_message(_msg(sender="A", type="chat", payload="2"), ws)

    chats = [m for m in ws.decoded() if m.get("type") == "chat"]
    assert chats[0]["timestamp"] == 1700.0
    assert [c["msg_id"] for c in chats] == [1, 2]


async def test_presence_broadcast_on_first_message() -> None:
    hub = _hub()
    ws = FakeServerWS()
    await hub.register(ws)
    await hub.handle_message(_msg(sender="A", type="heartbeat"), ws)
    presence = [m for m in ws.decoded() if m.get("type") == "presence_update"]
    assert presence[-1]["event"] == "joined"
    assert presence[-1]["agent"] == "A"


async def test_heartbeat_produces_no_route_reply() -> None:
    hub = _hub()
    ws = FakeServerWS()
    await hub.register(ws)
    before = len(ws.sent)
    await hub.handle_message(_msg(sender="A", type="heartbeat"), ws)
    # Only the presence-join broadcast may appear; no per-route reply follows it.
    replies = [m for m in ws.decoded()[before:] if m.get("type") not in {"presence_update"}]
    assert replies == []


async def test_unknown_type_errors_sender() -> None:
    hub = _hub()
    ws = FakeServerWS()
    await hub.register(ws)
    await hub.handle_message(_msg(sender="A", type="frobnicate"), ws)
    assert ws.last()["type"] == "error"
    assert "Unknown message type" in ws.last()["payload"]
