# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — chat delivery public runtime journeys

from __future__ import annotations

from pathlib import Path

import pytest

from hub_e2e_helpers import close_agents, connect_agent, running_hub
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore


@pytest.mark.parametrize("private", [False, True])
async def test_receipted_delivery_pins_history_journal_retries_and_private_audience(
    tmp_path: Path, private: bool
) -> None:
    store = EventStore(tmp_path / "chat.db")
    hub = SynapseHub(
        journal=store,
        private_directed_messages=private,
        max_history=2,
        dead_letter_escalation_threshold=2,
    )
    try:
        async with running_hub(hub) as (_, uri):
            sender = await connect_agent("DELIVERY/sender", uri)
            receiver = await connect_agent("DELIVERY/receiver", uri)
            other = await connect_agent("DELIVERY/other", uri)
            try:
                await sender.agent.send_message(
                    "chat",
                    target=receiver.agent.name,
                    payload="one accepted delivery",
                    client_msg_id="delivery-1",
                    receipt_requested=True,
                )
                received = await receiver.recorder.wait_for(
                    lambda m: m.get("client_msg_id") == "delivery-1"
                )
                receipt = await sender.recorder.wait_for(
                    lambda m: (
                        m.get("type") == "delivery_receipt"
                        and m.get("client_msg_id") == "delivery-1"
                    )
                )
                assert receipt["delivered"] is True and receipt["message_seq"] == received["seq"]
                assert "seq" not in hub.chat_history[0]
                if private:
                    with pytest.raises(TimeoutError):
                        await other.recorder.wait_for(
                            lambda m: m.get("client_msg_id") == "delivery-1", timeout=0.1
                        )
                else:
                    await other.recorder.wait_for(lambda m: m.get("client_msg_id") == "delivery-1")
                await sender.agent.send_message(
                    "chat",
                    target=receiver.agent.name,
                    payload="one accepted delivery",
                    client_msg_id="delivery-1",
                    receipt_requested=True,
                )
                await sender.recorder.wait_for(lambda m: m.get("duplicate") is True)
                assert len([e for e in store.read_all() if e.kind == EventKind.CHAT]) == 1
                await sender.agent.send_message(
                    "chat",
                    target="DELIVERY/offline",
                    payload="retained while offline",
                    client_msg_id="offline-1",
                    receipt_requested=True,
                )
                negative = await sender.recorder.wait_for(
                    lambda m: (
                        m.get("type") == "delivery_receipt"
                        and m.get("client_msg_id") == "offline-1"
                    )
                )
                assert negative["delivered"] is False and len(hub.chat_history) == 2
                assert hub.counters.chat_duplicates_suppressed == 1
                assert not any(e.kind == EventKind.DEAD_LETTER_ESCALATION for e in store.read_all())
            finally:
                await close_agents(sender, receiver, other)
    finally:
        store.close()
