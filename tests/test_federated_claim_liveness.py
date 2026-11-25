# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — a forwarded claim lives as long as the peer hub that forwarded it
"""A forwarded claim lives as long as its forwarding peer, not its never-connected claimant.

The owner of a namespace grants claims that a peer hub forwards for its own agents. Such a
claimant is connected to the peer, never to the owner, so the owner cannot judge the claim by
the claimant's socket: it would count the claimant as offline from the start and release a live
claim after one lease window. The owner judges a forwarded claim by the forwarding peer's
connection instead. The claim stands while the peer is connected, and it is released only after
the peer has been away for the whole window, exactly as a local holder's claim is.

Every test runs a real owner hub, a real peer connection and, where a sweep or a state view is
needed, a real local agent, so the forward, the disconnect, the restart and the view are all
exercised through the hub's public paths.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from websockets.asyncio.client import ClientConnection

from hub_e2e_helpers import AgentHandle, connect_agent, read_until_type, running_hub, send_json
from synapse_channel.core.claim_holder_presence import HOLDER_OFFLINE_ACTOR
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.multihub_claim_wire import (
    ClaimForwardRequest,
    decode_claim_forward_result,
    encode_claim_forward_request,
)
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType
from test_federation_serving_binds_the_object import _A, _claim, _owner, _policy
from test_hub_operator_relay import _connect, _write_peer_cert

_WINDOW = 0.4
_PEER = "peer"


def _forwarded(task_id: str) -> ClaimForwardRequest:
    """Return a claim on ``task_id`` that the peer forwards for its agent ``ALPHA/alice``."""
    return _claim(_A, task_id, claimant=f"{_A}/alice", body_task=task_id)


async def _forward_on(ws: ClientConnection, request: ClaimForwardRequest) -> bool:
    """Forward one claim on an open peer socket and return whether the owner granted it."""
    await send_json(
        ws,
        sender=_PEER,
        type=MessageType.MULTIHUB_CLAIM_REQUEST,
        **encode_claim_forward_request(request),
    )
    reply = await read_until_type(ws, MessageType.MULTIHUB_CLAIM_RESULT)
    return decode_claim_forward_result(reply).granted


async def _until_peer_left(hub: SynapseHub) -> None:
    """Wait until the owner has processed the peer's disconnect."""
    async with asyncio.timeout(5):
        while _PEER in hub.clients.agent_sockets:
            await asyncio.sleep(0.01)


async def _sweep_by_local_claim(agent: AgentHandle, task_id: str) -> None:
    """Make a local claim, which runs the owner's abandoned-claim sweep before it is decided."""
    await agent.agent.claim(task_id, worktree="/repo", paths=[f"local/{task_id}.py"])
    await agent.recorder.wait_for(
        lambda m: (
            m.get("task_id") == task_id
            and m.get("type") in (MessageType.CLAIM_GRANTED, MessageType.CLAIM_DENIED)
        )
    )


def _owner_hub(tmp_path: Path, *, journal: EventStore | None = None) -> SynapseHub:
    """Return the owner of ALPHA, trusting the peer to forward ALPHA claims, with a short window."""
    pin, der = _write_peer_cert(tmp_path)
    return _owner(
        _policy(pin, der, trusted=_A, scope=[("claim", _A)]),
        journal=journal,
        lease_offline_ttl=_WINDOW,
    )


async def test_a_forwarded_claim_stands_past_the_window_while_its_peer_stays_connected(
    tmp_path: Path,
) -> None:
    """The peer stays connected past the window; its forwarded claim stands, local orphans go."""
    hub = _owner_hub(tmp_path)
    async with running_hub(hub) as (_, uri):
        async with await _connect(uri, _PEER) as ws:
            assert await _forward_on(ws, _forwarded("tF")) is True
            await asyncio.sleep(_WINDOW + 0.2)
            # The next forward runs the abandoned-claim sweep before it is decided.
            assert await _forward_on(ws, _forwarded("tG")) is True
            assert hub.state.claims["tF"].owner == f"{_A}/alice"
    # The same sweep still released the local claims whose holders never connected.
    assert "tA" not in hub.state.claims
    assert "tB" not in hub.state.claims


async def test_a_forwarded_claim_is_released_a_window_after_its_peer_left(
    tmp_path: Path,
) -> None:
    """The window runs from the peer's departure, not from the hub's start."""
    hub = _owner_hub(tmp_path)
    async with running_hub(hub) as (_, uri):
        # Let the hub's own start fall a whole window into the past first.
        await asyncio.sleep(_WINDOW + 0.2)
        async with await _connect(uri, _PEER) as ws:
            assert await _forward_on(ws, _forwarded("tF")) is True
        await _until_peer_left(hub)
        carol = await connect_agent(f"{_A}/carol", uri)
        try:
            await _sweep_by_local_claim(carol, "tH")
            assert hub.state.claims["tF"].owner == f"{_A}/alice"

            await asyncio.sleep(_WINDOW + 0.2)
            carol.recorder.messages.clear()
            await _sweep_by_local_claim(carol, "tI")
            released = await carol.recorder.wait_for(
                lambda m: m.get("type") == MessageType.RELEASE_GRANTED and m.get("task_id") == "tF"
            )
        finally:
            await carol.close()
    assert released["released_by"] == HOLDER_OFFLINE_ACTOR
    assert released["owner"] == f"{_A}/alice"
    assert f"forwarded by peer hub {_PEER}" in released["payload"]
    assert "tF" not in hub.state.claims


