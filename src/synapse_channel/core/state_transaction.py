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

import asyncio
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from copy import copy, deepcopy
from dataclasses import dataclass, fields
from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from synapse_channel.core.state import SynapseState
    from synapse_channel.core.state_models import TaskClaim

MutationResult = TypeVar("MutationResult")


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
            if self.claim_value is None:
                raise RuntimeError("transaction snapshot lost its live claim value")
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


class SerializedStateMutationActor:
    """Serialize durable state transitions without blocking the event loop.

    A durable transition is first applied to a private deep copy.  Its journal
    append then runs in a worker thread while the live state remains unchanged.
    Only a successful append publishes the candidate, synchronously and without
    yielding.  The actor lock orders concurrent state mutations and prevents a
    heartbeat or another transition from being lost while one append is in
    flight.

    Non-durable hubs mutate the live state directly under the same lock.  This
    keeps one ordering model without paying the copy cost when there is no I/O
    boundary to cross.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()

    async def run(
        self,
        state: SynapseState,
        mutate: Callable[[SynapseState], MutationResult],
        *,
        persist: Callable[[MutationResult], None] | None = None,
        publish: Callable[[MutationResult], None] | None = None,
    ) -> MutationResult:
        """Apply one serialized mutation and publish it only after persistence."""
        async with self._lock:
            if persist is None:
                result = mutate(state)
                if publish is not None:
                    publish(result)
                return result

            candidate = deepcopy(state)
            result = mutate(candidate)
            append = asyncio.create_task(asyncio.to_thread(persist, result))
            cancelled = False
            try:
                await asyncio.shield(append)
            except asyncio.CancelledError:
                # A worker thread cannot be cancelled.  Keep the mutation lock,
                # wait for its authoritative outcome, and publish a committed
                # candidate before propagating cancellation.  Otherwise shutdown
                # could close the journal around an in-flight append or leave a
                # durable event whose live state was discarded.
                cancelled = True
                await append
            state.publish_from(candidate)
            if publish is not None:
                publish(result)
            if cancelled:
                raise asyncio.CancelledError
            return result
