# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — durable atomic keyed-operation regressions

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal

import pytest

from synapse_channel.core.atomic_operations import OperationDraft, canonical_request_digest
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import (
    EventStore,
    OperationCommitResult,
    StoredOperation,
)
from synapse_channel.core.state import SynapseState
from synapse_channel.core.state_transaction import SerializedStateMutationActor


def _request(**extra: object) -> dict[str, object]:
    return {
        "sender": "SYNAPSE-CHANNEL/test-seat",
        "type": "claim",
        "idem_key": "operation-1",
        "task_id": "T1",
        "paths": ["src/a.py"],
        **extra,
    }


def test_request_digest_is_canonical_and_excludes_refresh_proofs() -> None:
    first = _request(
        timestamp="old",
        client_timestamp="old-client",
        auth={"nonce": "old-secret"},
        signature="old-signature",
    )
    second = dict(reversed(list(_request().items())))
    second.update(
        timestamp="new",
        client_timestamp="new-client",
        auth={"nonce": "fresh-secret"},
        signature="fresh-signature",
    )

    assert canonical_request_digest(first) == canonical_request_digest(second)
    assert canonical_request_digest(first) != canonical_request_digest(
        {**first, "paths": ["src/changed.py"]}
    )
    with pytest.raises(ValueError, match="Out of range float"):
        canonical_request_digest({**first, "ttl_seconds": float("nan")})


