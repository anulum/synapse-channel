# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — real journal reconstruction across event families
"""Exercise family reconstruction through persisted facts and the public replay API."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from synapse_channel.core.journal import (
    UnsupportedProtectedWriteHistoryError,
    record_chat,
    record_checkpoint,
    record_claim,
    record_handoff,
    record_idempotency,
    record_task_update,
    replay,
)
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.state import TaskClaim


@pytest.mark.parametrize(
    "record", [record_claim, record_task_update, record_checkpoint, record_handoff]
)
def test_claim_snapshot_family_preserves_scope_after_reopen(
    tmp_path: Path, record: Callable[[EventStore, TaskClaim], None]
) -> None:
    path = tmp_path / "snapshots.db"
    snapshot = TaskClaim(
        task_id="WORK/task",
        owner="WORK/seat",
        quota_principal="WORK/seat",
        note="retained work",
        claimed_at=1000.0,
        lease_expires_at=5000.0,
        worktree="repository",
        paths=("src/owned.py",),
        epoch=17,
        version=3,
    )
    with EventStore(path) as store:
        record(store, snapshot)
        store.append("external_audit", {"task_id": snapshot.task_id, "owner": "foreign"})
    with EventStore(path) as store:
        restored = replay(store, now=2000.0)
        assert restored.state.claims[snapshot.task_id] == snapshot
        assert restored.state.last_seen[snapshot.owner] == snapshot.claimed_at
        assert restored.chat_history == []


def test_replay_prefix_keeps_chat_numbering_and_latest_response_order(tmp_path: Path) -> None:
    path = tmp_path / "responses.db"
    with EventStore(path) as store:
        record_chat(store, {"sender": "WORK/seat", "payload": "first", "msg_id": 9})
        record_idempotency(store, "operation-one", {"status": "first"})
        record_idempotency(store, "operation-two", {"status": "second"})
        prefix = store.max_seq()
        record_chat(store, {"sender": "WORK/seat", "payload": "later", "msg_id": 4})
        record_idempotency(store, "operation-one", {"status": "updated"})
    with EventStore(path) as store:
        before = replay(store, up_to_seq=prefix, now=2000.0)
        after = replay(store, now=2000.0)
        assert [item["payload"] for item in before.chat_history] == ["first"]
        assert [item["payload"] for item in after.chat_history] == ["first", "later"]
        assert before.message_seq == after.message_seq == 9
        assert before.idempotency == [
            ("operation-one", {"status": "first"}),
            ("operation-two", {"status": "second"}),
        ]
        assert after.idempotency == [
            ("operation-two", {"status": "second"}),
            ("operation-one", {"status": "updated"}),
        ]


def test_unknown_protected_fact_refuses_after_an_ordinary_audit_fact(tmp_path: Path) -> None:
    path = tmp_path / "custody.db"
    with EventStore(path) as store:
        audit = store.append("external_audit", {"owner": "WORK/seat"})
        protected = store.append("protected_write_future", {"owner": "WORK/seat"})
    with EventStore(path) as store:
        assert replay(store, up_to_seq=audit, now=2000.0).state.claims == {}
        with pytest.raises(UnsupportedProtectedWriteHistoryError) as refused:
            replay(store, now=2000.0)
        assert refused.value.code == "unsupported_protected_write_history"
        assert f"sequence {protected}" in str(refused.value)
        assert store.count() == 2
