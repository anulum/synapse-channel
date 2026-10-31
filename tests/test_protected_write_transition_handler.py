# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — authenticated durable transition integration
"""Exercise real signed role gates and actor-ordered durable reservation transitions."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from synapse_channel.core.event_row_recovery import CorruptEventRow
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.message_auth import MessageAuthKey, MessageReplayCache, sign_frame
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protected_write_admission_journal import ProtectedAdmissionReplayPolicy
from synapse_channel.core.protected_write_enrollment_control import ProtectedEnrollmentControl
from synapse_channel.core.protected_write_session_auth import (
    AuthenticatedProtectedRequest,
    ProtectedSessionEnrollment,
    authenticate_protected_request,
)
from synapse_channel.core.protected_write_transition_handler import ProtectedWriteTransitionHandler
from test_protected_write_admission import CONTEXT
from test_protected_write_admission_journal import POLICIES, POLICY, _committed
from test_protected_write_request import _request

NOW = CONTEXT.now
AUTHOR_KEY = MessageAuthKey("author-transition", b"a" * 32, frozenset({"EXAMPLE/author"}))
WRITER_KEY = MessageAuthKey("writer-transition", b"w" * 32, frozenset({"EXAMPLE/writer"}))
OPERATOR_KEY = MessageAuthKey("operator-transition", b"o" * 32, frozenset({"EXAMPLE/operator"}))
FOREIGN_KEY = MessageAuthKey("foreign-transition", b"f" * 32, frozenset({"EXAMPLE/foreign"}))
AUTHORITY_KEY = MessageAuthKey("authority-transition", b"s" * 32, frozenset({"EXAMPLE/authority"}))


@pytest.mark.asyncio
async def test_durable_writer_reincarnation_cannot_settle_old_reservation(tmp_path: Path) -> None:
    """A newly enrolled writer incarnation cannot settle its predecessor durable reservation."""
    store = _committed(tmp_path)
    original = _enrollment("EXAMPLE/writer", WRITER_KEY, frozenset({"begin", "settle"}))
    successor_key = MessageAuthKey("successor-writer", b"n" * 32, frozenset({"EXAMPLE/writer"}))
    successor = replace(
        original,
        session_id="successor-session",
        writer_incarnation="successor-incarnation",
        key=successor_key,
    )
    keys = {original.session_id: original.key, successor.session_id: successor.key}
    hub = SynapseHub(
        journal=store, protected_write_policies=POLICIES, anti_rollback_checkpoint=False
    )
    control = ProtectedEnrollmentControl(hub=hub, sealed_keys=keys, clock=lambda: NOW)
    await control.issue(original)
    handler = ProtectedWriteTransitionHandler(
        hub=hub,
        policy=POLICY,
        enrollment_revision="enrollment",
        current_enrollments=control.current,
        writer_principal="EXAMPLE/writer",
        operator_principals=frozenset(),
        clock=lambda: NOW,
        sign_response=lambda frame: sign_frame(
            frame, key=AUTHORITY_KEY, nonce="authority", sequence=1, timestamp=NOW
        ),
    )
    begun = _authenticated_transition(
        "begin",
        grant=original,
        key=WRITER_KEY,
        nonce="durable-begin",
        registry=control.current(),
    )
    assert json.loads(await handler(begun))["body"]["operation_phase"] == "executing"
    await control.revoke(original.session_id)
    with pytest.raises(ValueError, match="session unavailable"):
        await handler(begun)
    store.close()

    with EventStore(tmp_path / "admission-replay.db") as reopened:
        restored = SynapseHub(
            journal=reopened, protected_write_policies=POLICIES, anti_rollback_checkpoint=False
        )
        restored_control = ProtectedEnrollmentControl(
            hub=restored, sealed_keys=keys, clock=lambda: NOW
        )
        assert restored_control.current() == {}
        await restored_control.issue(successor)
        restored_handler = ProtectedWriteTransitionHandler(
            hub=restored,
            policy=POLICY,
            enrollment_revision="enrollment",
            current_enrollments=restored_control.current,
            writer_principal="EXAMPLE/writer",
            operator_principals=frozenset(),
            clock=lambda: NOW,
            sign_response=lambda frame: sign_frame(
                frame, key=AUTHORITY_KEY, nonce="authority", sequence=1, timestamp=NOW
            ),
        )
        successor_request = _authenticated_transition(
            "settle",
            grant=successor,
            key=successor_key,
            nonce="successor-settle",
            registry=restored_control.current(),
            body_fields={"writer_incarnation": successor.writer_incarnation},
        )
        with pytest.raises(ValueError, match="does not own the admitted incarnation"):
            await restored_handler(successor_request)


def _enrollment(
    principal: str, key: MessageAuthKey, verbs: frozenset[str]
) -> ProtectedSessionEnrollment:
    """Construct an exact author/writer session grant matching the real transition fixture."""
    source = _request("revoke")
    return ProtectedSessionEnrollment(
        session_id="session" if principal == "EXAMPLE/author" else "writer-session",
        principal=principal,
        target="EXAMPLE/authority",
        authority_id="authority",
        authority_continuity="continuity",
        enrollment_revision="enrollment",
        transaction_id="transaction",
        proposal_sha256=str(source["proposal_sha256"]),
        verbs=verbs,
        expires_at=NOW + 30,
        key=key,
        writer_incarnation="incarnation" if verbs & {"begin", "settle"} else None,
    )


def _authenticated_transition(
    verb: str,
    *,
    grant: ProtectedSessionEnrollment,
    key: MessageAuthKey,
    nonce: str,
    registry: dict[str, ProtectedSessionEnrollment],
    body_fields: Mapping[str, object] | None = None,
    request_id: str | None = None,
) -> AuthenticatedProtectedRequest:
    """Sign and authenticate a scoped transition against the supplied current registry."""
    request = _request(verb)
    if body_fields:
        body = request["body"]
        assert isinstance(body, dict)
        body.update(body_fields)
    request.update(
        sender=grant.principal,
        session_id=grant.session_id,
        request_id=request_id or f"transition-{verb}-{nonce}",
        timestamp=NOW,
    )
    signed = sign_frame(request, key=key, nonce=nonce, sequence=1, timestamp=NOW)
    return authenticate_protected_request(
        json.dumps(signed),
        limits=POLICY.limits,
        enrollments=registry,
        authenticated_principal=grant.principal,
        replay_cache=MessageReplayCache(window_seconds=10, max_entries=32),
        now=NOW,
    )


def _handler(
    hub: SynapseHub,
    registry: dict[str, ProtectedSessionEnrollment],
    *,
    revision: str = "enrollment",
    operators: frozenset[str] = frozenset(),
    policy: ProtectedAdmissionReplayPolicy = POLICY,
) -> ProtectedWriteTransitionHandler:
    """Bind the real durable transition handler to this test registry and replay policy."""
    return ProtectedWriteTransitionHandler(
        hub=hub,
        policy=policy,
        enrollment_revision=revision,
        current_enrollments=lambda: registry,
        writer_principal="EXAMPLE/writer",
        operator_principals=operators,
        clock=lambda: NOW,
        sign_response=lambda frame: sign_frame(
            frame, key=AUTHORITY_KEY, nonce="authority", sequence=1, timestamp=NOW
        ),
    )


async def test_repeated_requested_revocation_cannot_append_another_transition(
    tmp_path: Path,
) -> None:
    """A second actual revoke request preserves the pending revocation and its journal prefix."""
    with _committed(tmp_path) as store:
        hub = SynapseHub(
            journal=store, protected_write_policies=POLICIES, anti_rollback_checkpoint=False
        )
        author = _enrollment("EXAMPLE/author", AUTHOR_KEY, frozenset({"revoke"}))
        writer = _enrollment("EXAMPLE/writer", WRITER_KEY, frozenset({"begin"}))
        registry = {author.session_id: author, writer.session_id: writer}
        handler = _handler(hub, registry)
        begun = _authenticated_transition(
            "begin", grant=writer, key=WRITER_KEY, nonce="begin-before-revoke", registry=registry
        )
        assert json.loads(await handler(begun))["body"]["operation_phase"] == "executing"
        request = _authenticated_transition(
            "revoke", grant=author, key=AUTHOR_KEY, nonce="first-pending-revoke", registry=registry
        )
        assert json.loads(await handler(request))["body"]["revocation_phase"] == "requested"
        before_events = store.read_all()
        before_operations = store.read_operations()
        repeated = _authenticated_transition(
            "revoke", grant=author, key=AUTHOR_KEY, nonce="second-pending-revoke", registry=registry
        )
        with pytest.raises(ValueError, match="revocation is already requested"):
            await handler(repeated)
        assert store.read_all() == before_events
        assert store.read_operations() == before_operations
        assert hub.state.protected_write_reservations["reservation"].holds_custody


async def test_revoked_enrollment_cannot_transition(tmp_path: Path) -> None:
    """A grant removed after authentication cannot perform its protected transition."""
    store = _committed(tmp_path)
    hub = SynapseHub(
        journal=store, protected_write_policies=POLICIES, anti_rollback_checkpoint=False
    )
    grant = _enrollment("EXAMPLE/author", AUTHOR_KEY, frozenset({"revoke"}))
    registry = {grant.session_id: grant}
    handler = ProtectedWriteTransitionHandler(
        hub=hub,
        policy=POLICY,
        enrollment_revision="enrollment",
        current_enrollments=lambda: registry,
        writer_principal="EXAMPLE/writer",
        operator_principals=frozenset(),
        clock=lambda: NOW,
        sign_response=lambda frame: sign_frame(
            frame, key=AUTHORITY_KEY, nonce="authority", sequence=1, timestamp=NOW
        ),
    )
    request = _request("revoke")
    request.update(session_id=grant.session_id, timestamp=NOW)
    authenticated = authenticate_protected_request(
        json.dumps(sign_frame(request, key=AUTHOR_KEY, nonce="nonce", sequence=1, timestamp=NOW)),
        limits=POLICY.limits,
        enrollments=registry,
        authenticated_principal=grant.principal,
        replay_cache=MessageReplayCache(window_seconds=10, max_entries=32),
        now=NOW,
    )
    before = len(store.read_all())
    registry[grant.session_id] = replace(grant, revoked=True)
    try:
        with pytest.raises(ValueError, match="unavailable"):
            await handler(authenticated)
        assert len(store.read_all()) == before
    finally:
        store.close()


@pytest.mark.parametrize("verb", ["begin", "settle"])
async def test_author_cannot_use_writer_transition_even_with_enrolled_verb(
    tmp_path: Path, verb: str
) -> None:
    """A signed author request cannot turn a reservation into writer authority."""
    store = _committed(tmp_path)
    hub = SynapseHub(
        journal=store, protected_write_policies=POLICIES, anti_rollback_checkpoint=False
    )
    grant = _enrollment("EXAMPLE/author", AUTHOR_KEY, frozenset({verb}))
    registry = {grant.session_id: grant}
    handler = ProtectedWriteTransitionHandler(
        hub=hub,
        policy=POLICY,
        enrollment_revision="enrollment",
        current_enrollments=lambda: registry,
        writer_principal="EXAMPLE/writer",
        operator_principals=frozenset(),
        clock=lambda: NOW,
        sign_response=lambda frame: sign_frame(
            frame, key=AUTHORITY_KEY, nonce="authority", sequence=1, timestamp=NOW
        ),
    )
    authenticated = _authenticated_transition(
        verb, grant=grant, key=AUTHOR_KEY, nonce=f"author-{verb}", registry=registry
    )
    before = len(store.read_all())
    try:
        with pytest.raises(
            ValueError,
            match="only the enrolled writer may begin or settle",
        ):
            await handler(authenticated)
        assert len(store.read_all()) == before
        assert hub.state.protected_write_reservations["reservation"].holds_custody
    finally:
        store.close()


def test_transition_handler_refuses_unbound_authority_configuration(tmp_path: Path) -> None:
    """Construction cannot silently accept a volatile hub or mutable role set."""
    volatile = SynapseHub()
    with pytest.raises(ValueError, match="durable authority"):
        _handler(volatile, {})
    store = _committed(tmp_path)
    try:
        durable = SynapseHub(
            journal=store, protected_write_policies=POLICIES, anti_rollback_checkpoint=False
        )
        with pytest.raises(ValueError, match="exact enrollment and writer"):
            _handler(durable, {}, revision="")
        # Deliberately malformed dynamic input at the Python constructor boundary.
        mutable_roles: Any = {"EXAMPLE/operator"}
        with pytest.raises(ValueError, match="immutable"):
            _handler(durable, {}, operators=mutable_roles)
        with pytest.raises(ValueError, match="writer and operator principals must be separate"):
            _handler(durable, {}, operators=frozenset({"EXAMPLE/writer"}))
    finally:
        store.close()


@pytest.mark.parametrize(
    ("verb", "revision", "reservation", "message"),
    [
        ("status", "enrollment", "reservation", "requires begin, settle or revoke"),
        ("revoke", "different", "reservation", "enrollment revision mismatch"),
        ("revoke", "enrollment", "missing", "reservation is unavailable"),
    ],
)
async def test_transition_refusal_does_not_append_journal(
    tmp_path: Path, verb: str, revision: str, reservation: str, message: str
) -> None:
    """A signed but inapplicable request leaves durable custody unchanged."""
    store = _committed(tmp_path)
    hub = SynapseHub(
        journal=store, protected_write_policies=POLICIES, anti_rollback_checkpoint=False
    )
    grant = _enrollment("EXAMPLE/author", AUTHOR_KEY, frozenset({verb}))
    registry = {grant.session_id: grant}
    authenticated = _authenticated_transition(
        verb,
        grant=grant,
        key=AUTHOR_KEY,
        nonce=f"refusal-{verb}-{reservation}",
        registry=registry,
        body_fields={"reservation_id": reservation},
    )
    before = len(store.read_all())
    try:
        with pytest.raises(ValueError, match=message):
            await _handler(hub, registry, revision=revision)(authenticated)
        assert len(store.read_all()) == before
        assert hub.state.protected_write_reservations["reservation"].holds_custody
    finally:
        store.close()


async def test_known_corrupt_authority_cannot_transition(tmp_path: Path) -> None:
    """A hub that has recorded a corrupt row never mutates reservation custody."""
    store = _committed(tmp_path)
    hub = SynapseHub(
        journal=store, protected_write_policies=POLICIES, anti_rollback_checkpoint=False
    )
    grant = _enrollment("EXAMPLE/author", AUTHOR_KEY, frozenset({"revoke"}))
    registry = {grant.session_id: grant}
    authenticated = _authenticated_transition(
        "revoke", grant=grant, key=AUTHOR_KEY, nonce="corrupt", registry=registry
    )
    hub.journal_corrupt_rows = (CorruptEventRow(99, None, (), "a" * 64),)
    before = len(store.read_all())
    try:
        with pytest.raises(ValueError, match="journal unavailable or incomplete"):
            await _handler(hub, registry)(authenticated)
        assert len(store.read_all()) == before
        assert hub.state.protected_write_reservations["reservation"].holds_custody
    finally:
        store.close()


@pytest.mark.parametrize("principal", ["EXAMPLE/operator", "EXAMPLE/foreign"])
async def test_only_enrolled_operator_may_revoke_foreign_reservation(
    tmp_path: Path, principal: str
) -> None:
    """A service role must be both signed and operator enrolled at the actor."""
    store = _committed(tmp_path)
    hub = SynapseHub(
        journal=store, protected_write_policies=POLICIES, anti_rollback_checkpoint=False
    )
    key = OPERATOR_KEY if principal == "EXAMPLE/operator" else FOREIGN_KEY
    grant = _enrollment(principal, key, frozenset({"revoke"}))
    registry = {grant.session_id: grant}
    authenticated = _authenticated_transition(
        "revoke", grant=grant, key=key, nonce="role-check", registry=registry
    )
    before = len(store.read_all())
    try:
        handler = _handler(hub, registry, operators=frozenset({"EXAMPLE/operator"}))
        if principal == "EXAMPLE/operator":
            response = json.loads(await handler(authenticated))
            assert response["body"]["disposition"] == "effective"
            assert len(store.read_all()) == before + 2
            assert not hub.state.protected_write_reservations["reservation"].holds_custody
            again = _authenticated_transition(
                "revoke", grant=grant, key=key, nonce="second-role-check", registry=registry
            )
            with pytest.raises(ValueError, match="already terminal"):
                await handler(again)
            assert len(store.read_all()) == before + 2
        else:
            with pytest.raises(ValueError, match="not enrolled for this reservation"):
                await handler(authenticated)
            assert len(store.read_all()) == before
            assert hub.state.protected_write_reservations["reservation"].holds_custody
    finally:
        store.close()


async def test_operator_cannot_begin_even_with_signed_begin_enrollment(tmp_path: Path) -> None:
    """The operator's revocation role never becomes a writer role."""
    store = _committed(tmp_path)
    hub = SynapseHub(
        journal=store, protected_write_policies=POLICIES, anti_rollback_checkpoint=False
    )
    grant = _enrollment("EXAMPLE/operator", OPERATOR_KEY, frozenset({"begin"}))
    registry = {grant.session_id: grant}
    authenticated = _authenticated_transition(
        "begin", grant=grant, key=OPERATOR_KEY, nonce="operator-begin", registry=registry
    )
    before = len(store.read_all())
    try:
        with pytest.raises(ValueError, match="operator may only revoke"):
            await _handler(hub, registry, operators=frozenset({"EXAMPLE/operator"}))(authenticated)
        assert len(store.read_all()) == before
    finally:
        store.close()


