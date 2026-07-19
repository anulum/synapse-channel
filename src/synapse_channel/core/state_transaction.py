# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — rollback boundary for durable state mutations
"""Make an in-memory claim mutation atomic with its durable journal append.

The constant-cost boundary snapshots one task before its normal state method.
If the following append raises, the helper restores that task, its epoch and
checkpoint accounting, and any explicitly named presence entries. Independent
heartbeat housekeeping remains valid; lease indexing is rebuilt only on the
exceptional path.
"""

from __future__ import annotations

import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from copy import copy
from dataclasses import dataclass, fields
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from synapse_channel.core.state import SynapseState
    from synapse_channel.core.state_models import TaskClaim


@dataclass(frozen=True)
class _TaskStateSnapshot:
    """State needed to undo one synchronous claim-family mutation."""

    task_id: str
    claim: TaskClaim | None
    claim_value: TaskClaim | None
    checkpoint_present: bool
    checkpoint: str
    presence: tuple[tuple[str, bool, float], ...]
    epoch_seq: int

    @classmethod
    def capture(
        cls,
        state: SynapseState,
        task_id: str,
        presence_agents: Sequence[str],
    ) -> _TaskStateSnapshot:
        """Capture one task plus presence entries the mutation may update."""
        claim = state.claims.get(task_id)
        checkpoint_present = task_id in state.expired_checkpoints
        return cls(
            task_id=task_id,
            claim=claim,
            claim_value=copy(claim) if claim is not None else None,
            checkpoint_present=checkpoint_present,
            checkpoint=state.expired_checkpoints.get(task_id, ""),
            presence=tuple(
                (agent, agent in state.last_seen, state.last_seen.get(agent, 0.0))
                for agent in dict.fromkeys(presence_agents)
            ),
            epoch_seq=state._epoch_seq,
        )

    def restore(self, state: SynapseState) -> None:
        """Restore the task and accounting captured before the mutation."""
        expired = self.claim is not None and self.claim.lease_expires_at <= time.time()
        if self.claim is None or expired:
            state.claims.pop(self.task_id, None)
        else:
            assert self.claim_value is not None
            for field in fields(self.claim_value):
                setattr(self.claim, field.name, getattr(self.claim_value, field.name))
            state.claims[self.task_id] = self.claim

        if expired and self.claim is not None and self.claim.checkpoint:
            state.expired_checkpoints[self.task_id] = self.claim.checkpoint
        elif self.checkpoint_present:
            state.expired_checkpoints[self.task_id] = self.checkpoint
        else:
            state.expired_checkpoints.pop(self.task_id, None)

        for agent, present, timestamp in self.presence:
            if present:
                state.last_seen[agent] = timestamp
            else:
                state.last_seen.pop(agent, None)

        state._epoch_seq = self.epoch_seq
        state.reindex_leases()


@contextmanager
def durable_state_transaction(
    state: SynapseState,
    task_id: str,
    *,
    enabled: bool,
    restore_presence: Sequence[str] = (),
) -> Iterator[None]:
    """Roll back one task if its synchronous durable append raises.

    The context may contain state mutation plus synchronous journal I/O only;
    it must not contain an event-loop yield.
    """
    snapshot = _TaskStateSnapshot.capture(state, task_id, restore_presence) if enabled else None
    try:
        yield
    except BaseException:
        if snapshot is not None:
            snapshot.restore(state)
        raise
