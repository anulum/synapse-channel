# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — durable protected enrollment reconstruction
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import (
    EventKind,
    UnsupportedProtectedWriteHistoryError,
    record_delivery_receipt_deferred,
    record_delivery_receipt_expired,
    record_delivery_receipt_immediate,
    record_delivery_receipt_requested,
    record_guard_denial,
    record_mailbox_watermark,
    record_multihub_equivocation,
    replay,
    restore_active_multihub_quarantines,
)
from synapse_channel.core.message_auth import MessageAuthKey
from synapse_channel.core.multihub_equivocation import FederationQuarantine
from synapse_channel.core.path_identity import CanonicalPathIdentity, ClaimScopeIdentity
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protected_write_enrollment_journal import (
    ISSUE_EVENT_KIND,
    REVOKE_EVENT_KIND,
    apply_enrollment_event,
    enrollment_issue_payload,
    key_fingerprint,
)
from synapse_channel.core.protected_write_session_auth import ProtectedSessionEnrollment
from synapse_channel.core.state import SynapseState


def _enrollment(
    session: str = "writer-1", incarnation: str = "incarnation-1", secret: bytes = b"a" * 32
) -> ProtectedSessionEnrollment:
    return ProtectedSessionEnrollment(
        session_id=session,
        principal="RIGOR/writer",
        target="SYNAPSE/authority",
        authority_id="authority-1",
        authority_continuity="continuity-1",
        enrollment_revision="revision-1",
        transaction_id="transaction-1",
        proposal_sha256="0" * 64,
        verbs=frozenset({"begin", "settle"}),
        expires_at=2_000_000_000.0,
        key=MessageAuthKey("key-" + session, secret, frozenset({"RIGOR/writer"})),
        writer_incarnation=incarnation,
    )


def _restart(path: Path) -> SynapseState:
    """Replay actual retained enrollment history from a reopened authority store."""
    with EventStore(path) as store:
        return replay(store, protected_write_policies={}).state


def test_issue_revoke_survive_real_store_restart_without_secret(tmp_path: Path) -> None:
    path = tmp_path / "enrollment.db"
    first = _enrollment()
    payload = enrollment_issue_payload(first)
    assert first.key.secret not in repr(payload).encode()
    assert payload["key_fingerprint"] == key_fingerprint(first.key.secret)
    with EventStore(path) as store:
        store.append(ISSUE_EVENT_KIND, payload, durable=True)
    state = _restart(path)
    record = state.protected_enrollments[first.session_id]
    assert record.materialize(first.key, now=1_900_000_000.0) == first
    with pytest.raises(ValueError, match="fingerprint"):
        record.materialize(replace(first.key, secret=b"z" * 32), now=1_900_000_000.0)
    with EventStore(path) as store:
        store.append(
            REVOKE_EVENT_KIND,
            {"schema_version": payload["schema_version"], "session_id": first.session_id},
            durable=True,
        )
    revoked = _restart(path).protected_enrollments[first.session_id]
    with pytest.raises(ValueError, match="unavailable"):
        revoked.materialize(first.key, now=1_900_000_000.0)


@pytest.mark.parametrize("violation", ["same_session", "same_key", "same_secret", "active_writer"])
def test_replay_refuses_reuse_and_second_live_writer(tmp_path: Path, violation: str) -> None:
    path = tmp_path / "enrollment.db"
    first = _enrollment()
    second = _enrollment("writer-2", "incarnation-2", b"b" * 32)
    payload = enrollment_issue_payload(second)
    if violation == "same_session":
        payload["session_id"] = first.session_id
    elif violation == "same_key":
        payload["key_id"] = first.key.key_id
    elif violation == "same_secret":
        payload["key_fingerprint"] = key_fingerprint(first.key.secret)
    with EventStore(path) as store:
        store.append(ISSUE_EVENT_KIND, enrollment_issue_payload(first), durable=True)
        if violation != "active_writer":
            store.append(
                REVOKE_EVENT_KIND,
                {"schema_version": payload["schema_version"], "session_id": first.session_id},
                durable=True,
            )
        store.append(ISSUE_EVENT_KIND, payload, durable=True)
    with pytest.raises(ValueError):
        _restart(path)


