# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — current protected begin actor tests
"""Exercise a real durable begin, session issue and later revocation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.message_auth import MessageAuthKey, MessageReplayCache, sign_frame
from synapse_channel.core.protected_write_current import ProtectedWriteCurrent
from synapse_channel.core.protected_write_enrollment_control import ProtectedEnrollmentControl
from synapse_channel.core.protected_write_session_auth import (
    ProtectedSessionEnrollment,
    authenticate_protected_request,
)
from test_protected_write_admission import CONTEXT
from test_protected_write_admission_journal import POLICIES
from test_protected_write_preparation import _begun


@pytest.mark.asyncio
async def test_current_begin_follows_durable_enrollment_revocation(tmp_path: Path) -> None:
    """A real durable revocation invalidates the same previously authenticated writer begin."""
    store, prepared, _state = _begun(tmp_path)
    try:
        hub = SynapseHub(
            journal=store, protected_write_policies=POLICIES, anti_rollback_checkpoint=False
        )
        begun = hub.state.protected_write_reservations[prepared.admission.reservation_id]
        request = json.loads(begun.request_bytes)
        key = MessageAuthKey("writer-current", b"w" * 32, frozenset({"EXAMPLE/writer"}))
        enrollment = ProtectedSessionEnrollment(
            session_id=request["session_id"],
            principal=request["sender"],
            target=request["target"],
            authority_id=request["authority_id"],
            authority_continuity=request["authority_continuity"],
            enrollment_revision=request["enrollment_revision"],
            transaction_id=request["transaction_id"],
            proposal_sha256=request["proposal_sha256"],
            verbs=frozenset({"begin", "status"}),
            expires_at=CONTEXT.now + 20,
            key=key,
            writer_incarnation="incarnation",
        )
        control = ProtectedEnrollmentControl(
            hub=hub, sealed_keys={enrollment.session_id: key}, clock=lambda: CONTEXT.now + 1
        )
        await control.issue(enrollment)
        signed = sign_frame(
            request, key=key, nonce="current-begin", sequence=1, timestamp=CONTEXT.now + 1
        )
        authenticated = authenticate_protected_request(
            json.dumps(signed),
            limits=prepared.policy.limits,
            enrollments=control.current(),
            authenticated_principal=enrollment.principal,
            replay_cache=MessageReplayCache(window_seconds=10, max_entries=32),
            now=CONTEXT.now + 1,
        )
        current = ProtectedWriteCurrent(
            hub=hub,
            current_enrollments=control.current,
            clock=lambda: CONTEXT.now + 1,
            authorize_current=lambda candidate, reservation: (
                candidate.policy.enrollment_revision
                == json.loads(reservation.admission.request_bytes)["enrollment_revision"]
            ),
        )
        assert await current(prepared, authenticated) == begun
        hub.journal = None
        with pytest.raises(ValueError, match="journal unavailable"):
            await current(prepared, authenticated)
        hub.journal = store
        for index, (changed, reason) in enumerate(
            (
                (
                    {**request, "type": "protected_write_status", "body": {"reservation_id": None}},
                    "authenticated begin",
                ),
                (
                    {
                        **request,
                        "body": {"reservation_id": "other", "writer_incarnation": "incarnation"},
                    },
                    "differs from prepared",
                ),
            )
        ):
            variant = authenticate_protected_request(
                json.dumps(
                    sign_frame(
                        changed,
                        key=key,
                        nonce=f"variant-{index}",
                        sequence=2,
                        timestamp=CONTEXT.now + 1,
                    )
                ),
                limits=prepared.policy.limits,
                enrollments=control.current(),
                authenticated_principal=enrollment.principal,
                replay_cache=MessageReplayCache(window_seconds=10, max_entries=32),
                now=CONTEXT.now + 1,
            )
            with pytest.raises(ValueError, match=reason):
                await current(prepared, variant)
        await control.revoke(enrollment.session_id)
        with pytest.raises(ValueError, match="session unavailable"):
            await current(prepared, authenticated)
        assert hub.state.protected_write_reservations[prepared.admission.reservation_id] == begun
    finally:
        store.close()


def test_current_begin_requires_durable_authority() -> None:
    """A volatile hub cannot construct the current-begin authority check."""
    with pytest.raises(ValueError, match="complete durable journal"):
        ProtectedWriteCurrent(
            hub=SynapseHub(),
            current_enrollments=lambda: {},
            clock=lambda: CONTEXT.now,
            authorize_current=lambda _prepared, _reservation: False,
        )
