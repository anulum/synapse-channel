# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — exact read-only lease confirmation
"""Confirm uncertain claims without replaying a lease mutation."""

from __future__ import annotations

import math
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.lifecycle import ALL_STATUSES, TERMINAL_STATUSES
from synapse_channel.core.protocol import MessageType

ReplyAwaiter = Callable[
    [Callable[[dict[str, Any]], bool], Callable[[], Awaitable[None]]],
    Awaitable[dict[str, Any] | None],
]
DEFAULT_CLAIM_REPLY_TIMEOUT = 30.0
MAX_CLAIM_REPLY_TIMEOUT = 300.0


def valid_claim_timeout(value: float) -> bool:
    """Return whether a caller supplied a finite, positive bounded deadline.

    Parameters
    ----------
    value : float
        Requested seconds per send/reply exchange.

    Returns
    -------
    bool
        Whether the value is finite, positive and at most 300 seconds.
    """
    return math.isfinite(value) and 0 < value <= MAX_CLAIM_REPLY_TIMEOUT


def _finite_number(value: object) -> float | None:
    """Read a finite wire timestamp without treating booleans as numbers."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) else None


@dataclass(frozen=True)
class ClaimIntent:
    """Exact local scope that a grant or confirmation must prove.

    Parameters
    ----------
    task_id, owner : str
        Normalized task id and exact registered identity.
    worktree : str
        Canonical worktree root resolved on the requesting client.
    paths : tuple[str, ...]
        Ordered canonical display paths; empty means the whole worktree.
    path_identity : dict[str, object] or None
        Versioned filesystem comparison metadata, when supplied.
    git : dict[str, str] or None
        Exact branch, integration base and release policy metadata.

    Notes
    -----
    Fields mirror the resolved request; equality never broadens paths or drops
    Git metadata. An expired, terminal or unfenced lease cannot authorize work.
    """

    task_id: str
    owner: str
    worktree: str
    paths: tuple[str, ...]
    path_identity: dict[str, object] | None
    git: dict[str, str] | None

    def matches(self, claim: dict[str, Any], *, now: float) -> bool:
        """Check exact ownership, scope, metadata, fence and lease validity.

        Parameters
        ----------
        claim : dict[str, Any]
            Public grant or active-claim record returned by the trusted hub.
        now : float
            Conservative current timestamp used to refuse expired leases.

        Returns
        -------
        bool
            Whether this single record proves the exact live, nonterminal lease.
        """
        expiry = _finite_number(claim.get("lease_expires_at"))
        epoch = claim.get("epoch")
        status = claim.get("status")
        return (
            claim.get("task_id") == self.task_id
            and claim.get("owner") == self.owner
            and claim.get("worktree") == self.worktree
            and claim.get("paths") == list(self.paths)
            and claim.get("path_identity") == self.path_identity
            and claim.get("git") == self.git
            and isinstance(epoch, int)
            and not isinstance(epoch, bool)
            and epoch > 0
            and isinstance(status, str)
            and status in ALL_STATUSES - TERMINAL_STATUSES
            and expiry is not None
            and expiry > now
        )


async def confirm_claim(
    agent: SynapseAgent, await_reply: ReplyAwaiter, intent: ClaimIntent
) -> bool:
    """Read one correlated snapshot and persist only an exact live lease fence.

    Parameters
    ----------
    agent : SynapseAgent
        Ready authenticated client whose identity owns the requested lease.
    await_reply : ReplyAwaiter
        Bounded transport correlator; registers before sending the request.
    intent : ClaimIntent
        Exact resolved scope to confirm without claiming or renewing it.

    Returns
    -------
    bool
        Whether a fresh matching snapshot proves a currently live lease.
        Absence or mismatch remains unknown, never a denial or permission.
    """
    request_id = uuid.uuid4().hex
    reply = await await_reply(
        lambda data: (
            data.get("type") == MessageType.STATE_SNAPSHOT
            and data.get("target") == intent.owner
            and data.get("request_id") == request_id
        ),
        lambda: agent.send_message(
            MessageType.STATE_REQUEST,
            target="System",
            payload="claim confirmation",
            request_id=request_id,
        ),
    )
    if reply is None:
        return False
    snapshot = reply.get("snapshot")
    if not isinstance(snapshot, dict):
        return False
    generated_at = _finite_number(snapshot.get("generated_at"))
    claims = snapshot.get("active_claims")
    if generated_at is None or not isinstance(claims, list):
        return False
    matching = [
        claim
        for claim in claims
        if isinstance(claim, dict) and claim.get("task_id") == intent.task_id
    ]
    if len(matching) != 1 or not intent.matches(matching[0], now=max(time.time(), generated_at)):
        return False
    epoch = int(matching[0]["epoch"])
    if agent._lease_epoch_store is not None:
        agent._lease_epoch_store.save(agent.hub_id, intent.task_id, epoch)
        if agent._lease_epoch_store.load(agent.hub_id, intent.task_id) != epoch:
            return False
    agent.lease_epochs[intent.task_id] = epoch
    return True