def test_old_key_cannot_materialize_new_incarnation_after_restart(tmp_path: Path) -> None:
    path = tmp_path / "enrollment.db"
    first = _enrollment()
    second = _enrollment("writer-2", "incarnation-2", b"b" * 32)
    with EventStore(path) as store:
        store.append(ISSUE_EVENT_KIND, enrollment_issue_payload(first), durable=True)
        store.append(
            REVOKE_EVENT_KIND,
            {
                "schema_version": "synapse-protected-write.enrollment.v1",
                "session_id": first.session_id,
            },
            durable=True,
        )
        store.append(ISSUE_EVENT_KIND, enrollment_issue_payload(second), durable=True)
    state = _restart(path)
    with pytest.raises(ValueError, match="key id"):
        state.protected_enrollments[second.session_id].materialize(first.key, now=1_900_000_000.0)
    assert (
        state.protected_enrollments[second.session_id].materialize(second.key, now=1_900_000_000.0)
        == second
    )


def test_unconfigured_replay_refuses_enrollment_history(tmp_path: Path) -> None:
    with EventStore(tmp_path / "enrollment.db") as store:
        store.append(ISSUE_EVENT_KIND, enrollment_issue_payload(_enrollment()), durable=True)
        with pytest.raises(UnsupportedProtectedWriteHistoryError):
            replay(store)
        with pytest.raises(UnsupportedProtectedWriteHistoryError):
            SynapseHub(journal=store, anti_rollback_checkpoint=False)
        with pytest.raises(ValueError, match="unfiltered history"):
            replay(store, event_kinds={"claim"}, protected_write_policies={})


@pytest.mark.parametrize(
    "change",
    [
        {"schema_version": "unknown"},
        {"verbs": []},
        {"verbs": ["settle", "begin"]},
        {"key_fingerprint": "not-a-digest"},
        {"key_id": ""},
        {"principal": ""},
    ],
)
def test_corrupt_issue_history_refuses_restart(tmp_path: Path, change: dict[str, object]) -> None:
    path = tmp_path / "corrupt.db"
    payload = enrollment_issue_payload(_enrollment())
    payload.update(change)
    with EventStore(path) as store:
        store.append(ISSUE_EVENT_KIND, payload, durable=True)
    with pytest.raises(ValueError):
        _restart(path)


@pytest.mark.parametrize("bad_revoke", ["unknown", "duplicate", "schema", "extra"])
def test_corrupt_revoke_history_refuses_restart(tmp_path: Path, bad_revoke: str) -> None:
    path = tmp_path / "corrupt-revoke.db"
    payload = enrollment_issue_payload(_enrollment())
    revoke = {"schema_version": payload["schema_version"], "session_id": "writer-1"}
    with EventStore(path) as store:
        store.append(ISSUE_EVENT_KIND, payload, durable=True)
        if bad_revoke == "unknown":
            revoke["session_id"] = "unknown"
        elif bad_revoke == "duplicate":
            store.append(REVOKE_EVENT_KIND, revoke, durable=True)
        elif bad_revoke == "schema":
            revoke["schema_version"] = "unknown"
        else:
            revoke["unexpected"] = True
        store.append(REVOKE_EVENT_KIND, revoke, durable=True)
    with pytest.raises(ValueError):
        _restart(path)


@pytest.mark.parametrize("failure", ["expired", "key_id", "revoked_key", "scope", "clock"])
def test_projection_refuses_unavailable_key_or_time(tmp_path: Path, failure: str) -> None:
    path = tmp_path / "projection.db"
    enrollment = _enrollment()
    with EventStore(path) as store:
        store.append(ISSUE_EVENT_KIND, enrollment_issue_payload(enrollment), durable=True)
    record = _restart(path).protected_enrollments[enrollment.session_id]
    key = enrollment.key
    now = 1_900_000_000.0
    if failure == "expired":
        now = enrollment.expires_at
    elif failure == "key_id":
        key = replace(key, key_id="other-key")
    elif failure == "revoked_key":
        key = replace(key, revoked=True)
    elif failure == "scope":
        key = replace(key, senders=frozenset({"foreign"}))
    else:
        now = float("nan")
    with pytest.raises(ValueError):
        record.materialize(key, now=now)


def test_key_fingerprint_rejects_weak_material() -> None:
    with pytest.raises(ValueError, match="256 bits"):
        key_fingerprint(b"short")


def test_enrollment_projector_rejects_unrecognised_stored_event(tmp_path: Path) -> None:
    with EventStore(tmp_path / "unknown.db") as store:
        store.append("protected_write_enrollment_unknown", {}, durable=True)
        event = next(store.iter_events())
        with pytest.raises(ValueError, match="unsupported protected enrollment event"):
            apply_enrollment_event(event, SynapseState())
        with pytest.raises(UnsupportedProtectedWriteHistoryError):
            replay(store, protected_write_policies={})


