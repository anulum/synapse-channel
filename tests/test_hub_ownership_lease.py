# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — hub-authoritative ownership lease, end to end

"""The ownership keystone's hub half, proven over real websockets.

A name has exactly one owner and the hub is the authority: the first opt-in
claimant is granted a lease token, a claim without the token is refused with
close code ``4016`` whether the owner is connected or not, the owner re-takes
its own name across reconnects by presenting the token, and the takeover
damping — quarantine included — still applies to lease holders. Every test
here speaks the real wire protocol against a live in-process hub; nothing is
stubbed.
"""

from __future__ import annotations

import asyncio
from typing import Any

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from hub_e2e_helpers import read_json, read_until_type, running_hub, send_json
from synapse_channel.core.hub import SynapseHub

NAME = "PROJ/lease-owner"


class SteppingClock:
    """A monotonic hub clock the test advances explicitly."""

    def __init__(self) -> None:
        self.now = 5_000.0

    def __call__(self) -> float:
        return self.now


async def _register(websocket: Any, name: str, **fields: Any) -> None:
    """Send the registration heartbeat that binds ``name`` to the socket."""
    await send_json(websocket, sender=name, type="heartbeat", **fields)


async def _drain_expecting_close(websocket: Any, *, code: int, timeout: float = 3.0) -> str:
    """Read a socket until the hub closes it; assert the close code, return the reason."""
    try:
        while True:
            await read_json(websocket, timeout=timeout)
    except ConnectionClosed as exc:
        received = exc.rcvd
        assert received is not None, "the close must come from the hub, not this side"
        assert received.code == code, f"expected close {code}, got {received.code}"
        return str(received.reason or "")


async def _await_bound(hub: SynapseHub, name: str, *, timeout: float = 3.0) -> Any:
    """Poll the live registry until ``name`` is bound; return its socket."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        websocket = hub.clients.agent_sockets.get(name)
        if websocket is not None:
            return websocket
        await asyncio.sleep(0.01)
    raise TimeoutError(f"{name} did not bind on the hub")


async def _await_unbound(hub: SynapseHub, name: str, *, timeout: float = 3.0) -> None:
    """Poll the live registry until ``name`` is no longer bound."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if hub.clients.agent_sockets.get(name) is None:
            return
        await asyncio.sleep(0.01)
    raise TimeoutError(f"{name} did not unbind on the hub")


async def test_the_first_opt_in_claimant_is_granted_a_lease_token() -> None:
    """A free name claimed with ``lease: true`` answers with a one-time token."""
    async with running_hub() as (hub, uri):
        async with connect(uri) as owner:
            await read_json(owner)  # welcome
            await _register(owner, NAME, lease=True)
            grant = await read_until_type(owner, "lease_granted")
            assert grant["target"] == NAME
            assert grant["lease_name"] == NAME
            token = str(grant["owner_lease"])
            assert token
            # The hub keeps only a digest; the live table verifies the token.
            assert hub.clients.ownership.matches(NAME, token)
            assert not hub.clients.ownership.matches(NAME, token + "x")


async def test_a_stranger_is_refused_while_the_owner_is_live_even_with_takeover() -> None:
    """A live leased name cannot be evicted by a claim that lacks the token."""
    async with running_hub(SynapseHub(hub_id="syn-lease", takeover_cooldown=0.0)) as (hub, uri):
        async with connect(uri) as owner:
            await read_json(owner)  # welcome
            await _register(owner, NAME, lease=True)
            await read_until_type(owner, "lease_granted")

            async with connect(uri) as stranger:
                await read_json(stranger)  # welcome
                await _register(stranger, NAME, takeover=True)
                refusal = await read_until_type(stranger, "name_conflict")
                assert "ownership lease" in str(refusal["payload"])
                reason = await _drain_expecting_close(stranger, code=4016)
                assert reason == "name owned"

            # The owner was never disturbed: a directed message still lands on it.
            async with connect(uri) as prober:
                await read_json(prober)  # welcome
                await send_json(
                    prober, sender="PROJ/prober", type="chat", target=NAME, payload="still-mine"
                )
                delivered = await read_until_type(owner, "chat")
            assert delivered["payload"] == "still-mine"


