# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — a task postmortem attributes a chat to its hub-bound sender only
"""A chat frame that names someone else in ``from``, ``author`` or ``owner`` is still its sender's.

The hub binds a chat's ``sender`` to the connection and journals the frame's other
fields as they arrived. A postmortem that read those fields would let one seat
answer for another: a forged "reply" from the task owner would hide a message the
owner never answered, and would enter the task timeline under the owner's name.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from websockets.asyncio.client import connect

from hub_e2e_helpers import close_agents, collect_available, connect_agent, running_hub, send_json
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.postmortem import TaskPostmortem, build_task_postmortem
from synapse_channel.core.protocol import MessageType

OWNER = "LAB/alpha"
LEAD = "LAB/lead"
FORGER = "LAB/mallory"


async def _received(seat: Any, sender: str, payload: str) -> None:
    def arrived(message: dict[str, Any]) -> bool:
        return (
            message.get("type") == "chat"
            and message.get("sender") == sender
            and message.get("payload") == payload
        )

    await seat.recorder.wait_for(arrived)


async def _forge_owner_reply(uri: str) -> None:
    """Send a chat to the lead that claims, in every actor field, to come from the owner."""
    async with connect(uri) as websocket:
        await send_json(websocket, type="heartbeat", sender=FORGER, target="System")
        await collect_available(websocket, 0.3)
        await send_json(
            websocket,
            type=MessageType.CHAT,
            sender=FORGER,
            target=LEAD,
            payload="T1 done",
            **{"from": OWNER, "author": OWNER, "owner": OWNER},
        )
        await collect_available(websocket, 0.3)


def _postmortem(store: EventStore) -> TaskPostmortem:
    return build_task_postmortem("T1", tuple(store.iter_events()))


def _chat_rows(report: TaskPostmortem) -> list[tuple[str, str]]:
    return [(entry.actor, entry.text) for entry in report.timeline if entry.kind == EventKind.CHAT]


async def test_a_forged_owner_reply_neither_answers_nor_enters_the_timeline(
    tmp_path: Path,
) -> None:
    """Only the owner's own reply answers the lead; the forgery stays the forger's."""
    store = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(SynapseHub(journal=store)) as (_hub, uri):
            owner = await connect_agent(OWNER, uri)
            lead = await connect_agent(LEAD, uri)
            try:
                await owner.agent.claim("T1")
                await owner.recorder.wait_for(
                    lambda message: message.get("type") == "claim_granted"
                )
                await lead.agent.chat("T1 status?", target=OWNER)
                await _received(owner, LEAD, "T1 status?")

                await _forge_owner_reply(uri)
                await _received(lead, FORGER, "T1 done")
                forged = _postmortem(store)

                await owner.agent.chat("T1 done", target=LEAD)
                await _received(lead, OWNER, "T1 done")
            finally:
                await close_agents(owner, lead)
        answered = _postmortem(store)
        journalled = [
            event.payload for event in store.iter_events() if event.kind == EventKind.CHAT
        ]
    finally:
        store.close()

    # The forged frame's actor fields were journalled as it sent them.
    claimed = [row for row in journalled if row["sender"] == FORGER]
    assert [(row["from"], row["author"], row["owner"]) for row in claimed] == [
        (OWNER, OWNER, OWNER)
    ]

    # The forgery answers nothing and is not the owner's in the timeline.
    assert forged.owners == (OWNER,)
    assert [(m.sender, m.target, m.payload) for m in forged.unanswered_messages] == [
        (LEAD, OWNER, "T1 status?")
    ]
    assert _chat_rows(forged) == [(LEAD, "T1 status?")]

    # The owner's own reply answers the lead and enters the timeline under the owner.
    assert answered.unanswered_messages == ()
    assert _chat_rows(answered) == [(LEAD, "T1 status?"), (OWNER, "T1 done")]
