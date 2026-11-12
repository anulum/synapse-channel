# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — a successor receives a mailbox and none of its predecessor's authority
"""Succession moves mail, not authority, on a real hub.

A custodian hands a departed seat's pending mail to a successor. The successor
receives that mail. It does not answer to the predecessor's role, because a
role grant names identities and not mailboxes, and it cannot release the
predecessor's claim, which stays the predecessor's.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from hub_e2e_helpers import AgentHandle, Recorder, close_agents, connect_agent, running_hub
from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType
from synapse_channel.core.role_grants import RoleGrants

OLD = "P/old"
NEW = "P/new"
BOSS = "P/boss"
PEER = "X/peer"
ROLE = "P/coordinator"
TASK = "T-succession"
MAIL = "for whoever carries on"


async def _connect_declaring_role(name: str, uri: str) -> AgentHandle:
    """Connect ``name`` with ``ROLE`` declared on its registration heartbeat."""
    recorder = Recorder()
    agent = SynapseAgent(
        name, recorder, uri=uri, heartbeat_interval=60.0, verbose=False, roles=(ROLE,)
    )
    handle = AgentHandle(agent=agent, recorder=recorder, task=asyncio.create_task(agent.connect()))
    assert await agent.wait_until_ready(3.0), f"{name} did not receive the hub welcome"
    return handle


async def _next_frame(
    handle: AgentHandle, msg_types: tuple[str, ...], request: Callable[[], Awaitable[None]]
) -> dict[str, Any]:
    """Run ``request`` and return the first frame of ``msg_types`` that arrives after it."""
    earlier = {id(frame) for frame in handle.recorder.messages}
    await request()
    return await handle.recorder.wait_for(
        lambda frame: frame.get("type") in msg_types and id(frame) not in earlier
    )


async def _bound_roles(handle: AgentHandle) -> dict[str, Any]:
    """Return the role bindings of a WHO snapshot requested now."""
    snapshot = await _next_frame(handle, (MessageType.WHO_SNAPSHOT,), handle.agent.request_who)
    roles = snapshot["agent_roles"]
    assert isinstance(roles, dict)
    return roles


async def test_a_successor_gets_the_predecessors_mail_and_none_of_its_authority(
    tmp_path: Path,
) -> None:
    """The handover delivers the mail; the role and the claim stay with the predecessor."""
    store = EventStore(tmp_path / "hub.db")
    hub = SynapseHub(
        journal=store,
        mailbox_custodians={"P": BOSS},
        role_grants=RoleGrants({ROLE: frozenset({OLD})}),
        require_role_claim=True,
    )
    try:
        async with running_hub(hub) as (live, uri):
            old = await _connect_declaring_role(OLD, uri)
            try:
                assert (await _bound_roles(old)).get(OLD) == [ROLE]
                await old.agent.claim(TASK, note="the predecessor's work")
                await old.recorder.wait_for(
                    lambda frame: (
                        frame.get("type") == MessageType.CLAIM_GRANTED
                        and frame.get("task_id") == TASK
                    )
                )
            finally:
                await close_agents(old)

            peer = await connect_agent(PEER, uri)
            boss = await connect_agent(BOSS, uri)
            new = await _connect_declaring_role(NEW, uri)
            try:
                receipt = await _next_frame(
                    peer,
                    (MessageType.DELIVERY_RECEIPT,),
                    lambda: peer.agent.send_message(
                        MessageType.CHAT,
                        target=OLD,
                        payload=MAIL,
                        client_msg_id="succession-1",
                        receipt_requested=True,
                    ),
                )
                assert receipt["delivered"] is False

                handover: dict[str, Any] = {"from": OLD, "to": NEW, "reason": "seat ended"}
                handed = await _next_frame(
                    boss,
                    (MessageType.MAILBOX_HANDOVER_RESULT,),
                    lambda: boss.agent.send_message(MessageType.MAILBOX_HANDOVER, **handover),
                )
                assert (handed["status"], handed["forwarded"]) == ("applied", 1)
                mail = await new.recorder.wait_for(
                    lambda frame: frame.get("handover_from") == OLD and frame.get("payload") == MAIL
                )
                assert mail["original_sender"] == PEER

                assert NEW not in await _bound_roles(new)

                answer = await _next_frame(
                    new,
                    (MessageType.RELEASE_DENIED, MessageType.RELEASE_GRANTED),
                    lambda: new.agent.release(TASK),
                )
                assert answer["type"] == MessageType.RELEASE_DENIED
                assert answer["payload"] == f"Task '{TASK}' is owned by {OLD}, not {NEW}."
                claim = live.state.claims.get(TASK)
                assert claim is not None and claim.owner == OLD
            finally:
                await close_agents(peer, boss, new)
    finally:
        store.close()
