# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — Claude claim-guard state transport regressions

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from synapse_channel.claude_claim_state import StateSnapshotError, fetch_state_snapshot
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
async def test_state_fetch_returns_valid_snapshot_and_ignores_other_frames() -> None:
    class ValidAgent(_AgentBase):
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


@pytest.mark.asyncio
async def test_state_fetch_unavailable_hub_is_a_controlled_denial() -> None:
    class UnavailableAgent(_AgentBase):
        async def wait_until_ready(self, *, timeout: float) -> bool:
            del timeout
            return False

        async def request_state(self) -> None:
            raise AssertionError("state must not be requested before readiness")

    with pytest.raises(StateSnapshotError, match="unavailable"):
        await fetch_state_snapshot(
            uri="ws://hub",
            requester="guard",
            token=None,
            timeout=0.01,
            agent_factory=UnavailableAgent,
        )


@pytest.mark.asyncio
async def test_state_fetch_timeout_is_a_controlled_denial() -> None:
    class SilentAgent(_AgentBase):
        async def request_state(self) -> None:
            return None

    with pytest.raises(StateSnapshotError, match="timed out"):
        await fetch_state_snapshot(
            uri="ws://hub",
            requester="guard",
            token=None,
            timeout=0.01,
            agent_factory=SilentAgent,
        )


@pytest.mark.parametrize("timeout", [float("inf"), float("-inf"), float("nan"), 0.0])
@pytest.mark.asyncio
async def test_state_fetch_rejects_unbounded_timeout_before_connect(timeout: float) -> None:
    class MustNotStart(_AgentBase):
        def __init__(self, _name: str, callback: Callback, **_kwargs: object) -> None:
            raise AssertionError("invalid timeout must fail before the agent starts")

        async def request_state(self) -> None:
            raise AssertionError("invalid timeout must not request state")

    with pytest.raises(StateSnapshotError, match="finite"):
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

    with pytest.raises(StateSnapshotError, match="invalid"):
        await fetch_state_snapshot(
            uri="ws://hub",
            requester="guard",
            token=None,
            timeout=0.1,
            agent_factory=InvalidAgent,
        )