async def test_a_stranger_cannot_squat_the_name_in_the_owners_reconnect_gap() -> None:
    """The refusal holds while the owner is offline — the squatting window is closed."""
    async with running_hub(SynapseHub(hub_id="syn-lease", takeover_cooldown=0.0)) as (hub, uri):
        async with connect(uri) as owner:
            await read_json(owner)  # welcome
            await _register(owner, NAME, lease=True)
            await read_until_type(owner, "lease_granted")
        await _await_unbound(hub, NAME)

        async with connect(uri) as squatter:
            await read_json(squatter)  # welcome
            await _register(squatter, NAME)
            await read_until_type(squatter, "name_conflict")
            assert await _drain_expecting_close(squatter, code=4016) == "name owned"

        async with connect(uri) as squatter_with_takeover:
            await read_json(squatter_with_takeover)  # welcome
            await _register(squatter_with_takeover, NAME, takeover=True)
            await read_until_type(squatter_with_takeover, "name_conflict")
            assert await _drain_expecting_close(squatter_with_takeover, code=4016) == "name owned"


async def test_the_owner_re_takes_its_name_with_the_lease_after_a_reconnect() -> None:
    """Presenting the token re-binds the name, keeps the lease, and mints nothing new."""
    async with running_hub() as (hub, uri):
        async with connect(uri) as first_life:
            await read_json(first_life)  # welcome
            await _register(first_life, NAME, lease=True)
            grant = await read_until_type(first_life, "lease_granted")
            token = str(grant["owner_lease"])
        await _await_unbound(hub, NAME)

        async with connect(uri) as second_life:
            await read_json(second_life)  # welcome
            await _register(second_life, NAME, lease=True, owner_lease=token)
            await _await_bound(hub, NAME)

            async with connect(uri) as prober:
                await read_json(prober)  # welcome
                await send_json(
                    prober, sender="PROJ/prober", type="chat", target=NAME, payload="welcome-back"
                )
                seen: list[str] = []
                while True:
                    frame = await read_json(second_life, timeout=3.0)
                    seen.append(str(frame.get("type")))
                    if frame.get("type") == "chat":
                        assert frame["payload"] == "welcome-back"
                        break
            # The existing lease was honoured, not rotated: no fresh grant frame
            # arrived anywhere between the rebind and the delivered probe.
            assert "lease_granted" not in seen
            assert hub.clients.ownership.matches(NAME, token)


async def test_a_lease_holder_evicts_its_own_ghost_with_takeover_and_the_token() -> None:
    """The re-arm path: a ghost socket holds the name, the token plus takeover re-takes it."""
    async with running_hub(SynapseHub(hub_id="syn-lease", takeover_cooldown=0.0)) as (hub, uri):
        async with connect(uri) as ghost, connect(uri) as rearmed:
            await read_json(ghost)  # welcome
            await read_json(rearmed)  # welcome
            await _register(ghost, NAME, lease=True)
            grant = await read_until_type(ghost, "lease_granted")
            token = str(grant["owner_lease"])

            await _register(rearmed, NAME, takeover=True, owner_lease=token)
            assert await _drain_expecting_close(ghost, code=4010) == "superseded"
            assert hub.clients.agent_sockets[NAME] is not None
            assert hub.clients.socket_agent.get(hub.clients.agent_sockets[NAME]) == NAME
            assert hub.clients.ownership.matches(NAME, token)


