# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — parent-bound recovery admission
from __future__ import annotations

import asyncio
import json
import threading
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from synapse_channel.core.atomic_operations import (
    OperationDraft,
    OperationRecord,
    canonical_request_digest,
)
from synapse_channel.core.journal import record_claim, replay
from synapse_channel.core.message_auth import MessageAuthKey, sign_frame
from synapse_channel.core.persistence import EventStore, OperationCommitResult
from synapse_channel.core.protected_write_admission import ProtectedAdmissionContext
from synapse_channel.core.protected_write_recovery_journal import (
    RECOVERY_EVENT_KIND,
    finalize_protected_recovery,
    protected_recovery_event_payload,
    restore_protected_recovery,
)
from synapse_channel.core.protected_write_request import protected_write_operation_key
from synapse_channel.core.protected_write_transition_journal import (
    TRANSITION_EVENT_KIND,
    finalize_protected_transition,
    protected_transition_event_payload,
)
from synapse_channel.core.state import SynapseState
from synapse_channel.core.state_transaction import SerializedStateMutationActor
from test_protected_write_admission import CONTEXT
from test_protected_write_admission_journal import POLICY, _committed
from test_protected_write_lifecycle import _frames
from test_protected_write_recovery import _inputs, _sync
from test_protected_write_transition_journal import _begin, _settle, _state

# Controlled retained-evidence verifier; no physical quiescence claim.
# Unit-only positive recovery callback; activation needs operator-reviewed predecessor and
# candidate files after interruption.
RECOVERY_POLICY = replace(POLICY, verify_recovery=lambda _p, _a: True)


def _child_transition(
    store: EventStore, state: SynapseState, child_id: str, verb: str, *, known: bool = False
) -> None:
    previous = state.protected_write_reservations[child_id]
    seq = store.read_all()[-1].seq + 1
    origin = json.loads(previous.admission.request_bytes)
    old = json.loads(previous.result_bytes)["body"]
    changes: dict[str, object] = {"disposition": "accepted"}
    if verb == "begin":
        changes.update(operation_phase="executing", begin_sequence=seq)
    else:
        changes.update(
            operation_phase="settled" if known else "recovery_required",
            outcome="committed" if known else "unknown",
            settlement_sequence=seq,
        )
    request, response, context = _frames(previous, verb, seq, **changes)
    for field in (
        "transaction_id",
        "proposal_sha256",
        "authority_id",
        "authority_continuity",
        "enrollment_revision",
        "target",
    ):
        request[field] = origin[field]
    request["sender"] = old["writer_principal"]
    request["body"].update(reservation_id=child_id, writer_incarnation=old["writer_incarnation"])
    if verb == "settle":
        request["body"]["outcome"] = "committed" if known else "unknown"
        response["body"]["evidence_reference"] = request["body"]["quiescence_reference"]
    for field in (
        "transaction_id",
        "proposal_sha256",
        "authority_id",
        "authority_continuity",
        "enrollment_revision",
    ):
        response[field] = request[field]
    response["target"] = request["sender"]
    response["body"]["request_digest"] = canonical_request_digest(request)
    context = replace(context, principal=request["sender"])
    raw = json.dumps(request)
    # Unit-only positive quiescence callback; activation needs descendant and descriptor fencing
    # plus stable file closure.
    policy = replace(RECOVERY_POLICY, verify_quiescence=lambda _a, _r, _s: True)
    candidate = deepcopy(state)
    store.commit_operation(
        operation_key=protected_write_operation_key(
            raw,
            limits=policy.limits,
            authenticated_principal=context.principal,
            authority_id=origin["authority_id"],
            authority_continuity=origin["authority_continuity"],
        ),
        request_digest=canonical_request_digest(request),
        response=response,
        events=(
            (
                TRANSITION_EVENT_KIND,
                protected_transition_event_payload(previous, raw, context=context, policy=policy),
            ),
        ),
        intent={"family": "recovery-child-transition"},
        finalize_response=lambda draft, seqs: finalize_protected_transition(
            draft,
            seqs,
            candidate=candidate,
            previous=previous,
            request=raw,
            context=context,
            policy=policy,
            sign_response=lambda frame: sign_frame(
                frame,
                key=MessageAuthKey("authority", b"child-transition-test-only"),
                nonce=f"child-transition-{seq}",
                sequence=seq,
                timestamp=CONTEXT.now,
            ),
        ),
    )
    state.publish_from(candidate)


