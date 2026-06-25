# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL - end-to-end tests for hub state, who, and history snapshots

from __future__ import annotations

from hub_e2e_helpers import close_agents, connect_agent, running_hub


async def test_state_request_returns_snapshot_end_to_end() -> None:
    async with running_hub() as (_, uri):
        alpha = await connect_agent("ALPHA", uri)
        try:
            await alpha.agent.claim("T1")
            await alpha.recorder.wait_for(lambda m: m.get("type") == "claim_granted")
            await alpha.agent.request_state()
            snapshot = await alpha.recorder.wait_for(lambda m: m.get("type") == "state_snapshot")
            assert snapshot["snapshot"]["active_claims"][0]["task_id"] == "T1"
        finally:
            await close_agents(alpha)


async def test_who_request_returns_roster_end_to_end() -> None:
    async with running_hub() as (_, uri):
        alpha = await connect_agent("ALPHA", uri)
        try:
            await alpha.agent.request_who()
            snap = await alpha.recorder.wait_for(lambda m: m.get("type") == "who_snapshot")
            assert snap["online_agents"] == ["ALPHA"]
            assert snap["connected_clients"] == 1
        finally:
            await close_agents(alpha)


async def test_history_request_variants_end_to_end() -> None:
    async with running_hub() as (_, uri):
        alpha = await connect_agent("ALPHA", uri)
        try:
            for index in range(3):
                await alpha.agent.chat(str(index), target="all")
            await alpha.agent.request_history(limit=2)
            limited = await alpha.recorder.wait_for(
                lambda m: m.get("type") == "history_snapshot" and m.get("requested_limit") == 2
            )
            assert len(limited["history"]) == 2
            await alpha.agent.request_history(limit=None)
            all_history = await alpha.recorder.wait_for(
                lambda m: m.get("type") == "history_snapshot" and m.get("requested_limit") == "all"
            )
            assert len(all_history["history"]) == 3
            await alpha.agent.send_message("history_request", limit="bad")
            fallback = await alpha.recorder.wait_for(
                lambda m: m.get("type") == "history_snapshot" and m.get("requested_limit") == "all"
            )
            assert len(fallback["history"]) == 3
        finally:
            await close_agents(alpha)