async def test_takeover_quarantine_still_trips_for_lease_holders() -> None:
    """Two processes sharing one token cannot evict each other indefinitely."""
    hub = SynapseHub(
        hub_id="syn-lease",
        takeover_cooldown=0.0,
        takeover_oscillation_threshold=2,
        takeover_quarantine=60.0,
    )
    async with running_hub(hub) as (_, uri):
        async with connect(uri) as owner, connect(uri) as twin, connect(uri) as owner_again:
            await read_json(owner)  # welcome
            await read_json(twin)  # welcome
            await read_json(owner_again)  # welcome
            await _register(owner, NAME, lease=True)
            grant = await read_until_type(owner, "lease_granted")
            token = str(grant["owner_lease"])

            # First takeover with the token is accepted and evicts the owner.
            await _register(twin, NAME, takeover=True, owner_lease=token)
            assert await _drain_expecting_close(owner, code=4010) == "superseded"

            # The second one inside the oscillation window trips quarantine —
            # holding the token does not exempt a name from the damping.
            await _register(owner_again, NAME, takeover=True, owner_lease=token)
            reason = await _drain_expecting_close(owner_again, code=4014)
            assert "quarantine" in reason


async def test_a_non_opt_in_name_keeps_classic_first_come_semantics() -> None:
    """A client that never asks for a lease is neither granted nor gated by one."""
    async with running_hub() as (hub, uri):
        async with connect(uri) as classic:
            await read_json(classic)  # welcome
            await _register(classic, NAME)
            await _await_bound(hub, NAME)
            assert not hub.clients.ownership.is_leased(NAME)
        await _await_unbound(hub, NAME)

        # The reconnect gap is open, exactly as before the lease existed: the
        # next claimant simply binds.
        async with connect(uri) as next_comer:
            await read_json(next_comer)  # welcome
            await _register(next_comer, NAME)
            await _await_bound(hub, NAME)
            assert not hub.clients.ownership.is_leased(NAME)


async def test_the_lease_lapses_after_the_offline_ttl_and_the_name_is_reclaimable() -> None:
    """A lost token self-heals: past the offline window the name is free again."""
    clock = SteppingClock()
    hub = SynapseHub(hub_id="syn-lease", clock=clock, lease_offline_ttl=10.0)
    async with running_hub(hub) as (_, uri):
        async with connect(uri) as owner:
            await read_json(owner)  # welcome
            await _register(owner, NAME, lease=True)
            grant = await read_until_type(owner, "lease_granted")
            stale_token = str(grant["owner_lease"])
        await _await_unbound(hub, NAME)

        clock.now += 10.0
        async with connect(uri) as new_owner:
            await read_json(new_owner)  # welcome
            await _register(new_owner, NAME, lease=True)
            regrant = await read_until_type(new_owner, "lease_granted")
            new_token = str(regrant["owner_lease"])
            assert new_token != stale_token

            # The stale token now belongs to nobody: the former owner presenting
            # it is a stranger against the new lease.
            async with connect(uri) as former_owner:
                await read_json(former_owner)  # welcome
                await _register(former_owner, NAME, owner_lease=stale_token, takeover=True)
                await read_until_type(former_owner, "name_conflict")
                assert await _drain_expecting_close(former_owner, code=4016) == "name owned"


async def test_the_grant_frame_reaches_only_the_owner_socket() -> None:
    """The token is a bearer credential: no other connection ever sees it."""
    async with running_hub() as (_, uri):
        async with connect(uri) as observer, connect(uri) as owner:
            await read_json(observer)  # welcome
            await read_json(owner)  # welcome
            await _register(observer, "PROJ/observer")
            await _register(owner, NAME, lease=True)
            await read_until_type(owner, "lease_granted")

            # Order a probe AFTER the grant: everything the observer was going
            # to be sent up to this point arrives before the probe does.
            async with connect(uri) as prober:
                await read_json(prober)  # welcome
                await send_json(
                    prober,
                    sender="PROJ/prober",
                    type="chat",
                    target="PROJ/observer",
                    payload="fence",
                )
                seen: list[str] = []
                while True:
                    frame = await read_json(observer, timeout=3.0)
                    seen.append(str(frame.get("type")))
                    if frame.get("type") == "chat" and frame.get("payload") == "fence":
                        break
            assert "lease_granted" not in seen