def test_same_service_recovers_again_and_restarts_with_entire_ancestor_domain(
    tmp_path: Path,
) -> None:
    store, state, request, response, context = _setup(tmp_path)
    candidate, _ = _commit(store, state, request, response, context)
    state.publish_from(candidate)
    first_child = "recovery-reservation"
    _child_transition(store, state, first_child, "begin")
    _child_transition(store, state, first_child, "settle")
    first_custody = state.protected_claim_custody[first_child]
    request["request_id"] = "second-recovery"
    request["transaction_id"] = "second-recovery-transaction"
    request["body"]["parent_reservation_id"] = first_child
    claim = replace(
        state.claims["recovery-claim"],
        task_id="second-recovery-claim",
        worktree="/second-service-claim",
    )
    record_claim(store, claim)
    state.claims[claim.task_id] = claim
    request["body"]["proposal"]["claims"][0]["task_id"] = claim.task_id
    request["body"]["proposal"]["operations"][0]["claim_task_id"] = claim.task_id
    context = replace(context, writer_incarnation="second-recovery-incarnation")
    response["body"].update(
        reservation_id="second-recovery-reservation", writer_incarnation=context.writer_incarnation
    )
    _sync(request, response)
    candidate, _ = _commit(store, state, request, response, context)
    state.publish_from(candidate)
    second = "second-recovery-reservation"
    assert state.protected_write_reservations[second].inherited_custody == first_custody
    store.close()
    store = EventStore(tmp_path / "admission-replay.db")
    state = replay(
        store, protected_write_policies={"enrollment": RECOVERY_POLICY}, now=CONTEXT.now
    ).state
    assert state.protected_write_recoveries == {"reservation": first_child, first_child: second}
    assert len(state.protected_claim_custody[second]) == 3
    _child_transition(store, state, second, "begin")
    _child_transition(store, state, second, "settle", known=True)
    store.close()
    store = EventStore(tmp_path / "admission-replay.db")
    # Unit-only positive quiescence callback; activation needs descendant and descriptor fencing
    # plus stable file closure.
    policy = replace(RECOVERY_POLICY, verify_quiescence=lambda _a, _r, _s: True)
    restored = replay(store, protected_write_policies={"enrollment": policy}, now=CONTEXT.now).state
    assert restored.protected_claim_custody == {}
    assert restored.protected_write_recoveries == state.protected_write_recoveries
    assert restored.protected_write_reservations == state.protected_write_reservations
    store.close()


