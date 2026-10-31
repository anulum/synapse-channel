# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — durable transition response journeys
"""Exercise response projection through authenticated durable authority commits."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.message_auth import MessageReplayCache, sign_frame
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protected_write_session_auth import authenticate_protected_request
from synapse_channel.core.protected_write_transition_handler import ProtectedWriteTransitionHandler
from test_protected_write_admission_journal import POLICIES, POLICY, _committed
from test_protected_write_request import _request
from test_protected_write_transition_handler import (
    AUTHOR_KEY,
    AUTHORITY_KEY,
    NOW,
    WRITER_KEY,
    _enrollment,
)


@pytest.mark.parametrize("first", ["revoke", "begin"])
async def test_authenticated_transitions_preserve_durable_custody(
    tmp_path: Path, first: str
) -> None:
    """Authenticated revoke/begin/settle replies preserve the real durable custody transitions."""
    store = _committed(tmp_path)
    hub = SynapseHub(
        journal=store, protected_write_policies=POLICIES, anti_rollback_checkpoint=False
    )
    registry = {
        "session": _enrollment("EXAMPLE/author", AUTHOR_KEY, frozenset({"revoke"})),
        "writer-session": _enrollment("EXAMPLE/writer", WRITER_KEY, frozenset({"begin", "settle"})),
    }
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

    async def send(verb: str, sequence: int) -> dict[str, object]:
        """Read the actual response body after an authenticated durable transition."""
        request = _request(verb)
        if verb == "settle":
            # An unknown writer outcome must retain custody for recovery; it
            # cannot be converted into a positive quiescence assertion.
            body = request["body"]
            assert isinstance(body, dict)
            body["outcome"] = "unknown"
            results = body["operation_results"]
            assert isinstance(results, list)
            first_result = results[0]
            assert isinstance(first_result, dict)
            first_result["status"] = "not_started"
        writer = verb in {"begin", "settle"}
        principal = "EXAMPLE/writer" if writer else "EXAMPLE/author"
        key = WRITER_KEY if writer else AUTHOR_KEY
        request.update(
            sender=principal,
            session_id="writer-session" if writer else "session",
            request_id=f"{verb}-{sequence}",
            timestamp=NOW,
        )
        signed = sign_frame(
            request, key=key, nonce=f"nonce-{sequence}", sequence=sequence, timestamp=NOW
        )
        authenticated = authenticate_protected_request(
            json.dumps(signed),
            limits=POLICY.limits,
            enrollments=registry,
            authenticated_principal=principal,
            replay_cache=MessageReplayCache(window_seconds=10, max_entries=32),
            now=NOW,
        )
        response: object = json.loads(await handler(authenticated))
        assert isinstance(response, dict)
        response_body = response["body"]
        assert isinstance(response_body, dict)
        return cast(dict[str, object], response_body)

    try:
        first_response = await send(first, 1)
        assert first_response["reservation_id"] == "reservation"
        if first == "revoke":
            assert first_response["disposition"] == "effective"
            assert first_response["revocation_phase"] == "effective"
            with pytest.raises(ValueError, match="cannot begin"):
                await send("begin", 2)
        else:
            assert first_response["operation_phase"] == "executing"
            second = await send("revoke", 2)
            assert second["disposition"] == "pending"
            assert second["revocation_phase"] == "requested"
            third = await send("settle", 3)
            assert third["operation_phase"] == "recovery_required"
            assert third["outcome"] == "unknown"
            assert third["revocation_phase"] == "requested"
        assert ("reservation" in hub.state.protected_claim_custody) == (first == "begin")
        before = len(store.read_all())
        store.close()
        reopened = EventStore(tmp_path / "admission-replay.db")
        restored = SynapseHub(
            journal=reopened,
            protected_write_policies=POLICIES,
            anti_rollback_checkpoint=False,
        )
        assert len(reopened.read_all()) == before
        assert ("reservation" in restored.state.protected_claim_custody) == (first == "begin")
        assert restored.state.protected_write_reservations["reservation"].holds_custody == (
            first == "begin"
        )
        reopened.close()
    finally:
        store.close()
