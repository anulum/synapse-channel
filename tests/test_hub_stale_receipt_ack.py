# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — stale-socket receipt ordering and ACK settlement regression
"""A stale live socket cannot ACK before its negative receipt becomes pending."""

from __future__ import annotations

import json
from pathlib import Path

from websockets.asyncio.client import connect

from hub_e2e_helpers import read_until_type, running_hub
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind
from synapse_channel.core.pending_receipts import PendingReceipts
from synapse_channel.core.persistence import EventStore


async def test_stale_live_ack_follows_and_settles_the_immediate_negative_receipt(
    tmp_path: Path,
) -> None:
    now = [0.0]
    store = EventStore(tmp_path / "events.db")
    hub = SynapseHub(
        journal=store,
        recipient_liveness_window=1.0,
        clock=lambda: now[0],
        private_directed_messages=True,
    )

    async with running_hub(hub) as (_, uri):
        async with connect(uri) as bob, connect(uri) as alice:
            await read_until_type(bob, "welcome")
            await read_until_type(alice, "welcome")
            await bob.send(
                json.dumps(
                    {
                        "sender": "BOB",
                        "type": "heartbeat",
                        "target": "System",
                        "payload": "online",
                        "mailbox": True,
                        "since_seq": 0,
                    }
                )
            )
            await read_until_type(bob, "presence_update")
            now[0] = 2.0

            await alice.send(
                json.dumps(
                    {
                        "sender": "ALICE",
                        "type": "chat",
                        "target": "BOB",
                        "payload": "urgent",
                        "receipt_requested": True,
                    }
                )
            )
            live_frame = await read_until_type(bob, "chat")
            await bob.send(
                json.dumps(
                    {
                        "sender": "BOB",
                        "type": "ack",
                        "seq": live_frame["seq"],
                    }
                )
            )
            immediate = await read_until_type(alice, "delivery_receipt")
            deferred = await read_until_type(alice, "delivery_receipt")

    events = [event.kind for event in store.read_all() if "delivery_receipt" in event.kind]
    store.close()
    assert immediate["delivered"] is False
    assert immediate["reason"] == "no_live_recipient"
    assert deferred["delivered"] is True
    assert deferred["deferred"] is True
    assert len(hub.pending_receipts) == 0
    assert events == [
        EventKind.DELIVERY_RECEIPT_REQUESTED,
        EventKind.DELIVERY_RECEIPT_IMMEDIATE,
        EventKind.DELIVERY_RECEIPT_DEFERRED,
    ]


async def test_pending_window_eviction_remains_durably_audited(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    hub = SynapseHub(journal=store)
    hub.pending_receipts = PendingReceipts(max_entries=1)

    async with running_hub(hub) as (_, uri):
        async with connect(uri) as alice:
            await read_until_type(alice, "welcome")
            for target in ("BOB", "CAROL"):
                await alice.send(
                    json.dumps(
                        {
                            "sender": "ALICE",
                            "type": "chat",
                            "target": target,
                            "payload": "urgent",
                            "receipt_requested": True,
                        }
                    )
                )
                assert (await read_until_type(alice, "delivery_receipt"))["delivered"] is False

    expired = [
        event for event in store.read_all() if event.kind == EventKind.DELIVERY_RECEIPT_EXPIRED
    ]
    store.close()
    assert len(hub.pending_receipts) == 1
    assert len(expired) == 1
    assert expired[0].payload["reason"] == "pending_window_evicted"
    assert expired[0].payload["target"] == "BOB"
