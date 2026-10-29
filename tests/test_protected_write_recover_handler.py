# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — authenticated durable recovery refusal
from __future__ import annotations

import asyncio
import json
import time
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from synapse_channel.core.event_row_recovery import CorruptEventRow
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import record_claim
from synapse_channel.core.message_auth import MessageAuthKey, sign_frame
from synapse_channel.core.message_auth_durable import (
    DurableMessageAuthReplayStore,
    SequenceFloorMode,
)
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protected_write_admission_journal import ProtectedAdmissionReplayPolicy
from synapse_channel.core.protected_write_preparation import ProtectedPreparationPolicy
from synapse_channel.core.protected_write_recover_handler import ProtectedWriteRecoverHandler
from synapse_channel.core.protected_write_session_auth import ProtectedSessionEnrollment
from synapse_channel.core.protected_write_transport import ProtectedWriteTransport
from synapse_channel.core.state_models import TaskClaim
from test_protected_write_admission import CONTEXT
from test_protected_write_admission_journal import POLICY
from test_protected_write_effects import _plan
from test_protected_write_recovery import _sync
from test_protected_write_recovery_journal import _setup

SERVICE_KEY = MessageAuthKey("recovery-service", b"r" * 32, frozenset({"EXAMPLE/recovery"}))
WRITER_KEY = MessageAuthKey("recovery-writer", b"w" * 32, frozenset({"EXAMPLE/writer"}))
AUTHORITY_KEY = MessageAuthKey("recovery-authority", b"a" * 32, frozenset({"EXAMPLE/authority"}))


def _refresh_recovery_claim(store: EventStore, claim: TaskClaim) -> TaskClaim:
    """Record a fresh real-wall lease so hub replay retains the recovery witness."""
    fresh = replace(claim, lease_expires_at=time.time() + 120, version=claim.version + 1)
    record_claim(store, fresh)
    return fresh


def _enrollment(
    request: dict[str, object],
    principal: str,
    key: MessageAuthKey,
    verbs: frozenset[str],
    *,
    incarnation: str | None = None,
) -> ProtectedSessionEnrollment:
    return ProtectedSessionEnrollment(
        session_id=str(request["session_id"]) if incarnation is None else "recovery-writer-session",
        principal=principal,
        target=str(request["target"]),
        authority_id=str(request["authority_id"]),
        authority_continuity=str(request["authority_continuity"]),
        enrollment_revision=str(request["enrollment_revision"]),
        transaction_id=str(request["transaction_id"]),
        proposal_sha256=str(request["proposal_sha256"]),
        verbs=verbs,
        expires_at=CONTEXT.now + 30,
        key=key,
        writer_incarnation=incarnation,
    )


def test_recovery_refuses_without_durable_verifier_or_distinct_service(tmp_path: Path) -> None:
    """Construction cannot silently create a permissive recovery service."""
    effects = ProtectedPreparationPolicy("enrollment", POLICY.limits, {}, {}, {}, {}, {}, 1024)
    with EventStore(tmp_path / "authority.db") as store:
        for hub, policy, services, writer, message in (
            (
                SynapseHub(),
                POLICY,
                frozenset({"EXAMPLE/recovery"}),
                ("EXAMPLE/writer", "new"),
                "durable",
            ),
            (
                SynapseHub(journal=store, anti_rollback_checkpoint=False),
                POLICY,
                frozenset({"EXAMPLE/recovery"}),
                ("EXAMPLE/writer", "new"),
                "evidence verifier",
            ),
            (
                SynapseHub(journal=store, anti_rollback_checkpoint=False),
                replace(
                    POLICY,
                    limits=replace(
                        POLICY.limits,
                        operation_limits=replace(
                            POLICY.limits.operation_limits,
                            max_content_bytes=POLICY.limits.operation_limits.max_content_bytes + 1,
                        ),
                    ),
                    verify_recovery=lambda _parent, _child: False,
                ),
                frozenset({"EXAMPLE/recovery"}),
                ("EXAMPLE/writer", "new"),
                "effect and replay limits differ",
            ),
            (
                SynapseHub(journal=store, anti_rollback_checkpoint=False),
                replace(POLICY, verify_recovery=lambda _parent, _child: False),
                frozenset(),
                ("EXAMPLE/writer", "new"),
                "service principals",
            ),
            (
                SynapseHub(journal=store, anti_rollback_checkpoint=False),
                replace(POLICY, verify_recovery=lambda _parent, _child: False),
                frozenset({"EXAMPLE/recovery"}),
                ("EXAMPLE/recovery", "new"),
                "distinct enrolled writer",
            ),
        ):
            with pytest.raises(ValueError, match=message):
                ProtectedWriteRecoverHandler(
                    hub=hub,
                    policy=policy,
                    effects=effects,
                    current_enrollments=lambda: {},
                    recovery_principals=services,
                    writer=writer,
                    clock=lambda: CONTEXT.now,
                    sign_response=lambda frame: frame,
                )


