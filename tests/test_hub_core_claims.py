# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL - end-to-end tests for hub claims, releases, and task updates

from __future__ import annotations

from hub_e2e_helpers import close_agents, connect_agent, running_hub


async def test_claim_granted_is_broadcast_end_to_end() -> None:
    async with running_hub() as (_, uri):
        alpha = await connect_agent("ALPHA", uri)
        beta = await connect_agent("BETA", uri)
        try:
            await alpha.agent.claim("T1", note="x")
            granted = await beta.recorder.wait_for(
                lambda m: m.get("type") == "claim_granted" and m.get("task_id") == "T1"
            )
            assert granted["owner"] == "ALPHA"
        finally:
            await close_agents(alpha, beta)


async def test_claim_denied_goes_to_second_agent_end_to_end() -> None:
    async with running_hub() as (_, uri):
        alpha = await connect_agent("ALPHA", uri)
        beta = await connect_agent("BETA", uri)
        try:
            await alpha.agent.claim("T1")
            await alpha.recorder.wait_for(lambda m: m.get("type") == "claim_granted")
            await beta.agent.claim("T1")
            denied = await beta.recorder.wait_for(lambda m: m.get("type") == "claim_denied")
            assert denied["task_id"] == "T1"
        finally:
            await close_agents(alpha, beta)


async def test_claim_with_invalid_ttl_falls_back_to_default_end_to_end() -> None:
    async with running_hub() as (hub, uri):
        alpha = await connect_agent("ALPHA", uri)
        try:
            await alpha.agent.send_message("claim", task_id="T1", ttl_seconds="abc")
            await alpha.recorder.wait_for(lambda m: m.get("type") == "claim_granted")
            assert hub.state.claims["T1"].owner == "ALPHA"
        finally:
            await close_agents(alpha)


async def test_claim_with_numeric_ttl_is_used_end_to_end() -> None:
    async with running_hub() as (hub, uri):
        alpha = await connect_agent("ALPHA", uri)
        try:
            await alpha.agent.claim("T1", ttl_seconds=120)
            await alpha.recorder.wait_for(lambda m: m.get("type") == "claim_granted")
            assert "T1" in hub.state.claims
        finally:
            await close_agents(alpha)


async def test_release_granted_and_denied_end_to_end() -> None:
    async with running_hub() as (_, uri):
        alpha = await connect_agent("ALPHA", uri)
        try:
            await alpha.agent.claim("T1")
            await alpha.recorder.wait_for(lambda m: m.get("type") == "claim_granted")
            await alpha.agent.release("T1")
            await alpha.recorder.wait_for(lambda m: m.get("type") == "release_granted")
            await alpha.agent.release("GHOST")
            await alpha.recorder.wait_for(lambda m: m.get("type") == "release_denied")
        finally:
            await close_agents(alpha)


async def test_task_update_success_is_broadcast_end_to_end() -> None:
    async with running_hub() as (_, uri):
        alpha = await connect_agent("ALPHA", uri)
        beta = await connect_agent("BETA", uri)
        try:
            await alpha.agent.claim("T1")
            await alpha.recorder.wait_for(lambda m: m.get("type") == "claim_granted")
            await alpha.agent.update_task("T1", status="working", data_ref="r")
            updated = await beta.recorder.wait_for(lambda m: m.get("type") == "task_updated")
            assert updated["status"] == "working"
            assert updated["data_ref"] == "r"
            assert updated["version"] == 1
        finally:
            await close_agents(alpha, beta)


async def test_task_update_failure_errors_sender_end_to_end() -> None:
    async with running_hub() as (_, uri):
        alpha = await connect_agent("ALPHA", uri)
        try:
            await alpha.agent.update_task("MISSING")
            await alpha.recorder.wait_for(lambda m: m.get("type") == "error")
        finally:
            await close_agents(alpha)
