# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — current-authority admission evidence regressions
from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any, NoReturn, cast

import pytest

from synapse_channel.core.atomic_operations import (
    OperationDraft,
    OperationRecord,
    canonical_request_digest,
)
from synapse_channel.core.message_auth import (
    MessageAuthKey,
    MessageReplayCache,
    VerificationResult,
    sign_frame,
    verify_frame,
)
from synapse_channel.core.persistence import EventStore, OperationCommitResult
from synapse_channel.core.protected_write_admission import (
    ProtectedAdmissionContext,
    ProtectedWriteAdmission,
    bind_protected_write_admission,
    finalize_protected_write_admission,
)
from synapse_channel.core.state import SynapseState
from synapse_channel.core.state_models import TaskClaim
from synapse_channel.core.state_transaction import SerializedStateMutationActor
from test_protected_write_proposal import LIMITS
from test_protected_write_result import _result

CONTEXT = ProtectedAdmissionContext(
    "EXAMPLE/author",
    "authority",
    "continuity",
    "EXAMPLE/writer",
    "incarnation",
    1,
    1788649199.0,
)


def _claim() -> TaskClaim:
    return TaskClaim(
        "task-1",
        "EXAMPLE/author",
        "",
        1788649100.0,
        1788649200.0,
        epoch=1,
        version=2,
        worktree="/example",
        paths=("records",),
    )


def _admission() -> ProtectedWriteAdmission:
    request, result = _result("admit")
    return bind_protected_write_admission(
        json.dumps(request),
        json.dumps(result),
        context=CONTEXT,
        claims={"task-1": _claim()},
        limits=LIMITS,
        reason_codes=frozenset(),
    )


def test_install_publishes_metadata_and_custody_together() -> None:
    state = SynapseState()
    state.claims["task-1"] = _claim()
    candidate = deepcopy(state)
    admission = _admission()
    candidate.install_protected_write_admission(admission, max_reservations=2)
    assert state.protected_write_admissions == {}
    assert state.protected_claim_custody == {}
    state.publish_from(candidate)
    assert state.protected_write_admissions == {"reservation": admission}
    assert state.protected_claim_custody == {"reservation": admission.claim_custody}


@pytest.mark.parametrize("budget", [True, 0, -1, 1.0])
def test_install_refuses_invalid_budget_without_mutation(budget: object) -> None:
    state = SynapseState()
    state.claims["task-1"] = _claim()
    with pytest.raises(ValueError, match="budget"):
        state.install_protected_write_admission(_admission(), max_reservations=budget)  # type: ignore[arg-type]
    assert state.protected_write_admissions == {}
    assert state.protected_claim_custody == {}


@pytest.mark.parametrize(
    "case", ["duplicate", "custody-only", "quota", "empty", "missing", "changed", "overlap"]
)
def test_install_refuses_inconsistent_or_contending_state(case: str) -> None:
    state = SynapseState()
    state.claims["task-1"] = _claim()
    admission = _admission()
    budget = 2
    if case == "duplicate":
        state.install_protected_write_admission(admission, max_reservations=2)
    elif case == "custody-only":
        state.protected_claim_custody["reservation"] = admission.claim_custody
    elif case in ("quota", "overlap"):
        state.protected_claim_custody["other-reservation"] = admission.claim_custody
        budget = 1 if case == "quota" else 2
    elif case == "empty":
        admission = replace(admission, claim_custody=())
    elif case == "missing":
        state.claims.clear()
    else:
        state.claims["task-1"].version += 1
    before = deepcopy(state)
    with pytest.raises(ValueError):
        state.install_protected_write_admission(admission, max_reservations=budget)
    assert state.claims == before.claims
    assert state.protected_write_admissions == before.protected_write_admissions
    assert state.protected_claim_custody == before.protected_claim_custody


