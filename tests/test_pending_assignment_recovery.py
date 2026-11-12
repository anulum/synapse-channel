# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — historical assignment journal and rollback journeys
"""Preserve native provenance and recipient indexes across replay and failed writes."""

from __future__ import annotations

import sqlite3
import time
from contextlib import closing
from pathlib import Path

import pytest

from synapse_channel.core.assignment_origin import ASSIGNMENT_ORIGIN_FIELD
from synapse_channel.core.journal import (
    EventKind,
    record_claim,
    record_handoff,
    record_ledger_task,
    record_release,
    replay,
)
from synapse_channel.core.ledger import Blackboard
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.state import SynapseState
from synapse_channel.core.state_transaction import durable_state_transaction
from synapse_channel.core.task_causality import task_record_payload
from test_pending_index import claim, task


def test_historical_task_origin_survives_replay_and_current_reassignment_does_not_resurface(
    tmp_path: Path,
) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        old = task(version=3)
        store.append(EventKind.LEDGER_TASK, old.as_dict())
        first = replay(store, now=100)
        before = first.blackboard.pending_assignments.rows("ALPHA/worker", limit=10)
        assert len(before) == 1
        assert first.blackboard.update_task("ALPHA/old", status="blocked", now=101)[0]
        record_ledger_task(store, first.blackboard.tasks["ALPHA/old"])
        event = store.read_since(0, kinds=(EventKind.LEDGER_TASK,))[-1]
        assert event.payload[ASSIGNMENT_ORIGIN_FIELD]["legacy"] is True
        assert ASSIGNMENT_ORIGIN_FIELD not in task_record_payload(event.payload)
        restored = replay(store, now=102)
        assert restored.blackboard.pending_assignments.rows("ALPHA/worker", limit=10) == before
        assert restored.blackboard.update_task("ALPHA/old", suggested_owner="ALPHA/other", now=103)[
            0
        ]
        record_ledger_task(store, restored.blackboard.tasks["ALPHA/old"])
        assert not replay(store, now=104).blackboard.pending_assignments.entries
        board = Blackboard()
        assert board.post_task(
            task_id="ALPHA/current",
            title="current",
            author="ALPHA/worker",
            suggested_owner="ALPHA/worker",
            project="ALPHA",
            now=105,
        )[0]
        record_ledger_task(store, board.tasks["ALPHA/current"])
        assert not replay(store, now=106).blackboard.pending_assignments.entries
    finally:
        store.close()


def test_actual_historical_handoff_remains_distinct_from_ordinary_claim_and_release(
    tmp_path: Path,
) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        old = claim()
        store.append(EventKind.HANDOFF, old.as_persisted_dict())
        ordinary = claim(task_id="ALPHA/direct", epoch=8)
        record_claim(store, ordinary)
        restored = replay(store, now=100)
        before = restored.state.pending_handoffs.rows("ALPHA/worker", limit=10)
        assert len(before) == 1 and before[0][0] == "ALPHA/handoff"
        assert restored.state.claim("ALPHA/worker", "ALPHA/handoff", now=101)[0]
        record_claim(store, restored.state.claims["ALPHA/handoff"])
        assert (
            replay(store, now=102).state.pending_handoffs.rows("ALPHA/worker", limit=10) == before
        )
        record_release(store, "ALPHA/handoff")
        final = replay(store, now=103)
        assert not final.state.pending_handoffs.entries and "ALPHA/direct" in final.state.claims
    finally:
        store.close()


def test_real_durable_release_failure_restores_historical_recipient_index(tmp_path: Path) -> None:
    path = tmp_path / "hub.db"
    store = EventStore(path)
    now = time.time()
    original = claim(claimed_at=now, lease_expires_at=now + 600)
    state = SynapseState()
    state.restore_claim(original, legacy_handoff=True)
    state.reindex_leases()
    before = state.pending_handoffs.rows("ALPHA/worker", limit=10)
    try:
        with closing(sqlite3.connect(path)) as database, database:
            database.execute(
                "CREATE TRIGGER fail_release BEFORE INSERT ON events BEGIN "
                "SELECT RAISE(ABORT,'injected disk refusal'); END"
            )
        with pytest.raises(sqlite3.IntegrityError):
            with durable_state_transaction(state, "ALPHA/handoff", enabled=True):
                assert state.release("ALPHA/worker", "ALPHA/handoff")[0]
                assert not state.pending_handoffs.entries
                record_release(store, "ALPHA/handoff")
        assert state.claims["ALPHA/handoff"] is original
        assert state.pending_handoffs.rows("ALPHA/worker", limit=10) == before
    finally:
        store.close()


def test_a_failed_write_after_the_lease_ran_out_leaves_no_index_entry(tmp_path: Path) -> None:
    """A rollback does not bring back a claim whose lease has run out, nor its index entries.

    The durable append is refused by the store. The claim's lease is already in the past,
    so the rollback keeps the claim gone; the owned-work index and the historical-handoff
    index must then hold nothing for it either.
    """
    path = tmp_path / "hub.db"
    store = EventStore(path)
    now = time.time()
    lapsed = claim(claimed_at=now - 600, lease_expires_at=now - 1)
    state = SynapseState()
    state.restore_claim(lapsed, legacy_handoff=True)
    assert "ALPHA/handoff" in state.owned_claims.entries
    assert "ALPHA/handoff" in state.pending_handoffs.entries
    try:
        with closing(sqlite3.connect(path)) as database, database:
            database.execute(
                "CREATE TRIGGER fail_release BEFORE INSERT ON events BEGIN "
                "SELECT RAISE(ABORT,'injected disk refusal'); END"
            )
        with pytest.raises(sqlite3.IntegrityError):
            with durable_state_transaction(state, "ALPHA/handoff", enabled=True):
                record_release(store, "ALPHA/handoff")
        assert "ALPHA/handoff" not in state.claims
        assert state.owned_claims.entries == {}
        assert state.pending_handoffs.entries == {}
        assert state.pending_handoffs.rows("ALPHA/worker", limit=10) == ()
    finally:
        store.close()


def test_current_notified_handoff_does_not_become_historical_on_replay(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        record_handoff(store, claim(), actor="ALPHA/giver")
        restored = replay(store, now=100)
        assert "ALPHA/handoff" in restored.state.claims
        assert not restored.state.pending_handoffs.entries
    finally:
        store.close()
