# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — protected verb registration contract
"""Refuse a dispatcher that cannot route the complete protected protocol."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.message_auth import (
    MessageReplayCache,
    VerificationResult,
    sign_frame,
    verify_frame,
)
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protected_write_dispatch import ProtectedWriteDispatch
from synapse_channel.core.protected_write_handler_binding import assemble_protected_write_dispatch
from synapse_channel.core.protected_write_preparation import ProtectedPreparationPolicy
from synapse_channel.core.protected_write_session_auth import authenticate_protected_request
from test_protected_write_admission_journal import POLICY
from test_protected_write_proposal import LIMITS
from test_protected_write_request import _request
from test_protected_write_session_auth import KEY, NOW, enrollment
from test_protected_write_transport import AUTHORITY_KEY


def test_missing_protected_verb_map_refuses_before_dispatch() -> None:
    """An incomplete verb map refuses construction before invoking any handler."""
    with pytest.raises(ValueError, match="requires all seven verbs"):
        ProtectedWriteDispatch({})


@pytest.mark.asyncio
async def test_registered_dispatch_routes_real_authenticated_status_and_rechecks_registry(
    tmp_path: Path,
) -> None:
    """Concrete seven-verb assembly dispatches a signed read and refuses the revoked session."""
    with EventStore(tmp_path / "authority.db") as store:
        policy = replace(POLICY, verify_recovery=lambda _parent, _child: False)
        hub = SynapseHub(
            journal=store,
            protected_write_policies={"enrollment": policy},
            anti_rollback_checkpoint=False,
        )
        registry = {"session": enrollment()}
        dispatch = assemble_protected_write_dispatch(
            hub=hub,
            effects=ProtectedPreparationPolicy("enrollment", LIMITS, {}, {}, {}, {}, {}, 1024),
            replay_policy=policy,
            current_enrollments=lambda: registry,
            writer=("EXAMPLE/writer", "incarnation"),
            operator_principals=frozenset({"EXAMPLE/operator"}),
            recovery_principals=frozenset({"EXAMPLE/recovery"}),
            clock=lambda: NOW,
            sign_response=lambda frame: sign_frame(
                frame, key=AUTHORITY_KEY, nonce="dispatch-reply", sequence=1, timestamp=NOW
            ),
        )
        authenticated = authenticate_protected_request(
            json.dumps(
                sign_frame(
                    _request("status"), key=KEY, nonce="dispatch-query", sequence=1, timestamp=NOW
                )
            ),
            limits=LIMITS,
            enrollments=registry,
            authenticated_principal="EXAMPLE/author",
            replay_cache=MessageReplayCache(window_seconds=10, max_entries=32),
            now=NOW,
        )
        reply = json.loads(await dispatch(authenticated))
        assert (
            verify_frame(
                reply,
                keys={AUTHORITY_KEY.key_id: AUTHORITY_KEY},
                replay_cache=MessageReplayCache(window_seconds=10, max_entries=32),
                now=NOW,
                required_sender="EXAMPLE/authority",
            )
            == VerificationResult.OK
        )
        assert reply["body"]["disposition"] == "unknown"
        assert reply["body"]["request_type"] == "protected_write_status"
        registry.clear()
        with pytest.raises(ValueError, match="session unavailable"):
            await dispatch(authenticated)
        assert store.read_all() == [] and store.read_operations() == ()
