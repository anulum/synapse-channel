# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — bounded historical pending work projection
"""Read one recipient's historical assignments without a global state snapshot."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from synapse_channel.core.ledger import Blackboard
from synapse_channel.core.pending_index import assignment_key
from synapse_channel.core.state import SynapseState

MAX_PENDING_WORK = 1024


@dataclass(frozen=True)
class PendingItem:
    """Retain a content-free historical task or proven handoff fingerprint."""

    kind: Literal["tasks", "handoffs"]
    key: str

    def __post_init__(self) -> None:
        """Reject unknown families and raw or malformed identifiers."""
        if (
            self.kind not in ("tasks", "handoffs")
            or type(self.key) is not str
            or re.fullmatch(r"[0-9a-f]{64}", self.key) is None
        ):
            raise ValueError("invalid pending work item")

    def encode(self) -> dict[str, str]:
        """Return only a family and fixed-size assignment fingerprint."""
        return {"kind": self.kind, "key": self.key}


@dataclass(frozen=True)
class PendingQuery:
    """Validate a complete bounded exact-recipient historical work request."""

    identity: str
    hub_id: str
    limit: int = MAX_PENDING_WORK

    def __post_init__(self) -> None:
        """Refuse coercion, aliases and unbounded reply populations."""
        if (
            type(self.identity) is not str
            or not self.identity
            or self.identity.strip() != self.identity
            or len(self.identity) > 256
            or type(self.hub_id) is not str
            or len(self.hub_id) > 256
            or type(self.limit) is not int
            or not 1 <= self.limit <= MAX_PENDING_WORK
        ):
            raise ValueError("invalid pending work query")

    @classmethod
    def from_value(cls, value: object) -> PendingQuery:
        """Parse the exact version-one request without additional selectors."""
        if (
            not isinstance(value, dict)
            or set(value) != {"version", "identity", "hub_id", "limit"}
            or type(value.get("version")) is not int
            or value["version"] != 1
        ):
            raise ValueError("invalid pending work query")
        return cls(value["identity"], value["hub_id"], value["limit"])


def read_pending_work(
    board: Blackboard, state: SynapseState, query: PendingQuery, *, hub_id: str, now: float
) -> dict[str, object]:
    """Return a complete bounded current projection of historical pending work.

    Parameters
    ----------
    board, state : Blackboard, SynapseState
        Native projections whose recipient indexes publish with their effects.
    query : PendingQuery
        Admitted exact identity, hub binding and complete-response limit.
    hub_id : str
        Actual native hub binding; a different requested binding is refused.
    now : float
        Native UTC seconds for filtering expired leases without mutating state.

    Returns
    -------
    dict[str, object]
        Metadata-only snapshot. Oversized recipient populations refuse rather
        than returning partial work; unrelated recipients are never traversed.
    """
    if query.hub_id and query.hub_id != hub_id:
        raise ValueError("pending work hub changed")
    items = [
        PendingItem("tasks", key)
        for _, key in board.pending_assignments.rows(query.identity, limit=query.limit)
    ]
    for task_id, key in state.pending_handoffs.rows(query.identity, limit=query.limit):
        claim = state.claims.get(task_id)
        if (
            claim is not None
            and claim.owner == query.identity
            and claim.lease_expires_at > now
            and claim.status not in ("done", "failed")
            and key
            == assignment_key(
                "handoffs", task_id, claim.owner, claim.ownership_epoch or claim.epoch
            )
        ):
            items.append(PendingItem("handoffs", key))
    if len(items) > query.limit:
        raise ValueError("pending assignment capacity exceeded")
    return {
        "version": 1,
        "available": True,
        "identity": query.identity,
        "hub_id": hub_id,
        "items": [x.encode() for x in sorted(items, key=lambda x: (x.kind, x.key))],
    }
