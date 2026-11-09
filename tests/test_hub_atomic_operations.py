# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — live atomic-operation ownership through the public hub
"""Keep one actual actor, live state and durable order across the ownership split."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import pytest

from synapse_channel.core.atomic_operations import OperationDraft
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.hub_ledger_guard import FindingQuota
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.state import SynapseState


async def test_replaced_live_state_uses_the_original_actor_and_caller_context(
    tmp_path: Path,
) -> None:
    """Neither state capture nor a replacement worker can enter through the extraction."""
    with EventStore(tmp_path / "live.db") as journal:
        hub = SynapseHub(journal=journal, anti_rollback_checkpoint=False)
        actor = hub.state_mutations
        original = hub.state
        current = SynapseState()
        caller = asyncio.current_task()
        thread = threading.get_ident()
        hub.state = current

        def mutate(candidate: SynapseState) -> object:
            """Run the real state mutation in the admitted caller context."""
            assert asyncio.current_task() is caller
            assert threading.get_ident() == thread
            return candidate.claim("A", "current")

        try:
            result = await hub.run_atomic_operation(
                {"sender": "A", "type": "claim", "idem_key": "current"},
                mutate,
                lambda _outcome: OperationDraft(
                    response={"task_id": "current"},
                    events=((EventKind.CLAIM, {"task_id": "current"}),),
                    intent={"family": "claim"},
                ),
            )
            assert result is not None and result.outcome == "inserted"
            assert hub.state_mutations is actor
            assert hub.state is current
            assert "current" in current.claims
            assert original.claims == {}
            assert len(journal.read_operations()) == 1
            assert journal.pending_operation_outbox_count() == 1
            await hub.settle_atomic_operation(
                {"sender": "A", "type": "claim", "idem_key": "current"}
            )
            assert journal.pending_operation_outbox_count() == 0
            assert hub.counters.operation_outbox_pending == 0
        finally:
            hub.close()


@pytest.mark.parametrize("refuse", [False, True])
async def test_foreign_subject_publishes_only_after_its_real_commit(
    tmp_path: Path, refuse: bool
) -> None:
    """A preparation failure leaves the quota, journal and live hub state untouched."""
    with EventStore(tmp_path / "quota.db") as journal:
        hub = SynapseHub(journal=journal, anti_rollback_checkpoint=False)
        quota = FindingQuota(2, {})
        actor = hub.state_mutations

        def prepare(_outcome: object) -> OperationDraft:
            """Observe publication order before returning or refusing the real draft."""
            assert quota.counts == {}
            assert journal.read_operations() == ()
            if refuse:
                raise OSError("evidence unavailable")
            return OperationDraft(
                response={"accepted": True},
                events=((EventKind.FINDING, {"author": "A"}),),
                intent={"family": "finding"},
            )

        async def execute() -> object:
            """Use the same public API and real copyable subject as finding handlers."""
            return await hub.run_atomic_operation(
                {"sender": "A", "type": "finding", "idem_key": "quota"},
                lambda candidate: candidate.reserve("A"),
                prepare,
                subject=quota,
                publish_candidate=quota.publish_from,
            )

        try:
            if refuse:
                with pytest.raises(OSError, match="evidence unavailable"):
                    await execute()
                assert quota.counts == {}
                assert journal.read_operations() == ()
                assert journal.read_all() == []
            else:
                await execute()
                assert quota.counts == {"A": 1}
                assert len(journal.read_operations()) == 1
                assert journal.pending_operation_outbox_count() == 1
            assert hub.state_mutations is actor
            assert hub.state.claims == {}
        finally:
            hub.close()