async def test_a_peer_that_returns_inside_the_window_keeps_its_forwarded_claims(
    tmp_path: Path,
) -> None:
    """A short peer reconnect gap does not cost the claims it forwarded."""
    hub = _owner_hub(tmp_path)
    async with running_hub(hub) as (_, uri):
        async with await _connect(uri, _PEER) as ws:
            assert await _forward_on(ws, _forwarded("tF")) is True
        await _until_peer_left(hub)
        async with await _connect(uri, _PEER) as ws:
            await asyncio.sleep(_WINDOW + 0.2)
            assert await _forward_on(ws, _forwarded("tG")) is True
            assert hub.state.claims["tF"].owner == f"{_A}/alice"


async def test_a_restored_forwarded_claim_still_lives_by_its_peer(tmp_path: Path) -> None:
    """After an owner restart the journal still binds the claim to the peer that forwarded it."""
    path = tmp_path / "owner.db"
    first = _owner_hub(tmp_path, journal=EventStore(path))
    async with running_hub(first) as (_, uri):
        async with await _connect(uri, _PEER) as ws:
            assert await _forward_on(ws, _forwarded("tF")) is True
    second = _owner_hub(tmp_path, journal=EventStore(path))
    assert second.state.claims["tF"].owner == f"{_A}/alice"
    async with running_hub(second) as (_, uri):
        async with await _connect(uri, _PEER) as ws:
            await asyncio.sleep(_WINDOW + 0.2)
            assert await _forward_on(ws, _forwarded("tG")) is True
            assert second.state.claims["tF"].owner == f"{_A}/alice"


async def test_the_state_view_shows_a_forwarded_claim_held_while_its_peer_is_connected(
    tmp_path: Path,
) -> None:
    """The holder columns of a state snapshot follow the forwarding peer, as the sweep does."""
    hub = _owner_hub(tmp_path)
    async with running_hub(hub) as (_, uri):
        carol = await connect_agent(f"{_A}/carol", uri)
        try:
            async with await _connect(uri, _PEER) as ws:
                assert await _forward_on(ws, _forwarded("tF")) is True
                held = (await _state_claims(carol))["tF"]
                assert held["holder_online"] is True
                assert held["holder_offline_seconds"] is None
            await _until_peer_left(hub)
            away = (await _state_claims(carol))["tF"]
            assert away["holder_online"] is False
            assert 0.0 <= away["holder_offline_seconds"] < _WINDOW
        finally:
            await carol.close()


async def test_the_forwarding_peer_is_journalled_but_never_shown(tmp_path: Path) -> None:
    """The journal records the forwarding peer; the grant, broadcast and state view do not."""
    hub = _owner_hub(tmp_path)
    async with running_hub(hub) as (_, uri):
        carol = await connect_agent(f"{_A}/carol", uri)
        try:
            async with await _connect(uri, _PEER) as ws:
                await send_json(
                    ws,
                    sender=_PEER,
                    type=MessageType.MULTIHUB_CLAIM_REQUEST,
                    **encode_claim_forward_request(_forwarded("tF")),
                )
                reply = decode_claim_forward_result(
                    await read_until_type(ws, MessageType.MULTIHUB_CLAIM_RESULT)
                )
                broadcast = await carol.recorder.wait_for(
                    lambda m: (
                        m.get("type") == MessageType.CLAIM_GRANTED and m.get("task_id") == "tF"
                    )
                )
                shown = (await _state_claims(carol))["tF"]
        finally:
            await carol.close()
    assert reply.granted is True
    assert "via_peer" not in (reply.grant or {})
    assert "via_peer" not in broadcast
    assert "via_peer" not in shown
    held = hub.state.claims["tF"]
    assert "via_peer" not in held.as_dict()
    assert held.as_persisted_dict()["via_peer"] == _PEER


async def _state_claims(agent: AgentHandle) -> dict[str, dict[str, Any]]:
    """Return the active claims of a fresh state snapshot, keyed by task id."""
    agent.recorder.messages.clear()
    await agent.agent.send_message(MessageType.STATE_REQUEST, target="System")
    snapshot = await agent.recorder.wait_for(lambda m: m.get("type") == MessageType.STATE_SNAPSHOT)
    return {claim["task_id"]: claim for claim in snapshot["snapshot"]["active_claims"]}
