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
from typing import Literal, NoReturn, cast

import pytest

from synapse_channel.core.atomic_operations import (
    OperationDraft,
    OperationRecord,
    canonical_request_digest,
)
from synapse_channel.core.journal import EventKind
from synapse_channel.core.message_auth import (
    MessageAuthKey,
    MessageReplayCache,
    VerificationResult,
    sign_frame,
    verify_frame,
)
from synapse_channel.core.persistence import (
    EventStore,
    OperationCommitResult,
    StoredOperation,
)
from synapse_channel.core.protected_write_result import parse_protected_write_result
from synapse_channel.core.state import SynapseState
from synapse_channel.core.state_transaction import SerializedStateMutationActor
from test_protected_write_proposal import LIMITS
from test_protected_write_result import _result


@pytest.mark.parametrize(
    "case",
    [
        "valid",
        "missing",
        "digest",
        "response",
        "numeric-type",
        "legacy",
        "invalid-digest",
        "empty-key",
    ],
)
def test_exact_predecessor_is_verified_before_any_child_event(tmp_path: Path, case: str) -> None:
    store = EventStore(tmp_path / "predecessor.db")
    parent = store.commit_operation(
        operation_key="parent",
        request_digest="a" * 64,
        response={"status": "completed", "step": 1},
        events=(("step", {}),),
        intent={},
    )
    expected = OperationRecord("parent", "a" * 64, parent.operation.response)
    if case == "missing":
        expected = OperationRecord("missing", "a" * 64, expected.response)
    elif case == "digest":
        expected = OperationRecord("parent", "b" * 64, expected.response)
    elif case == "response":
        expected = OperationRecord("parent", "a" * 64, {"status": "failed", "step": 1})
    elif case == "numeric-type":
        expected = OperationRecord("parent", "a" * 64, {"status": "completed", "step": True})
    elif case == "legacy":
        expected = OperationRecord("parent", None, expected.response)
    elif case == "invalid-digest":
        expected = OperationRecord("parent", "invalid", expected.response)
    elif case == "empty-key":
        expected = OperationRecord("", "a" * 64, expected.response)
    try:

        def commit() -> OperationCommitResult:
            return store.commit_operation(
                operation_key="child",
                request_digest="c" * 64,
                response={"status": "started"},
                events=(("child-step", {}),),
                intent={},
                required_predecessor=expected,
            )

        if case == "valid":
            assert commit().outcome == "inserted"
            assert commit().outcome == "replayed"
            assert store.count() == 4
        else:
            with pytest.raises(ValueError, match="predecessor"):
                commit()
            assert store.count() == 2
            assert len(store.read_operations()) == 1
    finally:
        store.close()


@pytest.mark.parametrize("durable", [False, True])
async def test_actor_propagates_worker_cancellation_without_publication(durable: bool) -> None:
    actor = SerializedStateMutationActor()
    state = SynapseState()

    def cancelled(_value: object) -> NoReturn:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await actor.run_atomic(
            state,
            lambda candidate: candidate.claim("seat", "cancelled-task")[:2],
            request_digest="c" * 64,
            lookup=lambda: None,
            prepare=lambda _result: (
                OperationDraft(response={}, events=(), intent={}) if durable else None
            ),
            commit=cancelled,
            remember=lambda _record: None,
            conflict=lambda _record: {},
            persist_uncommitted=cancelled,
        )
    assert state.claims == {}


def test_atomic_response_finalizer_binds_signed_nested_sequence_and_replays(tmp_path: Path) -> None:
    path = tmp_path / "signed-operation.db"
    store = EventStore(path)
    store.append("prior", {"retained": True})
    request, draft = _result("admit")
    del draft["auth"]
    cast(dict[str, object], draft["body"])["admission_sequence"] = None
    original_draft = json.dumps(draft, sort_keys=True)
    key = MessageAuthKey("authority", b"test-only-signing-key", frozenset({"EXAMPLE/authority"}))
    calls: list[tuple[int, ...]] = []

    def finalize(response: dict[str, object], sequences: tuple[int, ...]) -> dict[str, object]:
        calls.append(sequences)
        cast(dict[str, object], response["body"])["admission_sequence"] = sequences[0]
        return sign_frame(response, key=key, nonce="response", sequence=1, timestamp=1788649200.0)

    digest = canonical_request_digest(request)
    committed = store.commit_operation(
        operation_key="protected-response",
        request_digest=digest,
        response=draft,
        events=(("protected-admission", {"reservation_id": "reservation"}),),
        intent={"family": "protected-write"},
        finalize_response=finalize,
    )
    assert calls == [(2,)]
    assert json.dumps(draft, sort_keys=True) == original_draft
    assert committed.operation.response["body"]["admission_sequence"] == 2
    parsed = parse_protected_write_result(
        json.dumps(committed.operation.response),
        request=json.dumps(request),
        limits=LIMITS,
        reason_codes=frozenset(),
    )
    assert parsed.request_digest == digest
    assert (
        verify_frame(
            committed.operation.response,
            keys={key.key_id: key},
            replay_cache=MessageReplayCache(window_seconds=10.0, max_entries=32),
            now=1788649200.0,
            required_sender="EXAMPLE/authority",
        )
        is VerificationResult.OK
    )
    store.close()
    reopened = EventStore(path)
    try:
        replayed = reopened.commit_operation(
            operation_key="protected-response",
            request_digest=digest,
            response=draft,
            events=(("protected-admission", {"duplicate": True}),),
            intent={},
            finalize_response=finalize,
        )
        conflicted = reopened.commit_operation(
            operation_key="protected-response",
            request_digest="0" * 64,
            response=draft,
            events=(("protected-admission", {"changed": True}),),
            intent={},
            finalize_response=finalize,
        )
        assert replayed.outcome == "replayed" and conflicted.outcome == "conflict"
        assert (
            replayed.operation.response
            == committed.operation.response
            == conflicted.operation.response
        )
        assert calls == [(2,)]
        assert [event.kind for event in reopened.read_all()] == [
            "prior",
            "protected-admission",
            "idempotency",
        ]
        assert reopened.pending_operation_outbox_count() == 1
    finally:
        reopened.close()


