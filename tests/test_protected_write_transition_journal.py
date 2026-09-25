# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — durable reservation transition regressions
from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any, NoReturn

import pytest

from synapse_channel.core.atomic_operations import canonical_request_digest
from synapse_channel.core.journal import replay
from synapse_channel.core.message_auth import MessageAuthKey, sign_frame
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protected_write_request import protected_write_operation_key
from synapse_channel.core.protected_write_transition_journal import (
    TRANSITION_EVENT_KIND,
    finalize_protected_transition,
    protected_transition_event_payload,
    restore_protected_transition,
)
from test_protected_write_admission import CONTEXT
from test_protected_write_admission_journal import POLICY, _committed
from test_protected_write_lifecycle import _frames

# Controlled local verifier for state/storage tests only, not physical attestation.
# Unit-only positive quiescence callback; activation needs descendant and descriptor fencing plus
# stable file closure.
VERIFIED_POLICY = replace(POLICY, verify_quiescence=lambda _admission, _request, _result: True)


def _transition(
    store: Any,
    state: Any,
    verb: str,
    *,
    policy: Any = POLICY,
    outcome: str | None = None,
    fail: bool = False,
    **changes: object,
) -> None:
    previous = state.protected_write_reservations["reservation"]
    sequence = store.read_all()[-1].seq + 1
    request, response, context = _frames(previous, verb, sequence, **changes)
    if outcome is not None:
        response["body"]["outcome"] = outcome
        if verb == "settle":
            request["body"]["outcome"] = outcome
    response["body"]["request_digest"] = canonical_request_digest(request)
    raw = json.dumps(request)
    candidate = deepcopy(state)
    context = replace(context, event_sequence=0)  # Actual event sequence must win.
    key = protected_write_operation_key(
        raw,
        limits=policy.limits,
        authenticated_principal=context.principal,
        authority_id="authority",
        authority_continuity="continuity",
    )
    signer = MessageAuthKey("authority", b"transition-test-only", frozenset({"EXAMPLE/authority"}))

    def stage(name: str) -> None:
        if name == "before_commit":
            assert state.protected_write_reservations["reservation"] == previous
            if fail:
                raise OSError("transition commit failed")

    store.commit_operation(
        operation_key=key,
        request_digest=canonical_request_digest(request),
        response=response,
        events=(
            (
                TRANSITION_EVENT_KIND,
                protected_transition_event_payload(
                    previous,
                    raw.encode(),
                    context=context,
                    policy=policy,
                ),
            ),
        ),
        intent={"family": "protected-transition"},
        finalize_response=lambda draft, sequences: finalize_protected_transition(
            draft,
            sequences,
            candidate=candidate,
            previous=previous,
            request=raw,
            context=context,
            policy=policy,
            sign_response=lambda frame: sign_frame(
                frame,
                key=signer,
                nonce=f"transition-{sequence}",
                sequence=sequence,
                timestamp=CONTEXT.now,
            ),
        ),
        stage_hook=stage,
    )
    state.publish_from(candidate)


def _state(store: Any, policy: Any = POLICY) -> Any:
    return replay(store, protected_write_policies={"enrollment": policy}, now=CONTEXT.now).state


def _begin(store: Any, state: Any) -> None:
    _transition(store, state, "begin", operation_phase="executing", begin_sequence=999)


def _pending(store: Any, state: Any) -> None:
    _transition(store, state, "revoke", disposition="pending", revocation_phase="requested")


def _settle(store: Any, state: Any, outcome: str = "committed") -> None:
    old = json.loads(state.protected_write_reservations["reservation"].result_bytes)["body"]
    known = outcome in ("committed", "no_write")
    _transition(
        store,
        state,
        "settle",
        policy=VERIFIED_POLICY,
        outcome=outcome,
        disposition="accepted",
        operation_phase="settled" if known else "recovery_required",
        revocation_phase="effective"
        if known and old["revocation_phase"] == "requested"
        else old["revocation_phase"],
        evidence_reference=json.loads(
            state.protected_write_admissions["reservation"].proposal_bytes
        )["content_reference"],
    )


def test_durable_pending_revocation_settlement_and_evidence_requirement(tmp_path: Path) -> None:
    store = _committed(tmp_path)
    state = _state(store)
    _begin(store, state)
    _pending(store, state)
    _pending(store, state)
    _settle(store, state)
    expected = state.protected_write_reservations["reservation"]
    assert expected.transition_sequence == 10
    assert not expected.holds_custody
    with pytest.raises(ValueError, match="quiescence"):
        _state(store)
    assert _state(store, VERIFIED_POLICY).protected_write_reservations["reservation"] == expected
    assert _state(store, VERIFIED_POLICY).protected_claim_custody == {}
    store.close()
    reopened = EventStore(tmp_path / "admission-replay.db")
    assert _state(reopened, VERIFIED_POLICY).protected_write_reservations["reservation"] == expected
    assert _state(reopened, VERIFIED_POLICY).protected_claim_custody == {}
    reopened.close()


