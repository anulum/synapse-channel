# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — signed recovery conflict against retained admission
"""Exercise cross-verb key reuse without accepting physical recovery proof."""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from synapse_channel.core.atomic_operations import canonical_request_digest
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.message_auth import (
    MessageAuthKey,
    MessageReplayCache,
    VerificationResult,
    sign_frame,
    verify_frame,
)
from synapse_channel.core.message_auth_durable import (
    DurableMessageAuthReplayStore,
    SequenceFloorMode,
)
from synapse_channel.core.protected_write_preparation import ProtectedPreparationPolicy
from synapse_channel.core.protected_write_recover_handler import ProtectedWriteRecoverHandler
from synapse_channel.core.protected_write_request import protected_write_operation_key
from synapse_channel.core.protected_write_session_auth import AuthenticatedProtectedRequest
from synapse_channel.core.protected_write_transport import ProtectedWriteTransport
from test_protected_write_admission import CONTEXT
from test_protected_write_admission_journal import POLICY
from test_protected_write_effects import _plan
from test_protected_write_recover_handler import (
    AUTHORITY_KEY,
    WRITER_KEY,
    _enrollment,
    _refresh_recovery_claim,
)
from test_protected_write_recovery import _sync
from test_protected_write_recovery_journal import _setup


@pytest.mark.parametrize("reuse_admission_key", [True, False])
async def test_signed_recover_refuses_key_conflict_or_missing_custody_proof(
    tmp_path: Path, reuse_admission_key: bool
) -> None:
    """Refuse a reused key before verification, or a fresh recovery without custody proof."""
    store, state, request, _response, _context = _setup(tmp_path)
    fresh = _refresh_recovery_claim(store, state.claims["recovery-claim"])
    request["body"]["proposal"]["claims"][0].update(
        version=fresh.version, lease_expires_at=fresh.lease_expires_at
    )
    request["body"]["proposal"]["auxiliary_operations"] = deepcopy(_plan()["auxiliary_operations"])
    _sync(request, _response)
    parent = state.protected_write_reservations["reservation"]
    origin = json.loads(parent.admission.request_bytes)
    if reuse_admission_key:
        request.update(sender=origin["sender"], request_id=origin["request_id"])
    principal = str(request["sender"])
    service_key = MessageAuthKey("admission-author-recovery", b"c" * 32, frozenset({principal}))
    verifier_calls: list[str] = []

    def refuse_recovery(_parent: object, _child: object) -> bool:
        """Refuse missing physical proof if new recovery reaches admission."""
        verifier_calls.append("recovery")
        return False

    policy = replace(POLICY, verify_recovery=refuse_recovery)
    hub = SynapseHub(
        journal=store,
        protected_write_policies={"enrollment": policy},
        anti_rollback_checkpoint=False,
    )
    service = _enrollment(request, principal, service_key, frozenset({"recover"}))
    writer = _enrollment(
        request,
        "EXAMPLE/writer",
        WRITER_KEY,
        frozenset({"begin", "settle"}),
        incarnation="recovery-incarnation",
    )
    registry = {service.session_id: service, writer.session_id: writer}
    handler = ProtectedWriteRecoverHandler(
        hub=hub,
        policy=policy,
        effects=ProtectedPreparationPolicy(
            "enrollment",
            policy.limits,
            {},
            {("memory", "records/note.md"): frozenset({"recovery-claim"})},
            {
                ("memory", "records/note.md"): frozenset({"create", "fsync"}),
                ("memory", "records"): frozenset({"fsync"}),
            },
            {("memory", "records/note.md"): ("memory", "records")},
            {},
            65536,
        ),
        current_enrollments=lambda: registry,
        recovery_principals=frozenset({service.principal}),
        writer=("EXAMPLE/writer", "recovery-incarnation"),
        clock=lambda: CONTEXT.now,
        sign_response=lambda frame: sign_frame(
            frame, key=AUTHORITY_KEY, nonce="recovery-conflict", sequence=1, timestamp=CONTEXT.now
        ),
    )
    key = protected_write_operation_key(
        json.dumps(request),
        limits=policy.limits,
        authenticated_principal=service.principal,
        authority_id=service.authority_id,
        authority_continuity=service.authority_continuity,
    )
    original = store.get_operation(key)
    if reuse_admission_key:
        assert original is not None
        assert original.request_digest == canonical_request_digest(origin)
        assert original.request_digest != canonical_request_digest(request)
    else:
        assert original is None
    refusal_reasons: list[str] = []

    async def checked_dispatch(authenticated: AuthenticatedProtectedRequest) -> bytes:
        """Observe the real handler refusal without changing the socket contract."""
        try:
            return await handler(authenticated)
        except ValueError as exc:
            refusal_reasons.append(str(exc))
            raise

    before_events, before_operations = store.read_all(), store.read_operations()
    before_custody = dict(hub.state.protected_claim_custody)
    with DurableMessageAuthReplayStore(
        tmp_path / "nonces.db", max_entries=32, window_seconds=10
    ) as replay_store:
        ingress = ProtectedWriteTransport(
            limits=policy.limits,
            current_enrollments=lambda: registry,
            replay_store=replay_store,
            sequence_floor_mode=SequenceFloorMode.STRICT,
            clock=lambda: CONTEXT.now,
            dispatch=checked_dispatch,
            reason_codes=policy.reason_codes,
        )
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
                                key=service_key,
                                nonce="cross-verb-recovery",
                                sequence=1,
                                timestamp=CONTEXT.now,
                            )
                        )
                    )
                    if reuse_admission_key:
                        response = json.loads(await asyncio.wait_for(client.recv(), 2))
                    else:
                        with pytest.raises(ConnectionClosed) as refused:
                            await asyncio.wait_for(client.recv(), 2)
                        assert refused.value.rcvd is not None
                        assert refused.value.rcvd.code == 1008
                        assert refused.value.rcvd.reason == "protected request refused"
            if reuse_admission_key:
                assert (
                    verify_frame(
                        response,
                        keys={AUTHORITY_KEY.key_id: AUTHORITY_KEY},
                        replay_cache=MessageReplayCache(window_seconds=10, max_entries=32),
                        now=CONTEXT.now,
                        required_sender="EXAMPLE/authority",
                    )
                    == VerificationResult.OK
                )
                body = response["body"]
                assert body["disposition"] == "conflict"
                assert body["request_type"] == "protected_write_recover"
                assert body["request_digest"] == canonical_request_digest(request)
                assert all(
                    body[field] is None
                    for field in (
                        "reservation_id",
                        "operation_phase",
                        "revocation_phase",
                        "writer_principal",
                        "writer_incarnation",
                        "admission_sequence",
                        "begin_sequence",
                        "settlement_sequence",
                        "revocation_sequence",
                        "evidence_reference",
                    )
                )
            assert verifier_calls == ([] if reuse_admission_key else ["recovery"]), refusal_reasons
            assert refusal_reasons == (
                []
                if reuse_admission_key
                else ["recovery requires verified quiescence and bounded known effects"]
            )
            assert store.read_all() == before_events
            assert store.read_operations() == before_operations
            assert hub.state.protected_write_reservations["reservation"] == parent
            assert hub.state.protected_claim_custody == before_custody
            assert hub.state.protected_write_recoveries == {}
        finally:
            store.close()
