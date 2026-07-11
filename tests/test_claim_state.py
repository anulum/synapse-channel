# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — generic authoritative state transport regressions

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from hub_e2e_helpers import running_hub
from synapse_channel.claim_state import ClaimStateError, StateAgent, fetch_state_snapshot
from synapse_channel.core.errors import error_code
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.protocol import MessageType

Callback = Callable[[dict[str, Any]], Awaitable[None]]


class _AgentBase:
    running = True

    def __init__(self, _name: str, callback: Callback, **_kwargs: object) -> None:
        self.callback = callback

    async def connect(self) -> None:
        await asyncio.Future()

    async def wait_until_ready(self, *, timeout: float) -> bool:
        del timeout
        return True


@pytest.mark.asyncio
async def test_state_fetch_reads_a_real_hub_snapshot() -> None:
    async with running_hub(SynapseHub(hub_id="syn-claim-state")) as (_hub, uri):
        snapshot = await fetch_state_snapshot(
            uri=uri,
            requester="claim-state/test",
            token=None,
            timeout=1.0,
        )
    assert snapshot["active_claims"] == []
    assert any(agent["agent"] == "claim-state/test" for agent in snapshot["agents"])
    assert isinstance(snapshot["generated_at"], float)


@pytest.mark.asyncio
async def test_state_fetch_returns_snapshot_ignores_noise_and_cleans_up() -> None:
    agents: list[ValidAgent] = []

    class ValidAgent(_AgentBase):
        def __init__(self, name: str, callback: Callback, **kwargs: object) -> None:
            super().__init__(name, callback, **kwargs)
            agents.append(self)

        async def request_state(self) -> None:
            await self.callback({"type": "chat", "payload": "noise"})
            await self.callback(
                {"type": MessageType.STATE_SNAPSHOT, "snapshot": {"active_claims": []}}
            )

    snapshot = await fetch_state_snapshot(
        uri="ws://hub",
        requester="guard",
        token=None,
        timeout=0.1,
        agent_factory=ValidAgent,
    )
    assert snapshot == {"active_claims": []}
    assert len(agents) == 1
    assert not agents[0].running


@pytest.mark.asyncio
async def test_state_fetch_unavailable_hub_fails_closed() -> None:
    class UnavailableAgent(_AgentBase):
        async def wait_until_ready(self, *, timeout: float) -> bool:
            del timeout
            return False

        async def request_state(self) -> None:
            raise AssertionError("state must not be requested before readiness")

    with pytest.raises(ClaimStateError, match="unavailable"):
        await fetch_state_snapshot(
            uri="ws://hub",
            requester="guard",
            token=None,
            timeout=0.01,
            agent_factory=UnavailableAgent,
        )


@pytest.mark.asyncio
async def test_state_fetch_readiness_timeout_fails_closed() -> None:
    class NeverReadyAgent(_AgentBase):
        async def wait_until_ready(self, *, timeout: float) -> bool:
            del timeout
            await asyncio.Future[None]()
            return False

        async def request_state(self) -> None:
            raise AssertionError("state must not be requested before readiness")

    with pytest.raises(ClaimStateError, match="readiness timed out"):
        await fetch_state_snapshot(
            uri="ws://hub",
            requester="guard",
            token=None,
            timeout=0.01,
            agent_factory=NeverReadyAgent,
        )


@pytest.mark.asyncio
async def test_state_fetch_response_timeout_fails_closed() -> None:
    class SilentAgent(_AgentBase):
        async def request_state(self) -> None:
            return None

    with pytest.raises(ClaimStateError, match="state query timed out"):
        await fetch_state_snapshot(
            uri="ws://hub",
            requester="guard",
            token=None,
            timeout=0.01,
            agent_factory=SilentAgent,
        )


@pytest.mark.parametrize("timeout", [float("inf"), float("-inf"), float("nan"), 0.0, 1e308])
@pytest.mark.asyncio
async def test_state_fetch_rejects_invalid_timeout_before_connect(timeout: float) -> None:
    class MustNotStart(_AgentBase):
        def __init__(self, _name: str, callback: Callback, **_kwargs: object) -> None:
            raise AssertionError("invalid timeout must fail before the agent starts")

        async def request_state(self) -> None:
            raise AssertionError("invalid timeout must not request state")

    with pytest.raises(ClaimStateError, match="finite"):
        await fetch_state_snapshot(
            uri="ws://hub",
            requester="guard",
            token=None,
            timeout=timeout,
            agent_factory=MustNotStart,
        )


@pytest.mark.asyncio
async def test_state_fetch_rejects_non_mapping_snapshot() -> None:
    class InvalidAgent(_AgentBase):
        async def request_state(self) -> None:
            await self.callback({"type": MessageType.STATE_SNAPSHOT, "snapshot": []})

    with pytest.raises(ClaimStateError, match="invalid"):
        await fetch_state_snapshot(
            uri="ws://hub",
            requester="guard",
            token=None,
            timeout=0.1,
            agent_factory=InvalidAgent,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("phase", "message"),
    [("connect", "connection failed"), ("request", "state query failed")],
)
async def test_state_fetch_wraps_connection_and_request_failures(phase: str, message: str) -> None:
    class BrokenAgent(_AgentBase):
        async def connect(self) -> None:
            if phase == "connect":
                raise OSError("offline")
            await super().connect()

        async def wait_until_ready(self, *, timeout: float) -> bool:
            if phase == "connect":
                await asyncio.Future()
            return await super().wait_until_ready(timeout=timeout)

        async def request_state(self) -> None:
            raise OSError("request failed")

    with pytest.raises(ClaimStateError, match=message) as caught:
        await fetch_state_snapshot(
            uri="ws://hub",
            requester="guard",
            token=None,
            timeout=0.1,
            agent_factory=BrokenAgent,
        )
    assert isinstance(caught.value.__cause__, OSError)
    if phase == "connect":
        assert "unavailable" in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("connection_exit", ["cancel", "return"])
async def test_state_fetch_denies_connection_that_ends_before_readiness(
    connection_exit: str,
) -> None:
    class EndedAgent(_AgentBase):
        async def connect(self) -> None:
            if connection_exit == "cancel":
                raise asyncio.CancelledError

        async def wait_until_ready(self, *, timeout: float) -> bool:
            del timeout
            await asyncio.Future[None]()
            return False

        async def request_state(self) -> None:
            raise AssertionError("state must not be requested after connection exit")

    with pytest.raises(ClaimStateError, match="connection ended") as caught:
        await fetch_state_snapshot(
            uri="ws://hub",
            requester="guard",
            token=None,
            timeout=0.1,
            agent_factory=EndedAgent,
        )
    assert "unavailable" in str(caught.value)


@pytest.mark.asyncio
async def test_state_fetch_wraps_agent_factory_failure() -> None:
    def broken_factory(*_args: object, **_kwargs: object) -> StateAgent:
        raise OSError("factory failed")

    with pytest.raises(ClaimStateError, match="state query failed") as caught:
        await fetch_state_snapshot(
            uri="ws://hub",
            requester="guard",
            token=None,
            timeout=0.1,
            agent_factory=broken_factory,
        )
    assert isinstance(caught.value.__cause__, OSError)


def test_claim_state_error_has_frozen_taxonomy_code() -> None:
    error = ClaimStateError("failure")
    assert error_code(error) == "claim_state"
    assert isinstance(error, RuntimeError)
