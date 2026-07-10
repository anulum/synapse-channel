# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — live hub mailbox pending-count tests

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

IDENTITY = "PROJ/A"


async def _connect_mailbox_sidecar(uri: str) -> AgentHandle:
    """Connect the real mailbox-enabled ``-rx`` client."""
    recorder = Recorder()
    agent = SynapseAgent(
        f"{IDENTITY}-rx",
        recorder,
        uri=uri,
        heartbeat_interval=60.0,
        verbose=False,
        mailbox=True,
        mailbox_for=IDENTITY,
    )
    task = asyncio.create_task(agent.connect())
    handle = AgentHandle(agent=agent, recorder=recorder, task=task)
    if not await agent.wait_until_ready(3.0):
        await handle.close()
        raise TimeoutError("mailbox sidecar did not receive the hub welcome")
    return handle


async def _who_count(handle: AgentHandle, expected: int) -> dict[str, Any]:
    """Request WHO and wait for the expected identity count."""
    await handle.agent.request_who()
    return await handle.recorder.wait_for(
        lambda message: (
            message.get("type") == MessageType.WHO_SNAPSHOT
            and isinstance(message.get("mailbox_pending"), dict)
            and message["mailbox_pending"].get(IDENTITY) == expected
        )
    )


async def test_offline_pending_replay_ack_and_restart_projection(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(SynapseHub(journal=store)) as (_hub, uri):
            sender = await connect_agent("PEER", uri)
            sidecar: AgentHandle | None = None
            try:
                await sender.agent.send_message(
                    MessageType.CHAT,
                    target=IDENTITY,
                    payload="urgent",
                    receipt_requested=True,
                )
                immediate = await sender.recorder.wait_for(
                    lambda message: (
                        message.get("type") == MessageType.DELIVERY_RECEIPT
                        and message.get("delivered") is False
                    )
                )
                assert immediate["delivered"] is False
                await _who_count(sender, 1)

                sidecar = await _connect_mailbox_sidecar(uri)
                replayed = await sidecar.recorder.wait_for(
                    lambda message: (
                        message.get("type") == MessageType.CHAT
                        and message.get("payload") == "urgent"
                    )
                )
                assert replayed["replayed"] is True
                deferred = await sender.recorder.wait_for(
                    lambda message: (
                        message.get("type") == MessageType.DELIVERY_RECEIPT
                        and message.get("deferred") is True
                    )
                )
                assert deferred["recipients"] == [IDENTITY]
                await _who_count(sender, 0)
            finally:
                if sidecar is not None:
                    await close_agents(sidecar)
                await close_agents(sender)

        async with running_hub(SynapseHub(journal=store)) as (_restarted, uri):
            observer = await connect_agent("OBSERVER", uri)
            try:
                snapshot = await _who_count(observer, 0)
            finally:
                await close_agents(observer)

        watermarks = store.read_since(0, kinds=(EventKind.MAILBOX_WATERMARK,))
    finally:
        store.close()

    assert snapshot["mailbox_pending"][IDENTITY] == 0
    assert watermarks[-1].payload["identity"] == IDENTITY
    assert watermarks[-1].payload["source"] == "ack"


async def test_live_mailbox_delivery_is_acknowledged_without_model_claim(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(SynapseHub(journal=store)) as (_hub, uri):
            sidecar = await _connect_mailbox_sidecar(uri)
            sender = await connect_agent("PEER", uri)
            try:
                await sender.agent.chat("live", target=IDENTITY)
                await sidecar.recorder.wait_for(
                    lambda message: (
                        message.get("type") == MessageType.CHAT and message.get("payload") == "live"
                    )
                )
                snapshot = await _who_count(sender, 0)
            finally:
                await close_agents(sender, sidecar)
    finally:
        store.close()

    assert snapshot["mailbox_pending"][IDENTITY] == 0


async def test_who_reports_pending_projection_unavailable_without_journal() -> None:
    async with running_hub(SynapseHub()) as (_hub, uri):
        observer = await connect_agent("OBSERVER", uri)
        try:
            await observer.agent.request_who()
            snapshot = await observer.recorder.wait_for(
                lambda message: message.get("type") == MessageType.WHO_SNAPSHOT
            )
        finally:
            await close_agents(observer)

    assert "mailbox_pending" in snapshot
    assert snapshot["mailbox_pending"] is None
