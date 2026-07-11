# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — generic authoritative state retrieval
"""Fetch one authoritative hub state snapshot without printing or mutation."""

from __future__ import annotations

import asyncio
import contextlib
import math
from collections.abc import Callable
from typing import Any, Protocol

from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.errors import SynapseError
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
MAX_CLAIM_STATE_PHASE_TIMEOUT = 300.0
"""Maximum seconds allowed for either state-query phase."""

_HUB_UNAVAILABLE = "Synapse hub is unavailable; claim denied fail-closed."
_HUB_CONNECTION_ENDED = (
    "Synapse hub unavailable: connection ended before state arrived; claim denied fail-closed."
)
_HUB_CONNECTION_FAILED = (
    "Synapse hub unavailable: connection failed during state query; claim denied fail-closed."
)


class ClaimStateError(SynapseError, RuntimeError):
    """The authoritative hub snapshot could not be obtained safely."""

    code = "claim_state"


def _validated_phase_timeout(timeout: float) -> float:
    if not math.isfinite(timeout) or timeout <= 0 or timeout > MAX_CLAIM_STATE_PHASE_TIMEOUT:
        raise ClaimStateError(
            "Synapse state query timeout must be finite, greater than zero, "
            f"and at most {MAX_CLAIM_STATE_PHASE_TIMEOUT:g} seconds."
        )
    return timeout


def _raise_if_connection_ended(connection: asyncio.Task[None]) -> None:
    if not connection.done():
        return
    try:
        connection.result()
    except asyncio.CancelledError as exc:
        raise ClaimStateError(_HUB_CONNECTION_ENDED) from exc
    except Exception as exc:
        raise ClaimStateError(_HUB_CONNECTION_FAILED) from exc
    raise ClaimStateError(_HUB_CONNECTION_ENDED)


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
        Hub endpoint and bounded query identity.
    token : str or None
        Optional secured-hub token.
    timeout : float
        Per-phase deadline for readiness and the state reply.
    agent_factory : AgentFactory, optional
        Injectable client factory for focused transport verification.

    Returns
    -------
    dict[str, Any]
        The hub's state snapshot mapping.

    Raises
    ------
    ClaimStateError
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

    agent: StateAgent | None = None
    connection: asyncio.Task[None] | None = None
    readiness: asyncio.Task[bool] | None = None
    response: asyncio.Task[bool] | None = None
    try:
        agent = agent_factory(requester, collect, uri=uri, verbose=False, token=token)
        connection = asyncio.create_task(agent.connect())
        readiness = asyncio.create_task(agent.wait_until_ready(timeout=phase_timeout))
        done, _ = await asyncio.wait(
            {connection, readiness},
            timeout=phase_timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
        _raise_if_connection_ended(connection)
        if readiness not in done:
            raise ClaimStateError("Synapse hub readiness timed out; claim denied fail-closed.")
        if not readiness.result():
            raise ClaimStateError(_HUB_UNAVAILABLE)

        await agent.request_state()
        response = asyncio.create_task(received.wait())
        done, _ = await asyncio.wait(
            {connection, response},
            timeout=phase_timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
        _raise_if_connection_ended(connection)
        if response not in done:
            raise ClaimStateError("Synapse state query timed out; claim denied fail-closed.")
        if not result:
            raise ClaimStateError("Synapse returned an invalid state snapshot.")
        return result[-1]
    except ClaimStateError:
        raise
    except Exception as exc:
        raise ClaimStateError("Synapse state query failed; claim denied fail-closed.") from exc
    finally:
        if agent is not None:
            agent.running = False
        for task in (response, readiness, connection):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
