# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — atomic operation settlement counted once on a real journalled hub
"""Count each settled intent once; ignore unkeyed or unknown frames; bind subjects."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from synapse_channel.core.atomic_operations import OperationDraft
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore


def _claim(key: str, task_id: str) -> dict[str, Any]:
    return {"sender": "A", "type": "claim", "idem_key": key, "task_id": task_id}


async def _commit(hub: SynapseHub, frame: dict[str, Any]) -> None:
    task_id = frame["task_id"]

    def prepare(_result: object) -> OperationDraft:
        return OperationDraft(
            response={"task_id": task_id},
            events=((EventKind.CLAIM, {"task_id": task_id}),),
            intent={"family": "claim"},
        )

    execution = await hub.run_atomic_operation(
        frame, lambda state: state.claim("A", task_id), prepare
    )
    assert execution is not None
    assert execution.outcome == "inserted"


async def test_each_intent_settles_once_and_unknown_frames_change_nothing(
    tmp_path: Path,
) -> None:
    """A repeated settlement is ignored instead of decrementing the outbox again."""
    store = EventStore(tmp_path / "hub.db")
    try:
        hub = SynapseHub(journal=store, anti_rollback_checkpoint=False)
        first, second = _claim("k-1", "T-1"), _claim("k-2", "T-2")
        await _commit(hub, first)
        await _commit(hub, second)
        assert (hub.counters.operation_outbox_pending, store.pending_operation_outbox_count()) == (
            2,
            2,
        )
        await hub.settle_atomic_operation(first)
        assert (hub.counters.operation_outbox_pending, store.pending_operation_outbox_count()) == (
            1,
            1,
        )
        await hub.settle_atomic_operation(first)
        await hub.settle_atomic_operation({"sender": "A", "type": "claim", "task_id": "T-1"})
        await hub.settle_atomic_operation(_claim("never-committed", "T-9"))
        assert (hub.counters.operation_outbox_pending, store.pending_operation_outbox_count()) == (
            1,
            1,
        )
    finally:
        store.close()


async def test_the_store_reports_whether_a_settlement_was_new(tmp_path: Path) -> None:
    """The first binding settles; the same receipt again is an idempotent no-op."""
    store = EventStore(tmp_path / "hub.db")
    try:
        hub = SynapseHub(journal=store, anti_rollback_checkpoint=False)
        frame = _claim("k-1", "T-1")
        await _commit(hub, frame)
        (pending,) = store.pending_operation_intents()
        stored = store.get_operation(pending.operation_key)
        assert stored is not None
        receipt = f"local:{stored.response_sha256}"
        assert store.mark_operation_intent_delivered(pending.operation_key, receipt) is True
        assert store.mark_operation_intent_delivered(pending.operation_key, receipt) is False
        with pytest.raises(KeyError, match="absent or already settled"):
            store.mark_operation_intent_delivered(pending.operation_key, "local:other")
        assert store.pending_operation_outbox_count() == 0
    finally:
        store.close()


async def test_a_foreign_subject_needs_its_own_candidate_publisher(tmp_path: Path) -> None:
    """A keyed operation on a non-state subject without a publisher commits nothing."""
    store = EventStore(tmp_path / "hub.db")
    try:
        hub = SynapseHub(journal=store, anti_rollback_checkpoint=False)
        with pytest.raises(
            ValueError, match="^a non-state atomic subject requires a candidate publisher$"
        ):
            await hub.run_atomic_operation(
                _claim("k-subject", "T-subject"),
                lambda _subject: None,
                lambda _result: None,
                subject=object(),
            )
        assert store.read_operations() == ()
        assert store.read_all() == []
    finally:
        store.close()