@pytest.mark.parametrize("fail", [False, True])
@pytest.mark.parametrize("cancel_count", [1, 2])
async def test_actor_cancellation_keeps_recovery_commit_and_publication_consistent(
    tmp_path: Path, fail: bool, cancel_count: int
) -> None:
    store, state, request, response, context = _setup(tmp_path)
    parent = state.protected_write_reservations["reservation"]
    raw = json.dumps(request)
    key = protected_write_operation_key(
        raw,
        limits=POLICY.limits,
        authenticated_principal=context.author_principal,
        authority_id=context.authority_id,
        authority_continuity=context.authority_continuity,
    )
    digest = canonical_request_digest(request)
    actor = SerializedStateMutationActor()
    entered = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()

    def prepare(candidate: SynapseState) -> OperationDraft:
        return OperationDraft(
            response=response,
            events=(
                (
                    RECOVERY_EVENT_KIND,
                    protected_recovery_event_payload(
                        parent, raw, context=context, policy=RECOVERY_POLICY
                    ),
                ),
            ),
            intent={"family": "recovery-actor"},
            finalize_response=lambda draft, seqs: finalize_protected_recovery(
                draft,
                seqs,
                candidate=candidate,
                parent=parent,
                request=raw,
                context=context,
                policy=RECOVERY_POLICY,
                sign_response=lambda frame: sign_frame(
                    frame,
                    key=MessageAuthKey("authority", b"actor-test-only"),
                    nonce="recovery-actor",
                    sequence=9,
                    timestamp=CONTEXT.now,
                ),
            ),
        )

    def stage(name: str) -> None:
        if name == "before_commit":
            loop.call_soon_threadsafe(entered.set)
            if not release.wait(5):
                raise TimeoutError("test did not release commit")
            if fail:
                raise OSError("actor commit failure")

    def commit(draft: OperationDraft) -> OperationCommitResult:
        return store.commit_operation(
            operation_key=key,
            request_digest=digest,
            response=draft.response,
            events=draft.events,
            intent=draft.intent,
            finalize_response=draft.finalize_response,
            stage_hook=stage,
        )

    def lookup() -> OperationRecord | None:
        operation = store.get_operation(key)
        return (
            None
            if operation is None
            else OperationRecord(key, operation.request_digest, operation.response)
        )

    task = asyncio.create_task(
        actor.run_atomic(
            state,
            lambda candidate: candidate,
            request_digest=digest,
            lookup=lookup,
            prepare=prepare,
            commit=commit,
            remember=lambda _record: None,
            conflict=lambda _record: {"error_code": "conflict"},
            require_committed_response=True,
        )
    )
    try:
        await asyncio.wait_for(entered.wait(), 5)
        for _ in range(cancel_count):
            task.cancel()
            await asyncio.sleep(0)
        assert state.protected_write_recoveries == {}
        release.set()
        with pytest.raises(OSError if fail else asyncio.CancelledError):
            await task
        assert state.protected_write_recoveries == (
            {} if fail else {"reservation": "recovery-reservation"}
        )
        assert store.count() == (8 if fail else 10)
        restored = replay(
            store, protected_write_policies={"enrollment": RECOVERY_POLICY}, now=CONTEXT.now
        ).state
        assert restored.protected_claim_custody == state.protected_claim_custody
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        store.close()


def _setup(
    tmp_path: Path,
) -> tuple[EventStore, SynapseState, dict[str, Any], dict[str, Any], ProtectedAdmissionContext]:
    store = _committed(tmp_path)
    state = _state(store)
    _begin(store, state)
    _settle(store, state, "unknown")
    _, request, response, context, claim = _inputs()
    claim = replace(claim, worktree="/service", paths=("authority",))
    record_claim(store, claim)
    state.claims[claim.task_id] = claim
    _sync(request, response)
    return store, state, request, response, context


def _commit(
    store: EventStore,
    state: SynapseState,
    request: dict[str, Any],
    response: dict[str, Any],
    context: ProtectedAdmissionContext,
    *,
    fail: bool = False,
) -> tuple[SynapseState, OperationCommitResult]:
    raw = json.dumps(request)
    parent = state.protected_write_reservations[request["body"]["parent_reservation_id"]]
    candidate = deepcopy(state)

    def stage(name: str) -> None:
        if name == "before_commit" and fail:
            raise OSError("recovery commit interrupted")

    receipt = store.commit_operation(
        operation_key=protected_write_operation_key(
            raw,
            limits=POLICY.limits,
            authenticated_principal=context.author_principal,
            authority_id=context.authority_id,
            authority_continuity=context.authority_continuity,
        ),
        request_digest=canonical_request_digest(request),
        response=response,
        events=(
            (
                RECOVERY_EVENT_KIND,
                protected_recovery_event_payload(
                    parent, raw.encode(), context=context, policy=RECOVERY_POLICY
                ),
            ),
        ),
        intent={"family": "protected-recovery"},
        finalize_response=lambda draft, seqs: finalize_protected_recovery(
            draft,
            seqs,
            candidate=candidate,
            parent=parent,
            request=raw,
            context=context,
            policy=RECOVERY_POLICY,
            sign_response=lambda frame: sign_frame(
                frame,
                key=MessageAuthKey("authority", b"recovery-test-only"),
                nonce="recovery-nonce",
                sequence=9,
                timestamp=CONTEXT.now,
            ),
        ),
        stage_hook=stage,
    )
    return candidate, receipt