async def test_reused_operation_identity_conflicts_without_second_transition(
    tmp_path: Path,
) -> None:
    """A changed signed request cannot replay a previous revocation identity."""
    store = _committed(tmp_path)
    hub = SynapseHub(
        journal=store, protected_write_policies=POLICIES, anti_rollback_checkpoint=False
    )
    grant = _enrollment("EXAMPLE/author", AUTHOR_KEY, frozenset({"revoke"}))
    registry = {grant.session_id: grant}
    handler = _handler(hub, registry)
    first = _authenticated_transition(
        "revoke",
        grant=grant,
        key=AUTHOR_KEY,
        nonce="first",
        registry=registry,
        request_id="same-operation",
    )
    changed = _authenticated_transition(
        "revoke",
        grant=grant,
        key=AUTHOR_KEY,
        nonce="changed",
        registry=registry,
        request_id="same-operation",
        body_fields={"reservation_id": "other"},
    )
    try:
        assert json.loads(await handler(first))["body"]["disposition"] == "effective"
        before = len(store.read_all())
        conflict = json.loads(await handler(changed))
        assert conflict["body"]["disposition"] == "conflict"
        assert len(store.read_all()) == before
    finally:
        store.close()


async def test_writer_incarnation_is_pinned_across_live_sessions_and_restart(
    tmp_path: Path,
) -> None:
    """A fresh writer process cannot adopt its predecessor's custody."""
    store = _committed(tmp_path)
    hub = SynapseHub(
        journal=store, protected_write_policies=POLICIES, anti_rollback_checkpoint=False
    )
    pinned = _enrollment("EXAMPLE/writer", WRITER_KEY, frozenset({"begin", "settle"}))
    other_key = MessageAuthKey("new-writer-key", b"n" * 32, frozenset({"EXAMPLE/writer"}))
    other = replace(
        pinned,
        session_id="new-writer-session",
        writer_incarnation="new-incarnation",
        key=other_key,
    )
    registry = {pinned.session_id: pinned, other.session_id: other}
    handler = _handler(hub, registry)
    stale_begin = _authenticated_transition(
        "begin",
        grant=other,
        key=other_key,
        nonce="new-begin",
        registry=registry,
        body_fields={"writer_incarnation": "new-incarnation"},
    )
    before = len(store.read_all())
    try:
        with pytest.raises(ValueError, match="does not own the admitted incarnation"):
            await handler(stale_begin)
        assert len(store.read_all()) == before
        actual_begin = _authenticated_transition(
            "begin", grant=pinned, key=WRITER_KEY, nonce="pinned-begin", registry=registry
        )
        assert json.loads(await handler(actual_begin))["body"]["operation_phase"] == "executing"
        store.close()

        reopened = EventStore(tmp_path / "admission-replay.db")
        restored = SynapseHub(
            journal=reopened, protected_write_policies=POLICIES, anti_rollback_checkpoint=False
        )
        registry.pop(pinned.session_id)
        new_settle = _authenticated_transition(
            "settle",
            grant=other,
            key=other_key,
            nonce="new-settle",
            registry=registry,
            body_fields={"writer_incarnation": "new-incarnation"},
        )
        after_restart = len(reopened.read_all())
        try:
            with pytest.raises(ValueError, match="does not own the admitted incarnation"):
                await _handler(restored, registry)(new_settle)
            assert len(reopened.read_all()) == after_restart
            assert restored.state.protected_write_reservations["reservation"].holds_custody
        finally:
            reopened.close()
    finally:
        store.close()


