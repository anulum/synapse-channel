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
from pathlib import Path
from typing import Any

from hub_e2e_helpers import AgentHandle, Recorder, close_agents, connect_agent, running_hub
from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind
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
    if not await agent.wait_until_ready(3.0):
        await handle.close()
        raise TimeoutError(f"{name} did not receive the hub welcome")
    return handle


async def _bound_roles(handle: AgentHandle) -> dict[str, Any]:
    """Return the role bindings of a WHO snapshot requested after this call."""
    seen = len(handle.recorder.messages)
    await handle.agent.request_who()
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 3.0
    while loop.time() < deadline:
        for frame in handle.recorder.messages[seen:]:
            if frame.get("type") == MessageType.WHO_SNAPSHOT:
                roles = frame["agent_roles"]
                assert isinstance(roles, dict)
                return roles
        await asyncio.sleep(0.01)
    raise TimeoutError("the hub sent no WHO snapshot")


async def _hand_over(handle: AgentHandle, **fields: Any) -> dict[str, Any]:
    """Send one mailbox handover and return the result frame that answers it."""
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


async def _journalled_to_old(store: EventStore) -> None:
    """Wait until the directed chat to ``OLD`` is in the journal."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 3.0
    while loop.time() < deadline:
        for event in store.read_since(0, kinds=(EventKind.CHAT,)):
            if event.payload.get("target") == OLD and event.payload.get("payload") == MAIL:
                return
        await asyncio.sleep(0.01)
    raise TimeoutError("the chat to the departed seat was not journalled")


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
                await peer.agent.send_message(MessageType.CHAT, target=OLD, payload=MAIL)
                await _journalled_to_old(store)
                handed = await _hand_over(boss, **{"from": OLD, "to": NEW, "reason": "seat ended"})
                assert (handed["status"], handed["forwarded"]) == ("applied", 1)
                mail = await new.recorder.wait_for(
                    lambda frame: frame.get("handover_from") == OLD and frame.get("payload") == MAIL
                )
                assert mail["original_sender"] == PEER

                roles = await _bound_roles(new)
                assert NEW not in roles

                await new.agent.release(TASK)
                answer = await new.recorder.wait_for(
                    lambda frame: (
                        frame.get("type")
                        in (MessageType.RELEASE_DENIED, MessageType.RELEASE_GRANTED)
                    )
                )
                assert answer["type"] == MessageType.RELEASE_DENIED
                assert answer["payload"] == f"Task '{TASK}' is owned by {OLD}, not {NEW}."
                claim = live.state.claims.get(TASK)
                assert claim is not None and claim.owner == OLD
            finally:
                await close_agents(peer, boss, new)
    finally:
        store.close()