def test_recovery_commit_retry_and_reopen_restore_exact_lineage(tmp_path: Path) -> None:
    store, state, request, response, context = _setup(tmp_path)
    candidate, receipt = _commit(store, state, request, response, context)
    assert state.protected_write_recoveries == {}
    state.publish_from(candidate)
    _, retry = _commit(store, state, request, response, context)
    assert retry.outcome == "replayed"
    assert retry.operation.response == receipt.operation.response
    assert store.count() == 10
    assert receipt.operation.response["body"]["admission_sequence"] == 9
    store.close()
    store = EventStore(tmp_path / "admission-replay.db")
    restored = replay(
        store, protected_write_policies={"enrollment": RECOVERY_POLICY}, now=CONTEXT.now
    ).state
    assert restored.protected_write_recoveries == {"reservation": "recovery-reservation"}
    assert restored.protected_write_reservations == state.protected_write_reservations
    assert restored.protected_claim_custody == state.protected_claim_custody
    with pytest.raises(ValueError, match="evidence policy"):
        replay(store, protected_write_policies={"enrollment": POLICY}, now=CONTEXT.now)
    with pytest.raises(ValueError, match="complete matching operation"):
        replay(
            store,
            protected_write_policies={"enrollment": RECOVERY_POLICY},
            now=CONTEXT.now,
            up_to_seq=9,
        )
    store.close()


def test_recovery_failed_commit_publishes_nothing(tmp_path: Path) -> None:
    store, state, request, response, context = _setup(tmp_path)
    before = deepcopy(state)
    with pytest.raises(OSError, match="interrupted"):
        _commit(store, state, request, response, context, fail=True)
    assert store.count() == 8
    assert state.protected_claim_custody == before.protected_claim_custody
    assert state.protected_write_recoveries == {}
    store.close()


@pytest.mark.parametrize(
    "case",
    [
        "kind",
        "fields",
        "schema",
        "sequence",
        "metadata",
        "policy",
        "revision",
        "parent",
        "hash",
        "verb",
    ],
)
def test_recovery_replay_refuses_changed_metadata(tmp_path: Path, case: str) -> None:
    store, state, request, response, context = _setup(tmp_path)
    _commit(store, state, request, response, context)
    event = store.latest_at_or_before(9)
    assert event is not None
    payload = deepcopy(event.payload)
    policies = {"enrollment": RECOVERY_POLICY}
    if case == "kind":
        event = event._replace(kind="foreign")
    elif case == "fields":
        payload["extra"] = True
    elif case == "schema":
        payload["schema_version"] = "foreign"
    elif case == "sequence":
        payload["predecessor_sequence"] = True
    elif case == "metadata":
        payload["writer_principal"] = 1
    elif case == "policy":
        policies = {}
    elif case == "revision":
        payload["enrollment_revision"] = "other"
        policies["other"] = RECOVERY_POLICY
    elif case == "parent":
        payload["predecessor_sequence"] = 1
    elif case == "hash":
        payload["predecessor_result_sha256"] = "0" * 64
    elif case == "verb":
        altered = json.loads(payload["request"])
        altered["type"] = "protected_write_admit"
        del altered["body"]["parent_reservation_id"]
        payload["request"] = json.dumps(altered)
    with pytest.raises(ValueError):
        restore_protected_recovery(
            event._replace(payload=payload), store=store, state=state, policies=policies
        )
    assert state.protected_write_recoveries == {}
    store.close()


@pytest.mark.parametrize("case", ["no-events", "two-events", "body", "policy"])
def test_recovery_finalizer_fails_before_signing(tmp_path: Path, case: str) -> None:
    store, state, request, response, context = _setup(tmp_path)
    seqs = () if case == "no-events" else (9, 10) if case == "two-events" else (9,)
    if case == "body":
        response["body"] = None
    with pytest.raises(ValueError):
        finalize_protected_recovery(
            response,
            seqs,
            candidate=state,
            parent=state.protected_write_reservations["reservation"],
            request=json.dumps(request),
            context=context,
            policy=POLICY if case == "policy" else RECOVERY_POLICY,
            sign_response=lambda _frame: pytest.fail("must not sign"),
        )
    store.close()


def test_recovery_payload_rejects_ordinary_admission(tmp_path: Path) -> None:
    store, state, request, _, context = _setup(tmp_path)
    request["type"] = "protected_write_admit"
    del request["body"]["parent_reservation_id"]
    with pytest.raises(ValueError, match="requires recover"):
        protected_recovery_event_payload(
            state.protected_write_reservations["reservation"],
            json.dumps(request),
            context=context,
            policy=RECOVERY_POLICY,
        )
    store.close()