@pytest.mark.parametrize("pending", [False, True])
def test_durable_uncertainty_never_releases_custody(tmp_path: Path, pending: bool) -> None:
    store = _committed(tmp_path)
    state = _state(store)
    _begin(store, state)
    if pending:
        _pending(store, state)
    _settle(store, state, "unknown")
    _pending(store, state)
    restored = _state(store)
    assert restored.protected_write_reservations["reservation"].holds_custody
    assert "reservation" in restored.protected_claim_custody
    store.close()


@pytest.mark.parametrize("before_begin", [False, True])
def test_durable_revocation_of_admitted_or_settled_reservation(
    tmp_path: Path, before_begin: bool
) -> None:
    store = _committed(tmp_path)
    state = _state(store)
    if not before_begin:
        _begin(store, state)
        _settle(store, state)
    _transition(
        store,
        state,
        "revoke",
        disposition="effective",
        operation_phase="settled",
        revocation_phase="effective",
        outcome="no_write" if before_begin else None,
        evidence_reference=json.loads(
            state.protected_write_admissions["reservation"].proposal_bytes
        )["content_reference"],
    )
    assert (
        not _state(store, VERIFIED_POLICY).protected_write_reservations["reservation"].holds_custody
    )
    store.close()


def test_failed_transition_commit_publishes_nothing(tmp_path: Path) -> None:
    store = _committed(tmp_path)
    state = _state(store)
    before = deepcopy(state)
    with pytest.raises(OSError, match="commit failed"):
        _transition(
            store, state, "begin", operation_phase="executing", begin_sequence=999, fail=True
        )
    assert store.count() == 3
    assert state.protected_write_reservations == before.protected_write_reservations
    assert state.protected_claim_custody == before.protected_claim_custody
    store.close()


@pytest.mark.parametrize(
    "case",
    ["kind", "fields", "schema", "type", "sequence-type", "policy", "enrollment", "predecessor"],
)
def test_transition_replay_refuses_bad_metadata(tmp_path: Path, case: str) -> None:
    store = _committed(tmp_path)
    before = _state(store)
    current = deepcopy(before)
    _begin(store, current)
    event = store.read_all()[3]
    payload = dict(event.payload)
    policies = {"enrollment": POLICY}
    if case == "kind":
        event = event._replace(kind="other")
    elif case == "fields":
        payload["extra"] = True
    elif case == "schema":
        payload["schema_version"] = "future"
    elif case == "type":
        payload["principal"] = True
    elif case == "sequence-type":
        payload["predecessor_sequence"] = True
    elif case == "policy":
        policies = {}
    elif case == "enrollment":
        payload["enrollment_revision"] = "other"
        policies["other"] = POLICY
    else:
        payload["predecessor_result_sha256"] = "0" * 64
    with pytest.raises(ValueError):
        restore_protected_transition(
            event._replace(payload=payload),
            store=store,
            state=before,
            policies=policies,
        )
    assert before.protected_write_reservations["reservation"].transition_sequence == 2
    store.close()


@pytest.mark.parametrize("case", ["no-events", "multiple-events", "body", "verb"])
def test_transition_finalizer_refuses_bad_shape_before_signing(tmp_path: Path, case: str) -> None:
    store = _committed(tmp_path)
    state = _state(store)
    previous = state.protected_write_reservations["reservation"]
    request, response, context = _frames(previous, "status" if case == "verb" else "begin", 4)
    if case == "body":
        response["body"] = None
    sequences = () if case == "no-events" else (4, 5) if case == "multiple-events" else (4,)

    def unexpected(_frame: dict[str, object]) -> NoReturn:
        raise AssertionError("invalid transition shape must not reach signer")

    with pytest.raises(ValueError):
        finalize_protected_transition(
            response,
            sequences,
            candidate=state,
            previous=previous,
            request=json.dumps(request),
            context=context,
            policy=POLICY,
            sign_response=unexpected,
        )
    if case == "verb":
        with pytest.raises(ValueError, match="reservation transition"):
            protected_transition_event_payload(
                previous, json.dumps(request), context=context, policy=POLICY
            )
    store.close()
