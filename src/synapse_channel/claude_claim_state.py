# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — Claude claim-state compatibility facade
"""Preserve the Claude claim guard's released state-query API and error code."""

from __future__ import annotations

from typing import Any

from synapse_channel.claim_state import (
    MAX_CLAIM_STATE_PHASE_TIMEOUT,
    AgentFactory,
    ClaimStateError,
)
from synapse_channel.claim_state import (
    StateAgent as StateAgent,
)
from synapse_channel.claim_state import (
    fetch_state_snapshot as _fetch_state_snapshot,
)
from synapse_channel.client.agent import SynapseAgent

MAX_CLAUDE_CLAIM_PHASE_TIMEOUT = MAX_CLAIM_STATE_PHASE_TIMEOUT
"""Maximum seconds allowed for either claim-state query phase."""


class StateSnapshotError(ClaimStateError):
    """The authoritative hub snapshot could not be obtained safely."""

    code = "claude_claim_state"


def _legacy_denial_message(message: str) -> str:
    """Restore the released Claude-facing denial subject."""
    return message.replace(
        "claim denied fail-closed.",
        "Edit/Write denied fail-closed.",
    )


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
    try:
        return await _fetch_state_snapshot(
            uri=uri,
            requester=requester,
            token=token,
            timeout=timeout,
            agent_factory=agent_factory,
        )
    except ClaimStateError as exc:
        raise StateSnapshotError(_legacy_denial_message(str(exc))) from exc
