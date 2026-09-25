# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real admission journal reconstruction
from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from synapse_channel.core.atomic_operations import canonical_request_digest
from synapse_channel.core.journal import (
    UnsupportedProtectedWriteHistoryError,
    record_claim,
    replay,
)
from synapse_channel.core.message_auth import MessageAuthKey, sign_frame
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protected_write_admission import finalize_protected_write_admission
from synapse_channel.core.protected_write_admission_journal import (
    ADMISSION_EVENT_KIND,
    ProtectedAdmissionReplayPolicy,
    protected_admission_event_payload,
    restore_protected_admission,
)
from synapse_channel.core.protected_write_proposal import parse_protected_write_proposal
from synapse_channel.core.protected_write_request import parse_protected_write_request
from synapse_channel.core.state import SynapseState
from test_protected_write_admission import CONTEXT, _admission, _claim
from test_protected_write_proposal import LIMITS
from test_protected_write_result import _result

POLICY = ProtectedAdmissionReplayPolicy(LIMITS, frozenset(), 10)
POLICIES = {"enrollment": POLICY}


def _committed(tmp_path: Path) -> EventStore:
    store = EventStore(tmp_path / "admission-replay.db")
    claim = _claim()
    record_claim(store, claim)
    candidate = SynapseState()
    candidate.claims[claim.task_id] = claim
    request, result = _result("admit")
    raw = json.dumps(request)
    key = MessageAuthKey("authority", b"test-only-journal-key", frozenset({"EXAMPLE/authority"}))
    store.commit_operation(
        operation_key=_admission().operation_key,
        request_digest=canonical_request_digest(request),
        response=result,
        events=(
            (
                ADMISSION_EVENT_KIND,
                protected_admission_event_payload(raw, context=CONTEXT, limits=LIMITS),
            ),
        ),
        intent={"family": "protected-admission"},
        finalize_response=lambda draft, sequences: finalize_protected_write_admission(
            draft,
            sequences,
            candidate=candidate,
            request=raw,
            context=CONTEXT,
            limits=LIMITS,
            reason_codes=frozenset(),
            max_reservations=10,
            sign_response=lambda frame: sign_frame(
                frame,
                key=key,
                nonce="journal-admission",
                sequence=1,
                timestamp=CONTEXT.now,
            ),
        ),
    )
    return store


def test_reopened_replay_restores_custody_before_expiring_author(tmp_path: Path) -> None:
    _committed(tmp_path).close()
    store = EventStore(tmp_path / "admission-replay.db")
    state = replay(store, protected_write_policies=POLICIES, now=CONTEXT.now + 1000).state
    assert state.claims == {}
    admission = state.protected_write_admissions["reservation"]
    assert state.protected_claim_custody == {"reservation": admission.claim_custody}
    assert json.loads(admission.result_bytes)["body"]["admission_sequence"] == 2
    assert not state.claim(
        "EXAMPLE/author",
        "new-task",
        worktree="/example",
        paths=["records/new"],
        now=CONTEXT.now + 1000,
    )[0]
    assert store.delete([1, 2, 3]) == 0
    assert [event.seq for event in store.read_all()] == [1, 2, 3]
    assert (
        "reservation"
        in replay(store, protected_write_policies=POLICIES).state.protected_claim_custody
    )
    store.close()


def test_replay_requires_policy_and_a_complete_unfiltered_prefix(tmp_path: Path) -> None:
    store = _committed(tmp_path)
    with pytest.raises(UnsupportedProtectedWriteHistoryError):
        replay(store)
    with pytest.raises(ValueError, match="policy"):
        replay(store, protected_write_policies={})
    with pytest.raises(ValueError, match="unfiltered"):
        replay(store, protected_write_policies=POLICIES, event_kinds={"claim"})
    assert (
        replay(
            store, protected_write_policies=POLICIES, up_to_seq=1
        ).state.protected_write_admissions
        == {}
    )
    with pytest.raises(ValueError, match="complete matching operation"):
        replay(store, protected_write_policies=POLICIES, up_to_seq=2)
    assert (
        "reservation"
        in replay(
            store, protected_write_policies=POLICIES, up_to_seq=3
        ).state.protected_write_admissions
    )
    store.append("protected_write_future", {})
    with pytest.raises(UnsupportedProtectedWriteHistoryError):
        replay(store, protected_write_policies=POLICIES)
    store.close()