@pytest.mark.parametrize("fail_before_commit", [False, True])
async def test_real_journal_finalizer_installs_admission_atomically(
    tmp_path: Path, fail_before_commit: bool
) -> None:
    path = tmp_path / "admission-atomic.db"
    store = EventStore(path)
    store.append("test-prior", {"retained": True})
    state = SynapseState()
    state.claims["task-1"] = _claim()
    actor = SerializedStateMutationActor()
    request, response = _result("admit")
    raw = json.dumps(request)
    operation_key = _admission().operation_key
    digest = canonical_request_digest(request)
    signing_key = MessageAuthKey(
        "test-authority", b"test-only-admission-key", frozenset({"EXAMPLE/authority"})
    )
    finalized: list[ProtectedWriteAdmission] = []

    def prepare(candidate: SynapseState) -> OperationDraft:
        def finalize(draft: dict[str, Any], sequences: tuple[int, ...]) -> dict[str, Any]:
            signed = finalize_protected_write_admission(
                draft,
                sequences,
                candidate=candidate,
                request=raw,
                context=CONTEXT,
                limits=LIMITS,
                reason_codes=frozenset(),
                max_reservations=1,
                sign_response=lambda frame: sign_frame(
                    frame,
                    key=signing_key,
                    nonce="admission-final",
                    sequence=1,
                    timestamp=CONTEXT.now,
                ),
            )
            finalized.append(candidate.protected_write_admissions["reservation"])
            return dict(signed)

        return OperationDraft(
            response=cast(dict[str, Any], response),
            events=(("protected_write_admission", {"test_request": request}),),
            intent={"family": "protected-admission-test"},
            finalize_response=finalize,
        )

    def stage(name: str) -> None:
        if name == "before_commit":
            assert state.protected_write_admissions == {}
            assert state.protected_claim_custody == {}
            assert len(finalized) == 1
            if fail_before_commit:
                raise OSError("injected commit failure")

    def commit(draft: OperationDraft) -> OperationCommitResult:
        return store.commit_operation(
            operation_key=operation_key,
            request_digest=digest,
            response=draft.response,
            events=draft.events,
            intent=draft.intent,
            finalize_response=draft.finalize_response,
            stage_hook=stage,
        )

    def lookup() -> OperationRecord | None:
        stored = store.get_operation(operation_key)
        return (
            None
            if stored is None
            else OperationRecord(operation_key, stored.request_digest, stored.response)
        )

    async def execute() -> Any:
        return await actor.run_atomic(
            state,
            lambda candidate: candidate,
            request_digest=digest,
            lookup=lookup,
            prepare=prepare,
            commit=commit,
            remember=lambda _record: None,
            conflict=lambda _record: {"error_code": "request_conflict"},
            require_committed_response=True,
        )

    if fail_before_commit:
        with pytest.raises(OSError, match="commit failure"):
            await execute()
        assert state.protected_write_admissions == {}
        assert state.protected_claim_custody == {}
        assert store.count() == 1
        assert store.read_operations() == ()
        assert store.pending_operation_outbox_count() == 0
    else:
        result = await execute()
        admission = finalized[0]
        assert result.outcome == "inserted"
        assert state.protected_write_admissions == {"reservation": admission}
        assert state.protected_claim_custody == {"reservation": admission.claim_custody}
        assert result.response["body"]["admission_sequence"] == 2
        assert (
            verify_frame(
                result.response,
                keys={signing_key.key_id: signing_key},
                required_sender="EXAMPLE/authority",
                replay_cache=MessageReplayCache(window_seconds=10.0, max_entries=32),
                now=CONTEXT.now,
            )
            == VerificationResult.OK
        )
        assert (await execute()).outcome == "replayed"
        assert len(finalized) == 1
        assert store.count() == 3
    store.close()
    reopened = EventStore(path)
    stored = reopened.get_operation(operation_key)
    if fail_before_commit:
        assert stored is None
    else:
        assert stored is not None
        assert json.loads(finalized[0].result_bytes) == stored.response
    reopened.close()


@pytest.mark.parametrize("case", ["no-events", "multiple-events", "bad-body"])
def test_admission_finalizer_refuses_invalid_journal_shape_before_signing(case: str) -> None:
    request, result = _result("admit")
    candidate = SynapseState()
    candidate.claims["task-1"] = _claim()
    sequences = () if case == "no-events" else (1, 2) if case == "multiple-events" else (1,)
    if case == "bad-body":
        result["body"] = None

    def unexpected(_frame: dict[str, object]) -> NoReturn:
        raise AssertionError("invalid journal shape must not reach the signer")

    with pytest.raises(ValueError):
        finalize_protected_write_admission(
            result,
            sequences,
            candidate=candidate,
            request=json.dumps(request),
            context=CONTEXT,
            limits=LIMITS,
            reason_codes=frozenset(),
            max_reservations=1,
            sign_response=unexpected,
        )
    assert candidate.protected_write_admissions == {}
    assert candidate.protected_claim_custody == {}