def test_commit_operation_inserts_replays_and_conflicts_without_second_mutation(
    tmp_path: Path,
) -> None:
    store = EventStore(tmp_path / "operations.db")
    request = _request()
    digest = canonical_request_digest(request)
    response = {"type": "claim_granted", "task_id": "T1", "timestamp": "fixed"}

    inserted = store.commit_operation(
        operation_key="seat\x00claim\x00operation-1",
        request_digest=digest,
        response=response,
        events=((EventKind.CLAIM, {"task_id": "T1", "owner": "seat"}),),
        intent={"family": "claim"},
    )
    replayed = store.commit_operation(
        operation_key="seat\x00claim\x00operation-1",
        request_digest=digest,
        response={"type": "must-not-replace"},
        events=((EventKind.CLAIM, {"task_id": "duplicate"}),),
        intent={"family": "claim"},
    )
    conflicted = store.commit_operation(
        operation_key="seat\x00claim\x00operation-1",
        request_digest=canonical_request_digest({**request, "task_id": "changed"}),
        response={"type": "must-not-replace"},
        events=((EventKind.CLAIM, {"task_id": "changed"}),),
        intent={"family": "claim"},
    )

    assert inserted.outcome == "inserted"
    assert replayed.outcome == "replayed"
    assert conflicted.outcome == "conflict"
    assert replayed.operation.response == response
    assert conflicted.operation.response == response
    assert [event.kind for event in store.read_all()] == [
        EventKind.CLAIM,
        EventKind.IDEMPOTENCY,
    ]
    assert inserted.operation.first_event_seq == 1
    assert inserted.operation.commit_seq == 2
    encoded = json.dumps(response, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    assert inserted.operation.response_sha256 == hashlib.sha256(encoded.encode("ascii")).hexdigest()
    assert store.pending_operation_outbox_count() == 1
    assert store.pending_operation_intents() == (
        ("seat\x00claim\x00operation-1", {"family": "claim"}),
    )
    store.close()


@pytest.mark.parametrize(
    "stage",
    [
        "after_legacy_event_insert",
        "after_operation_insert",
        "after_operation_outbox_insert",
        "before_commit",
    ],
)
def test_precommit_fault_rolls_back_every_atomic_surface(tmp_path: Path, stage: str) -> None:
    db = tmp_path / f"fault-{stage}.db"
    store = EventStore(db)

    def fail(observed: str) -> None:
        if observed == stage:
            raise OSError("injected precommit failure")

    with pytest.raises(OSError, match="injected precommit"):
        store.commit_operation(
            operation_key="seat\x00claim\x00fault",
            request_digest=canonical_request_digest(_request()),
            response={"type": "claim_granted"},
            events=((EventKind.CLAIM, {"task_id": "T1"}),),
            intent={"family": "claim"},
            stage_hook=fail,
        )

    assert store.read_all() == []
    assert store.read_operations() == ()
    assert store.pending_operation_outbox_count() == 0
    store.close()

    reopened = EventStore(db)
    assert reopened.read_all() == []
    assert reopened.read_operations() == ()
    reopened.close()


def test_postcommit_fault_leaves_one_replayable_winner(tmp_path: Path) -> None:
    db = tmp_path / "postcommit.db"
    store = EventStore(db)

    def fail(stage: str) -> None:
        if stage == "after_commit":
            raise SystemExit("simulated process death")

    with pytest.raises(SystemExit, match="process death"):
        store.commit_operation(
            operation_key="seat\x00claim\x00postcommit",
            request_digest=canonical_request_digest(_request()),
            response={"type": "claim_granted", "task_id": "T1"},
            events=((EventKind.CLAIM, {"task_id": "T1"}),),
            intent={"family": "claim"},
            stage_hook=fail,
        )
    store.close()

    reopened = EventStore(db)
    assert len(reopened.read_operations()) == 1
    assert [event.kind for event in reopened.read_all()] == [
        EventKind.CLAIM,
        EventKind.IDEMPOTENCY,
    ]
    reopened.close()


def test_guard_response_sequence_and_compaction_reference_are_atomic(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "guard.db")
    committed = store.commit_operation(
        operation_key="seat\x00guard_denial\x00guard-1",
        request_digest=canonical_request_digest(
            {
                "sender": "seat",
                "type": "guard_denial",
                "idem_key": "guard-1",
                "call_sha256": "a" * 64,
            }
        ),
        response={"type": "guard_denial_recorded", "audit_seq": 0},
        events=((EventKind.GUARD_DENIAL, {"call_sha256": "a" * 64}),),
        intent={"family": "guard_denial"},
        response_event_seq_field="audit_seq",
    )

    assert committed.operation.response["audit_seq"] == committed.operation.first_event_seq
    assert store.delete([committed.operation.first_event_seq, committed.operation.commit_seq]) == 0
    assert store.count() == 2
    store.mark_operation_intent_delivered(
        committed.operation.operation_key,
        f"local:{committed.operation.response_sha256}",
    )
    assert store.pending_operation_outbox_count() == 0
    store.close()


def test_competing_store_connections_converge_on_one_winner(tmp_path: Path) -> None:
    db = tmp_path / "race.db"
    first = EventStore(db)
    second = EventStore(db)
    barrier = threading.Barrier(2)

    def commit(store: EventStore, task_id: str) -> str:
        barrier.wait(timeout=2)
        result = store.commit_operation(
            operation_key="seat\x00claim\x00race",
            request_digest=canonical_request_digest({**_request(), "task_id": task_id}),
            response={"type": "claim_granted", "task_id": task_id},
            events=((EventKind.CLAIM, {"task_id": task_id}),),
            intent={"family": "claim"},
        )
        return result.outcome

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_result = executor.submit(commit, first, "T1")
        second_result = executor.submit(commit, second, "T2")
        outcomes = {first_result.result(), second_result.result()}

    assert outcomes == {"inserted", "conflict"}
    assert len(first.read_operations()) == 1
    assert [event.kind for event in first.read_all()] == [EventKind.CLAIM, EventKind.IDEMPOTENCY]
    first.close()
    second.close()


@pytest.mark.parametrize("outcome", ["replayed", "conflict"])
async def test_actor_discards_candidate_when_database_has_a_winner(
    outcome: Literal["replayed", "conflict"],
) -> None:
    actor = SerializedStateMutationActor()
    state = SynapseState()
    response = {"type": "claim_granted", "task_id": "WINNER"}
    stored = StoredOperation(
        "seat\x00claim\x00race",
        "a" * 64,
        response,
        "b" * 64,
        1,
        2,
        1.0,
    )
    published: list[bool] = []

    execution = await actor.run_atomic(
        state,
        lambda candidate: candidate.claim("seat", "LOSER"),
        request_digest="a" * 64,
        lookup=lambda: None,
        prepare=lambda _result: OperationDraft(
            response={"type": "loser"},
            events=((EventKind.CLAIM, {"task_id": "LOSER"}),),
            intent={"family": "claim"},
        ),
        commit=lambda _draft: OperationCommitResult(outcome, stored),
        remember=lambda _record: None,
        conflict=lambda _record: {"error_code": "idempotency_conflict"},
        publish=lambda _result: published.append(True),
    )

    assert execution.outcome == outcome
    assert "LOSER" not in state.claims
    assert published == []
    if outcome == "replayed":
        assert execution.response == response
    else:
        assert execution.response == {"error_code": "idempotency_conflict"}


async def test_actor_cancellation_publishes_uncommitted_persisted_candidate() -> None:
    actor = SerializedStateMutationActor()
    state = SynapseState()
    started = threading.Event()
    finish = threading.Event()
    published: list[bool] = []

    def persist_uncommitted(_result: tuple[bool, str]) -> None:
        started.set()
        assert finish.wait(timeout=2)

    task = asyncio.create_task(
        actor.run_atomic(
            state,
            lambda candidate: candidate.claim("seat", "T1")[:2],
            request_digest="a" * 64,
            lookup=lambda: None,
            prepare=lambda _result: None,
            commit=lambda _draft: (_ for _ in ()).throw(AssertionError("no draft to commit")),
            remember=lambda _record: None,
            conflict=lambda _record: {"error_code": "idempotency_conflict"},
            persist_uncommitted=persist_uncommitted,
            publish=lambda _result: published.append(True),
        )
    )
    assert await asyncio.to_thread(started.wait, 1)
    task.cancel()
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert "T1" in state.claims
    assert published == [True]

    plain_state = SynapseState()
    plain = await actor.run_atomic(
        plain_state,
        lambda candidate: candidate.claim("seat", "T2")[:2],
        request_digest="b" * 64,
        lookup=lambda: None,
        prepare=lambda _result: None,
        commit=lambda _draft: (_ for _ in ()).throw(AssertionError("no draft to commit")),
        remember=lambda _record: None,
        conflict=lambda _record: {"error_code": "idempotency_conflict"},
    )
    assert plain.outcome == "uncommitted"
    assert "T2" in plain_state.claims


@pytest.mark.parametrize("limit", [0, True, 10_001])
def test_pending_operation_intent_limit_is_bounded(tmp_path: Path, limit: object) -> None:
    store = EventStore(tmp_path / "limit.db")
    with pytest.raises(ValueError, match="operation outbox limit"):
        store.pending_operation_intents(limit=limit)  # type: ignore[arg-type]
    store.close()