@pytest.mark.parametrize("revoke_before_settle", [False, True])
async def test_no_write_settlement_requires_exited_process_and_unchanged_file(
    tmp_path: Path, revoke_before_settle: bool
) -> None:
    """A known no-write outcome uses observed process and file evidence."""
    target = tmp_path / "enrolled-target"
    target.write_bytes(b"original-custody")
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "from pathlib import Path; Path(__import__('sys').argv[1]).read_bytes()",
            str(target),
        ],
        capture_output=True,
        check=False,
        timeout=5,
    )

    def verify_quiescence(_admission: object, _request: bytes, _result: bytes) -> bool:
        """Require the actual child to exit and the target bytes to stay unchanged."""
        return completed.returncode == 0 and target.read_bytes() == b"original-custody"

    policy = replace(POLICY, verify_quiescence=verify_quiescence)
    store = _committed(tmp_path)
    hub = SynapseHub(
        journal=store,
        protected_write_policies={"enrollment": policy},
        anti_rollback_checkpoint=False,
    )
    author = _enrollment("EXAMPLE/author", AUTHOR_KEY, frozenset({"revoke"}))
    writer = _enrollment("EXAMPLE/writer", WRITER_KEY, frozenset({"begin", "settle"}))
    registry = {author.session_id: author, writer.session_id: writer}
    handler = _handler(hub, registry, policy=policy)
    try:
        begin = _authenticated_transition(
            "begin", grant=writer, key=WRITER_KEY, nonce="begin-no-write", registry=registry
        )
        assert json.loads(await handler(begin))["body"]["operation_phase"] == "executing"
        if revoke_before_settle:
            pending = _authenticated_transition(
                "revoke", grant=author, key=AUTHOR_KEY, nonce="pending-no-write", registry=registry
            )
            assert json.loads(await handler(pending))["body"]["disposition"] == "pending"
        settle_request = _request("settle")
        settle_body = settle_request["body"]
        assert isinstance(settle_body, dict)
        settle_body["outcome"] = "no_write"
        settle_results = settle_body["operation_results"]
        assert isinstance(settle_results, list)
        settle_first = settle_results[0]
        assert isinstance(settle_first, dict)
        settle_first["status"] = "not_started"
        settle_request.update(
            sender=writer.principal,
            session_id=writer.session_id,
            request_id="settle-no-write",
            timestamp=NOW,
        )
        signed = sign_frame(
            settle_request, key=WRITER_KEY, nonce="settle-no-write", sequence=2, timestamp=NOW
        )
        settle = authenticate_protected_request(
            json.dumps(signed),
            limits=POLICY.limits,
            enrollments=registry,
            authenticated_principal=writer.principal,
            replay_cache=MessageReplayCache(window_seconds=10, max_entries=32),
            now=NOW,
        )
        settled = json.loads(await handler(settle))
        assert settled["body"]["operation_phase"] == "settled"
        assert settled["body"]["outcome"] == "no_write"
        if revoke_before_settle:
            assert settled["body"]["revocation_phase"] == "effective"
        else:
            revoke = _authenticated_transition(
                "revoke", grant=author, key=AUTHOR_KEY, nonce="after-no-write", registry=registry
            )
            effective = json.loads(await handler(revoke))
            assert effective["body"]["revocation_phase"] == "effective"
        assert target.read_bytes() == b"original-custody"
        assert not hub.state.protected_write_reservations["reservation"].holds_custody
    finally:
        store.close()