def test_admission_captures_full_bytes_and_immutable_live_witness() -> None:
    request, result = _result("admit")
    claim = _claim()
    admission = bind_protected_write_admission(
        json.dumps(request),
        json.dumps(result),
        context=CONTEXT,
        claims={"task-1": claim},
        limits=LIMITS,
        reason_codes=frozenset(),
    )
    assert admission.reservation_id == "reservation"
    assert json.loads(admission.request_bytes) == request
    assert json.loads(admission.result_bytes) == result
    assert (
        json.loads(admission.proposal_bytes) == cast(dict[str, object], request["body"])["proposal"]
    )
    assert admission.operation_key.startswith("synapse-protected-write.v1/operation:")
    claim.owner = "changed"
    claim.paths = ("changed",)
    assert admission.claim_custody[0].owner == "EXAMPLE/author"
    assert admission.claim_custody[0].paths == ("records",)


@pytest.mark.parametrize(
    "now", [None, True, 1, float("nan"), float("inf"), -1.0, -0.0, float(2**53)]
)
def test_admission_refuses_invalid_clock(now: object) -> None:
    request, result = _result("admit")
    with pytest.raises(ValueError, match="clock"):
        bind_protected_write_admission(
            json.dumps(request),
            json.dumps(result),
            context=replace(CONTEXT, now=now),  # type: ignore[arg-type]
            claims={"task-1": _claim()},
            limits=LIMITS,
            reason_codes=frozenset(),
        )


@pytest.mark.parametrize("sequence", [None, True, 0, -1, 1.0, 1001])
def test_admission_refuses_invalid_journal_sequence(sequence: object) -> None:
    request, result = _result("admit")
    with pytest.raises(ValueError, match="journal sequence"):
        bind_protected_write_admission(
            json.dumps(request),
            json.dumps(result),
            context=replace(CONTEXT, admission_sequence=sequence),  # type: ignore[arg-type]
            claims={"task-1": _claim()},
            limits=LIMITS,
            reason_codes=frozenset(),
        )


@pytest.mark.parametrize("verb", ["recover", "begin", "prepare", "status"])
def test_admission_cannot_substitute_other_protocol_operations(verb: str) -> None:
    request, result = _result(verb)
    context = replace(CONTEXT, author_principal=str(request["sender"]))
    with pytest.raises(ValueError, match="admit proposal"):
        bind_protected_write_admission(
            json.dumps(request),
            json.dumps(result),
            context=context,
            claims={"task-1": _claim()},
            limits=LIMITS,
            reason_codes=frozenset(),
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("disposition", "denied"),
        ("revocation_phase", "requested"),
        ("revocation_sequence", 2),
        ("admission_sequence", 2),
        ("writer_principal", "other"),
        ("writer_incarnation", "other"),
    ],
)
def test_admission_binds_current_writer_and_open_admission(field: str, value: object) -> None:
    request, result = _result("admit")
    cast(dict[str, object], result["body"])[field] = value
    with pytest.raises(ValueError, match="current writer or journal"):
        bind_protected_write_admission(
            json.dumps(request),
            json.dumps(result),
            context=CONTEXT,
            claims={"task-1": _claim()},
            limits=LIMITS,
            reason_codes=frozenset(),
        )


def test_admission_cannot_assign_the_author_as_isolated_writer() -> None:
    request, result = _result("admit")
    cast(dict[str, object], result["body"])["writer_principal"] = CONTEXT.author_principal
    with pytest.raises(ValueError, match="current writer or journal"):
        bind_protected_write_admission(
            json.dumps(request),
            json.dumps(result),
            context=replace(CONTEXT, writer_principal=CONTEXT.author_principal),
            claims={"task-1": _claim()},
            limits=LIMITS,
            reason_codes=frozenset(),
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("task_id", "other"),
        ("owner", "other"),
        ("epoch", True),
        ("epoch", 2),
        ("version", True),
        ("version", 3),
        ("lease_expires_at", 1788649200),
        ("lease_expires_at", 1788649300.0),
    ],
)
def test_admission_refuses_changed_or_coerced_live_witness(field: str, value: object) -> None:
    request, result = _result("admit")
    claim = _claim()
    setattr(claim, field, value)
    with pytest.raises(ValueError, match="claim witness"):
        bind_protected_write_admission(
            json.dumps(request),
            json.dumps(result),
            context=CONTEXT,
            claims={"task-1": claim},
            limits=LIMITS,
            reason_codes=frozenset(),
        )


@pytest.mark.parametrize("case", ["missing", "expired"])
def test_admission_requires_live_present_claims(case: str) -> None:
    request, result = _result("admit")
    with pytest.raises(ValueError, match="claim witness"):
        bind_protected_write_admission(
            json.dumps(request),
            json.dumps(result),
            context=replace(CONTEXT, now=1788649200.0) if case == "expired" else CONTEXT,
            claims={} if case == "missing" else {"task-1": _claim()},
            limits=LIMITS,
            reason_codes=frozenset(),
        )
