# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — authoritative state retrieval for the Claude claim guard
"""Fetch one live hub snapshot for the Claude claim guard.

This transport is isolated from claim-decision logic so network lifecycle and
timeouts stay independently testable. It never prints: the hook's stdout is
reserved for Claude Code's structured decision JSON.
"""

from __future__ import annotations

import asyncio
import contextlib
import math
from collections.abc import Callable
from typing import Any, Protocol

from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.protocol import MessageType


class StateAgent(Protocol):
    """Minimal client surface required to retrieve one state snapshot."""

    running: bool

    async def connect(self) -> None:
        """Connect until cancelled."""

    async def wait_until_ready(self, *, timeout: float) -> bool:
        """Return whether the hub handshake completed before ``timeout``."""

    async def request_state(self) -> None:
        """Request the authoritative state snapshot."""


AgentFactory = Callable[..., StateAgent]


class StateSnapshotError(RuntimeError):
    """The authoritative hub snapshot could not be obtained safely."""


def _validated_phase_timeout(timeout: float) -> float:
    """Return a finite positive state-query deadline or fail closed."""
    if not math.isfinite(timeout) or timeout <= 0:
        raise StateSnapshotError(
            "Synapse state query timeout must be finite and greater than zero."
        )
    return timeout


async def fetch_state_snapshot(
    *,
    uri: str,
    requester: str,
    token: str | None,
    timeout: float,
    agent_factory: AgentFactory = SynapseAgent,
) -> dict[str, Any]:
    """Request one authoritative live state snapshot without printing.

    Parameters
    ----------
    uri, requester : str
        Hub endpoint and short-lived query identity.
    token : str or None
        Optional secured-hub token.
    timeout : float
        Per-phase deadline for connection readiness and the state reply.
    agent_factory : AgentFactory, optional
        Injectable client factory for focused timeout and connection tests.

    Returns
    -------
    dict[str, Any]
        The hub's state snapshot mapping.

    Raises
    ------
    StateSnapshotError
        If connection, timeout, or response validation fails.
    """
    phase_timeout = _validated_phase_timeout(timeout)
    received = asyncio.Event()
    result: list[dict[str, Any]] = []

    async def collect(data: dict[str, Any]) -> None:
        if data.get("type") != MessageType.STATE_SNAPSHOT:
            return
        snapshot = data.get("snapshot")
        if isinstance(snapshot, dict):
            result.append(snapshot)
        received.set()

    agent = agent_factory(requester, collect, uri=uri, verbose=False, token=token)
    connection = asyncio.create_task(agent.connect())
    try:
        if not await agent.wait_until_ready(timeout=phase_timeout):
            raise StateSnapshotError("Synapse hub is unavailable; Edit/Write denied fail-closed.")
        await agent.request_state()
        try:
            await asyncio.wait_for(received.wait(), timeout=phase_timeout)
        except asyncio.TimeoutError as exc:
            raise StateSnapshotError(
                "Synapse state query timed out; Edit/Write denied fail-closed."
            ) from exc
        if not result:
            raise StateSnapshotError("Synapse returned an invalid state snapshot.")
        return result[-1]
    finally:
        agent.running = False
        connection.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await connection
