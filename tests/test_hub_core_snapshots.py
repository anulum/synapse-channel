# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL - end-to-end tests for hub state, who, and history snapshots

from __future__ import annotations

from websockets.asyncio.client import connect

from hub_e2e_helpers import (
    close_agents,
    connect_agent,
    read_until_type,
    running_hub,
    send_json,
)


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
    from synapse_channel import __version__

    async with running_hub() as (_, uri):
        alpha = await connect_agent("ALPHA", uri)
        try:
            await alpha.agent.request_who()
            snap = await alpha.recorder.wait_for(lambda m: m.get("type") == "who_snapshot")
            assert snap["online_agents"] == ["ALPHA"]
            assert snap["connected_clients"] == 1
            # The roster response carries the hub's pinning tag for a cockpit.
            assert snap["hub_version"] == __version__
            assert "config_epoch" in snap
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


async def test_history_request_rejects_overflow_and_keeps_the_socket() -> None:
    """Reject exponent overflow, then serve a valid request on the same socket."""
    async with running_hub() as (_hub, uri):
        async with connect(uri) as ws:
            await read_until_type(ws, "welcome")
            await send_json(ws, sender="ALPHA", type="chat", target="all", payload="x")
            await read_until_type(ws, "chat")
            await ws.send('{"sender": "ALPHA", "type": "history_request", "limit": 1e400}')
            error = await read_until_type(ws, "error")
            assert error["payload"] == "Malformed JSON."
            await send_json(ws, sender="ALPHA", type="history_request")
            snap = await read_until_type(ws, "history_snapshot")
    assert snap["requested_limit"] == "all"
    assert len(snap["history"]) == 1


async def test_resume_request_rejects_overflow_and_keeps_the_socket() -> None:
    """Reject an overflowing cursor, then accept the explicit reset cursor."""
    async with running_hub() as (_hub, uri):
        async with connect(uri) as ws:
            await read_until_type(ws, "welcome")
            await send_json(ws, sender="ALPHA", type="chat", target="all", payload="x")
            await read_until_type(ws, "chat")
            await ws.send('{"sender": "ALPHA", "type": "resume_request", "since": 1e400}')
            error = await read_until_type(ws, "error")
            assert error["payload"] == "Malformed JSON."
            await send_json(ws, sender="ALPHA", type="resume_request", since=0)
            snap = await read_until_type(ws, "resume_snapshot")
    assert snap["since"] == 0
    assert len(snap["messages"]) == 1