@pytest.mark.parametrize("failure", ["raise", "nonfinite"])
def test_response_finalizer_failure_rolls_back_without_mutating_draft(
    tmp_path: Path, failure: str
) -> None:
    store = EventStore(tmp_path / "failed-finalizer.db")
    draft: dict[str, object] = {"body": {"sequence": None}}

    def fail(response: dict[str, object], sequences: tuple[int, ...]) -> dict[str, object]:
        cast(dict[str, object], response["body"])["sequence"] = sequences[0]
        if failure == "raise":
            raise OSError("signing unavailable")
        response["nonfinite"] = float("nan")
        return response

    try:
        with pytest.raises((OSError, ValueError)):
            store.commit_operation(
                operation_key="failure",
                request_digest="0" * 64,
                response=draft,
                events=(("mutation", {"candidate": True}),),
                intent={},
                finalize_response=fail,
            )
        assert draft == {"body": {"sequence": None}}
        assert store.read_all() == []
        assert store.read_operations() == ()
        assert store.pending_operation_outbox_count() == 0
        assert store.append("after-failure", {"usable": True}) == 1
    finally:
        store.close()


def test_response_finalizer_and_legacy_sequence_field_are_exclusive(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "exclusive-builder.db")
    try:
        with pytest.raises(ValueError, match="one atomic response"):
            store.commit_operation(
                operation_key="exclusive",
                request_digest="0" * 64,
                response={},
                events=(("mutation", {}),),
                intent={},
                response_event_seq_field="seq",
                finalize_response=lambda response, sequences: response,
            )
        assert store.read_all() == []
    finally:
        store.close()


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


@pytest.mark.parametrize("digest", [None, "a" * 64, "b" * 64])
@pytest.mark.parametrize("legacy", [False, True])
async def test_actor_requires_request_equality_unless_legacy_opted_in(
    digest: str | None, legacy: bool
) -> None:
    actor = SerializedStateMutationActor()
    state = SynapseState()
    record = OperationRecord("request-key", digest, {"type": "historical-response"})

    def unexpected(*_args: object) -> NoReturn:
        raise AssertionError("cached request must not mutate, prepare, commit or publish")

    execution = await actor.run_atomic(
        state,
        unexpected,
        request_digest="a" * 64,
        lookup=lambda: record,
        prepare=unexpected,
        commit=unexpected,
        remember=unexpected,
        conflict=lambda _record: {"error_code": "idempotency_conflict"},
        publish_candidate=unexpected,
        publish=unexpected,
        allow_legacy_digestless_replay=legacy,
    )
    replay = digest == "a" * 64 or (digest is None and legacy)
    assert execution.outcome == ("replayed" if replay else "conflict")
    assert execution.response == (
        record.response if replay else {"error_code": "idempotency_conflict"}
    )
    assert state.claims == {}


async def test_durable_only_actor_discards_candidate_without_draft() -> None:
    actor = SerializedStateMutationActor()
    state = SynapseState()

    def unexpected(*_args: object) -> NoReturn:
        raise AssertionError("durable-only operation cannot take uncommitted publication path")

    with pytest.raises(ValueError, match="durable response draft"):
        await actor.run_atomic(
            state,
            lambda candidate: candidate.claim("seat", "not-published"),
            request_digest="a" * 64,
            lookup=lambda: None,
            prepare=lambda _result: None,
            commit=unexpected,
            remember=unexpected,
            conflict=unexpected,
            publish_candidate=unexpected,
            persist_uncommitted=unexpected,
            publish=unexpected,
            require_committed_response=True,
        )
    assert state.claims == {}


async def test_actor_default_refuses_digestless_cached_response() -> None:
    actor = SerializedStateMutationActor()
    record = OperationRecord("request-key", None, {"type": "historical-response"})

    def unexpected(*_args: object) -> NoReturn:
        raise AssertionError("digest-less cached response cannot authorize execution")

    execution = await actor.run_atomic(
        SynapseState(),
        unexpected,
        request_digest="a" * 64,
        lookup=lambda: record,
        prepare=unexpected,
        commit=unexpected,
        remember=unexpected,
        conflict=lambda _record: {"error_code": "idempotency_conflict"},
        publish_candidate=unexpected,
    )
    assert execution.outcome == "conflict"
    assert execution.response == {"error_code": "idempotency_conflict"}


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


@pytest.mark.parametrize("cancel_count", [1, 2, 3])
async def test_actor_cancellation_publishes_uncommitted_persisted_candidate(
    cancel_count: int,
) -> None:
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
    for _ in range(cancel_count):
        task.cancel()
        await asyncio.sleep(0)
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
