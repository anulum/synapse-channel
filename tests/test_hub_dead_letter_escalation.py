# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — end-to-end tests for dead-letter blackhole escalation

from __future__ import annotations

import json
from pathlib import Path

from websockets.asyncio.client import ClientConnection, connect

from hub_e2e_helpers import read_until_type, running_hub
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore


async def _dead_letter(websocket: ClientConnection, *, times: int) -> None:
    """Send ``times`` directed chats to a name with no live connection, fully draining each.

    Each message requests a delivery receipt and the receipt is drained before the next send. The
    receipt is the last thing the chat handler emits — after the chat broadcast and after any
    escalation it triggers — so draining it guarantees the escalation (if any) has been journalled
    before the test inspects the store, with no fire-and-forget race.
    """
    for index in range(times):
        await websocket.send(
            json.dumps(
                {
                    "sender": "ALPHA",
                    "type": "chat",
                    "target": "MISSING",
                    "payload": f"m{index}",
                    "receipt_requested": True,
                }
            )
        )
        await read_until_type(websocket, "delivery_receipt")


def _escalations(store: EventStore) -> list[dict[str, object]]:
    """Return the dead-letter escalation audit payloads in the log, in order."""
    return [e.payload for e in store.read_all() if e.kind == EventKind.DEAD_LETTER_ESCALATION]


async def test_escalation_fires_at_the_threshold_and_is_audited(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    hub = SynapseHub(hub_id="syn-esc", journal=store, dead_letter_escalation_threshold=2)
    async with running_hub(hub) as (_, uri):
        async with connect(uri) as websocket:
            await read_until_type(websocket, "welcome")
            await _dead_letter(websocket, times=2)  # the 2nd crosses the threshold

    audits = _escalations(store)
    store.close()
    assert audits == [{"target": "MISSING", "count": 2, "last_sender": "ALPHA", "threshold": 2}]


async def test_escalation_repeats_on_each_further_multiple(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    hub = SynapseHub(hub_id="syn-esc", journal=store, dead_letter_escalation_threshold=2)
    async with running_hub(hub) as (_, uri):
        async with connect(uri) as websocket:
            await read_until_type(websocket, "welcome")
            await _dead_letter(websocket, times=4)  # crosses at 2 and again at 4

    assert [audit["count"] for audit in _escalations(store)] == [2, 4]  # not on messages 1 and 3
    store.close()


async def test_below_threshold_does_not_escalate(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    hub = SynapseHub(hub_id="syn-esc", journal=store, dead_letter_escalation_threshold=5)
    async with running_hub(hub) as (_, uri):
        async with connect(uri) as websocket:
            await read_until_type(websocket, "welcome")
            await _dead_letter(websocket, times=4)  # never reaches 5

    assert _escalations(store) == []
    store.close()
    # The ledger still recorded the blackhole for the passive snapshot.
    assert hub.dead_letters.snapshot()[0]["count"] == 4


async def test_disabled_by_default_never_escalates(tmp_path: Path) -> None:
    # The default threshold of 0 leaves the ledger's passive visibility untouched.
    store = EventStore(tmp_path / "events.db")
    hub = SynapseHub(hub_id="syn-esc", journal=store)
    assert hub.dead_letter_escalation_threshold == 0
    async with running_hub(hub) as (_, uri):
        async with connect(uri) as websocket:
            await read_until_type(websocket, "welcome")
            await _dead_letter(websocket, times=6)

    assert _escalations(store) == []
    store.close()


async def test_escalation_is_broadcast_to_connected_sockets() -> None:
    # threshold 1 escalates on the first dead letter; the notice reaches the connected socket.
    hub = SynapseHub(hub_id="syn-esc", dead_letter_escalation_threshold=1)
    async with running_hub(hub) as (_, uri):
        async with connect(uri) as websocket:
            await read_until_type(websocket, "welcome")
            await websocket.send(
                json.dumps({"sender": "ALPHA", "type": "chat", "target": "MISSING", "payload": "x"})
            )
            escalation = await read_until_type(websocket, "dead_letter_escalation")

    assert escalation["escalation_target"] == "MISSING"
    assert escalation["escalation_count"] == 1
    assert escalation["last_sender"] == "ALPHA"
    assert "no live connection" in escalation["payload"]