@pytest.mark.parametrize(
    "failure",
    [
        "evidence",
        "writer",
        "writer_scope",
        "writer_expired",
        "service",
        "parent",
        "effect",
        "revision",
        "verb",
        "journal",
        "corrupt",
    ],
)
async def test_real_socket_recovery_refusal_keeps_parent_custody(
    tmp_path: Path, failure: str
) -> None:
    """A rejected recovery leaves the durable parent and claim domain intact."""
    store, _state, request, response, _context = _setup(tmp_path)
    fresh = _refresh_recovery_claim(store, _state.claims["recovery-claim"])
    request["body"]["proposal"]["claims"][0].update(
        version=fresh.version, lease_expires_at=fresh.lease_expires_at
    )
    request["body"]["proposal"]["auxiliary_operations"] = deepcopy(_plan()["auxiliary_operations"])
    _sync(request, response)
    policy: ProtectedAdmissionReplayPolicy = replace(
        POLICY,
        verify_recovery=lambda _parent, _child: False,
    )
    hub = SynapseHub(
        journal=store,
        protected_write_policies={"enrollment": policy},
        anti_rollback_checkpoint=False,
    )
    service = _enrollment(
        request,
        "EXAMPLE/recovery",
        SERVICE_KEY,
        frozenset({"recover", "status"}) if failure == "verb" else frozenset({"recover"}),
    )
    writer = _enrollment(
        request,
        "EXAMPLE/writer",
        WRITER_KEY,
        frozenset({"begin", "settle"}),
        incarnation="recovery-incarnation",
    )
    registry = {service.session_id: service, writer.session_id: writer}
    effects = ProtectedPreparationPolicy(
        "different-enrollment" if failure == "revision" else "enrollment",
        policy.limits,
        {},
        {}
        if failure == "effect"
        else {("memory", "records/note.md"): frozenset({"recovery-claim"})},
        {
            ("memory", "records/note.md"): frozenset({"create", "fsync"}),
            ("memory", "records"): frozenset({"fsync"}),
        },
        {("memory", "records/note.md"): ("memory", "records")},
        {},
        65536,
    )
    handler = ProtectedWriteRecoverHandler(
        hub=hub,
        policy=policy,
        effects=effects,
        current_enrollments=lambda: registry,
        recovery_principals=(
            frozenset({"EXAMPLE/other"})
            if failure == "service"
            else frozenset({"EXAMPLE/recovery"})
        ),
        writer=("EXAMPLE/writer", "recovery-incarnation"),
        clock=lambda: CONTEXT.now,
        sign_response=lambda frame: sign_frame(
            frame, key=AUTHORITY_KEY, nonce="recovery-reply", sequence=1, timestamp=CONTEXT.now
        ),
    )
    if failure == "writer":
        del registry[writer.session_id]
    if failure == "writer_scope":
        registry[writer.session_id] = replace(writer, transaction_id="foreign-transaction")
    if failure == "writer_expired":
        registry[writer.session_id] = replace(writer, expires_at=CONTEXT.now)
    if failure == "parent":
        request["body"]["parent_reservation_id"] = "missing-parent"
    if failure == "verb":
        request.update(type="protected_write_status", body={"reservation_id": None})
    if failure == "journal":
        hub.journal = None
    if failure == "corrupt":
        hub.journal_corrupt_rows = (CorruptEventRow(99, None, (), "a" * 64),)
    replay_store = DurableMessageAuthReplayStore(
        tmp_path / "nonces.db", max_entries=32, window_seconds=10
    )
    ingress = ProtectedWriteTransport(
        limits=policy.limits,
        current_enrollments=lambda: registry,
        replay_store=replay_store,
        sequence_floor_mode=SequenceFloorMode.STRICT,
        clock=lambda: CONTEXT.now,
        dispatch=handler,
        reason_codes=policy.reason_codes,
    )
    before_events = store.read_all()
    before_operations = store.read_operations()
    parent = hub.state.protected_write_reservations["reservation"]
    try:
        async with serve(
            ingress, "127.0.0.1", 0, max_size=policy.limits.json_limits.max_wire_bytes
        ) as server:
            port = server.sockets[0].getsockname()[1]
            async with connect(f"ws://127.0.0.1:{port}") as client:
                await client.send(
                    json.dumps(
                        sign_frame(
                            request,
                            key=SERVICE_KEY,
                            nonce=f"recovery-{failure}",
                            sequence=1,
                            timestamp=CONTEXT.now,
                        )
                    )
                )
                with pytest.raises(ConnectionClosed) as refused:
                    await asyncio.wait_for(client.recv(), 2)
                assert refused.value.rcvd is not None
                assert refused.value.rcvd.code == 1008
                assert refused.value.rcvd.reason == "protected request refused"
        assert store.read_all() == before_events
        assert store.read_operations() == before_operations
        assert hub.state.protected_write_reservations["reservation"] == parent
        assert hub.state.protected_claim_custody["reservation"] == parent.custody
        assert hub.state.protected_write_recoveries == {}
    finally:
        replay_store.close()
        store.close()
