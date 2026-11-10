# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — retain an existing operation projection receipt
"""Leave another transport's actual settlement intact during local projection."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from synapse_channel.core.atomic_operations import OperationDraft
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore


async def test_local_settlement_retains_another_transport_receipt(tmp_path: Path) -> None:
    """Exercise public settlement against a genuinely conflicting durable receipt."""
    path = tmp_path / "journal.db"
    with EventStore(path) as journal:
        hub = SynapseHub(journal=journal, anti_rollback_checkpoint=False)
        frame = {"sender": "A", "type": "claim", "idem_key": "settled", "task_id": "T"}

        def prepare(_result: object) -> OperationDraft:
            """Retain the actual claim and its evidence intent in one transaction."""
            return OperationDraft(
                response={"task_id": "T"},
                events=((EventKind.CLAIM, {"task_id": "T"}),),
                intent={"family": "claim"},
            )

        try:
            execution = await hub.run_atomic_operation(
                frame, lambda state: state.claim("A", "T"), prepare
            )
            assert execution is not None and execution.outcome == "inserted"
            (pending,) = journal.pending_operation_intents()
            stored = journal.get_operation(pending.operation_key)
            assert stored is not None
            assert journal.mark_operation_intent_delivered(pending.operation_key, "peer:receipt")
            with pytest.raises(KeyError, match="absent or already settled"):
                journal.mark_operation_intent_delivered(
                    pending.operation_key, f"local:{stored.response_sha256}"
                )
            counters_before = hub.counters.operation_outbox_pending
            events_before = journal.read_all()
            await hub.settle_atomic_operation(frame)
            assert hub.counters.operation_outbox_pending == counters_before == 1
            assert journal.pending_operation_outbox_count() == 0
            assert journal.pending_operation_intents() == ()
            assert len(journal.read_operations()) == 1
            assert journal.read_all() == events_before
            assert journal.get_operation(pending.operation_key) == stored
            with closing(sqlite3.connect(path)) as connection:
                receipt = connection.execute(
                    "SELECT receipt_id FROM operation_outbox WHERE operation_key = ?",
                    (pending.operation_key,),
                ).fetchone()
            assert receipt == ("peer:receipt",)
        finally:
            hub.close()