def test_ordinary_audit_records_do_not_change_replayed_enrollment(tmp_path: Path) -> None:
    """Enrollment coexists with native receipt, guard and mailbox recording APIs."""
    grant = _enrollment()
    path = tmp_path / "mixed.db"
    receipt = {"sender": grant.principal, "target": grant.target, "seq": 1}
    with EventStore(path) as store:
        store.append(ISSUE_EVENT_KIND, enrollment_issue_payload(grant), durable=True)
        record_delivery_receipt_requested(store, receipt)
        record_delivery_receipt_immediate(store, receipt)
        record_delivery_receipt_deferred(store, receipt)
        record_delivery_receipt_expired(store, receipt)
        record_guard_denial(store, {"decision": "deny", "reason_code": "scope_mismatch"})
        record_mailbox_watermark(store, identity=grant.principal, through_seq=1, source="receiver")
    with EventStore(path) as store:
        state = replay(store, protected_write_policies={}).state
        assert (
            state.protected_enrollments[grant.session_id].materialize(
                grant.key, now=grant.expires_at - 1
            )
            == grant
        )
        assert store.count() == 7
        assert [event.payload for event in store.read_all()[1:5]] == [receipt] * 4
        assert store.read_all()[-1].payload["identity"] == grant.principal


@pytest.mark.parametrize("violation", ["path_scope", "ownership_epoch"])
def test_malformed_ordinary_claim_cannot_hide_behind_valid_enrollment(
    tmp_path: Path, violation: str
) -> None:
    """A valid new event never permits startup from an invalid legacy projection."""
    state = SynapseState()
    identity = ClaimScopeIdentity(
        worktree_path=str(tmp_path),
        case_sensitive=True,
        paths=(CanonicalPathIdentity("declared", "declared"),),
    )
    assert state.claim(
        "owner", "legacy", worktree=str(tmp_path), paths=["declared"], path_identity=identity
    )[0]
    payload = state.claims["legacy"].as_persisted_dict()
    if violation == "path_scope":
        payload["paths"] = ["other"]
    else:
        payload["ownership_epoch"] = True
    with EventStore(tmp_path / "mixed-corrupt.db") as store:
        store.append(ISSUE_EVENT_KIND, enrollment_issue_payload(_enrollment()), durable=True)
        store.append(EventKind.CLAIM, payload, durable=True)
        with pytest.raises(ValueError):
            SynapseHub(journal=store, protected_write_policies={}, anti_rollback_checkpoint=False)
        assert store.count() == 2


@pytest.mark.parametrize(
    "change",
    [
        {"accepted_fingerprint": "invalid"},
        {"peer_id": " "},
        {"seq": 0},
        {"detected_at": float("nan")},
        {"observer_id": "\ud800"},
    ],
)
def test_refused_federation_evidence_preserves_the_enrollment_prefix(
    tmp_path: Path, change: dict[str, object]
) -> None:
    """A collocated legacy recorder refuses invalid evidence before any append."""
    quarantine = FederationQuarantine("peer", 1, "a" * 64, "b" * 64, 1.0, "observer")
    fields = json.loads(json.dumps(change))
    with EventStore(tmp_path / "refused-evidence.db") as store:
        grant = _enrollment()
        store.append(ISSUE_EVENT_KIND, enrollment_issue_payload(grant), durable=True)
        with pytest.raises(ValueError):
            record_multihub_equivocation(store, replace(quarantine, **fields))
        assert store.count() == 1
        assert (
            grant.session_id
            in replay(store, protected_write_policies={}).state.protected_enrollments
        )


def test_incomplete_federation_recovery_keeps_quarantine_beside_enrollment(
    tmp_path: Path,
) -> None:
    """Reopening mixed history does not turn malformed recovery into withdrawal."""
    path = tmp_path / "mixed-quarantine.db"
    with EventStore(path) as store:
        store.append(ISSUE_EVENT_KIND, enrollment_issue_payload(_enrollment()), durable=True)
        store.append(EventKind.MULTIHUB_EQUIVOCATION, {"peer_id": ""}, durable=True)
        record_multihub_equivocation(
            store, FederationQuarantine("peer", 1, "a" * 64, "b" * 64, 1.0, "observer")
        )
        store.append(
            EventKind.MULTIHUB_EQUIVOCATION_RECOVERY,
            {"peer_id": "peer", "explicit_recovery": False},
            durable=True,
        )
    with EventStore(path) as store:
        assert restore_active_multihub_quarantines(store)["peer"].seq == 1
        assert len(replay(store, protected_write_policies={}).state.protected_enrollments) == 1
