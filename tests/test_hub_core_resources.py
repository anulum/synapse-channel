# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL - end-to-end tests for hub resource offers

from __future__ import annotations

from hub_e2e_helpers import close_agents, connect_agent, running_hub
from synapse_channel.core.state import MAX_OFFERS_PER_AGENT


async def test_resource_offer_is_broadcast_end_to_end() -> None:
    async with running_hub() as (_, uri):
        alpha = await connect_agent("ALPHA", uri)
        beta = await connect_agent("BETA", uri)
        try:
            await alpha.agent.send_message("resource", kind="llm", name="m", capacity=2)
            offered = await beta.recorder.wait_for(lambda m: m.get("type") == "resource_offered")
            assert offered["name"] == "m"
            assert offered["key"] == "ALPHA:llm:m"
        finally:
            await close_agents(alpha, beta)


async def test_resource_offer_missing_fields_errors_end_to_end() -> None:
    async with running_hub() as (_, uri):
        alpha = await connect_agent("ALPHA", uri)
        try:
            await alpha.agent.send_message("resource", kind="llm")
            error = await alpha.recorder.wait_for(lambda m: m.get("type") == "error")
            assert "kind+name" in error["payload"]
        finally:
            await close_agents(alpha)


async def test_resource_offer_quota_is_enforced_end_to_end() -> None:
    async with running_hub() as (_, uri):
        alpha = await connect_agent("ALPHA", uri)
        try:
            for index in range(MAX_OFFERS_PER_AGENT):
                await alpha.agent.send_message("resource", kind="llm", name=f"m{index}")
            await alpha.agent.send_message("resource", kind="llm", name="overflow")
            error = await alpha.recorder.wait_for(
                lambda m: m.get("type") == "error" and "quota" in str(m.get("payload"))
            )
            assert "quota" in error["payload"]
        finally:
            await close_agents(alpha)