@pytest.mark.parametrize(
    "kind", ["claim", "task_update", "checkpoint", "handoff", "release", "overlap"]
)
def test_replay_refuses_legacy_mutation_of_held_custody(tmp_path: Path, kind: str) -> None:
    store = _committed(tmp_path)
    claim = _claim()
    if kind == "overlap":
        claim.task_id = "different-task"
        kind = "claim"
    store.append(kind, claim.as_dict())
    with pytest.raises(UnsupportedProtectedWriteHistoryError, match="protected custody"):
        replay(store, protected_write_policies=POLICIES)
    store.close()


def test_replay_preserves_unrelated_coordination(tmp_path: Path) -> None:
    store = _committed(tmp_path)
    claim = _claim()
    claim.task_id, claim.paths = "unrelated", ("docs",)
    record_claim(store, claim)
    assert (
        "unrelated"
        in replay(store, protected_write_policies=POLICIES, now=CONTEXT.now).state.claims
    )
    store.close()


@pytest.mark.parametrize("case", ["schema", "extra", "type", "enrollment", "clock"])
def test_replay_refuses_malformed_internal_metadata(tmp_path: Path, case: str) -> None:
    store = _committed(tmp_path)
    event = store.read_all()[1]
    payload = dict(event.payload)
    if case == "schema":
        payload["schema_version"] = "future"
    elif case == "extra":
        payload["unexpected"] = True
    elif case == "type":
        payload["writer_principal"] = 1
    elif case == "enrollment":
        payload["enrollment_revision"] = "other"
    else:
        payload["admitted_at"] = "not-a-clock"
    store.close()
    with sqlite3.connect(tmp_path / "admission-replay.db") as connection:
        connection.execute("UPDATE events SET payload = ? WHERE seq = 2", (json.dumps(payload),))
    reopened = EventStore(tmp_path / "admission-replay.db")
    with pytest.raises(ValueError):
        replay(reopened, protected_write_policies={**POLICIES, "other": POLICY})
    reopened.close()


@pytest.mark.parametrize(
    "case",
    [
        "missing",
        "digest",
        "first",
        "commit",
        "hash",
        "marker-missing",
        "marker-changed",
        "claim-missing",
    ],
)
def test_replay_requires_exact_operation_and_commit_evidence(tmp_path: Path, case: str) -> None:
    store = _committed(tmp_path)
    store.close()
    commands = {
        "missing": "DELETE FROM operations",
        "digest": "UPDATE operations SET request_digest = 'changed'",
        "first": "UPDATE operations SET first_event_seq = 1",
        "commit": "UPDATE operations SET commit_seq = 99",
        "hash": "UPDATE operations SET response_sha256 = 'changed'",
        "marker-missing": "DELETE FROM events WHERE seq = 3",
        "marker-changed": "UPDATE events SET payload = '{}' WHERE seq = 3",
        "claim-missing": "DELETE FROM events WHERE seq = 1",
    }
    with sqlite3.connect(tmp_path / "admission-replay.db") as connection:
        connection.execute(commands[case])
    reopened = EventStore(tmp_path / "admission-replay.db")
    with pytest.raises(ValueError):
        replay(reopened, protected_write_policies=POLICIES)
    reopened.close()


def test_codec_refuses_wrong_kind_and_non_admission_request(tmp_path: Path) -> None:
    request, _ = _result("status")
    with pytest.raises(ValueError, match="ordinary admit"):
        protected_admission_event_payload(json.dumps(request), context=CONTEXT, limits=LIMITS)
    store = _committed(tmp_path)
    event = store.read_all()[1]._replace(kind="other")
    with pytest.raises(ValueError, match="event fields"):
        restore_protected_admission(event, store=store, state=SynapseState(), policies=POLICIES)
    store.close()


@pytest.mark.parametrize("as_bytes", [False, True])
def test_event_keeps_original_unicode_wire_budget(as_bytes: bool) -> None:
    limits = replace(
        LIMITS, operation_limits=replace(LIMITS.operation_limits, max_identifier_chars=1024)
    )
    request, _ = _result("admit")
    proposal = cast(dict[str, object], request["body"])["proposal"]
    proposal = json.loads(json.dumps(proposal).replace("records/note.md", "records/" + "é" * 400))
    parsed = parse_protected_write_proposal(json.dumps(proposal), limits=limits)
    request["body"] = {"proposal": proposal}
    request["proposal_sha256"] = parsed.proposal_sha256
    text = json.dumps(request, ensure_ascii=False)
    raw = text.encode("utf-8") if as_bytes else text
    exact = replace(
        limits, json_limits=replace(limits.json_limits, max_wire_bytes=len(text.encode("utf-8")))
    )
    payload = protected_admission_event_payload(raw, context=CONTEXT, limits=exact)
    restored = json.loads(json.dumps(payload))["request"]
    assert restored == text
    assert parse_protected_write_request(restored, limits=exact).proposal == parsed
