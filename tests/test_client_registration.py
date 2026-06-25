# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real-hub tests for client registration options

from __future__ import annotations

import asyncio
import contextlib

from hub_e2e_helpers import close_agents, connect_agent, running_hub
from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.auth import TokenAuthenticator
from synapse_channel.core.hub import SynapseHub


async def test_connect_sends_token_on_registration_to_secured_hub() -> None:
    hub = SynapseHub(authenticator=TokenAuthenticator(["s3cret"]))
    async with running_hub(hub) as (_, uri):
        handle = await connect_agent("A", uri, token="s3cret", wait_presence=False)
        try:
            assert handle.agent.ready_event.is_set()
            assert "A" in hub.online_agents()
        finally:
            await close_agents(handle)


async def test_connect_without_token_is_rejected_by_secured_hub() -> None:
    hub = SynapseHub(authenticator=TokenAuthenticator(["s3cret"]), auth_timeout=0.5)
    async with running_hub(hub) as (_, uri):
        agent = SynapseAgent("A", uri=uri, verbose=False)
        task = asyncio.create_task(agent.connect())
        try:
            assert await agent.wait_until_ready(timeout=0.5) is False
        finally:
            agent.running = False
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    assert "A" not in hub.online_agents()


async def test_registration_takeover_reclaims_stale_identity() -> None:
    hub = SynapseHub(takeover_cooldown=0.0)
    async with running_hub(hub) as (_, uri):
        first = await connect_agent("A-rx", uri, wait_presence=False)
        second = await connect_agent("A-rx", uri, takeover=True, wait_presence=False)
        try:
            for _ in range(20):
                if first.agent.connection is None:
                    break
                await asyncio.sleep(0.01)
            assert first.agent.connection is None
            assert second.agent.ready_event.is_set()
            assert hub.online_agents() == ["A-rx"]
        finally:
            await close_agents(second, first)


async def test_registration_duplicate_without_takeover_does_not_replace_owner() -> None:
    hub = SynapseHub()
    async with running_hub(hub) as (_, uri):
        owner = await connect_agent("A", uri, wait_presence=False)
        intruder = SynapseAgent("A", uri=uri, verbose=False)
        task = asyncio.create_task(intruder.connect())
        try:
            for _ in range(20):
                if intruder.connection is None and not intruder.running:
                    break
                await asyncio.sleep(0.01)
            assert hub.online_agents() == ["A"]
            assert owner.agent.connection is not None
            assert intruder.connection is None
        finally:
            intruder.running = False
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            await close_agents(owner)
