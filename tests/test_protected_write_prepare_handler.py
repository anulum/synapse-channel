# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — ordered protected proposal preparation
"""Check current proposal policy and claim witnesses through authenticated preparation."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import replace
from pathlib import Path

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from synapse_channel.core.event_row_recovery import CorruptEventRow
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import record_claim
from synapse_channel.core.message_auth import (
    MessageReplayCache,
    VerificationResult,
    sign_frame,
    verify_frame,
)
from synapse_channel.core.message_auth_durable import (
    DurableMessageAuthReplayStore,
    SequenceFloorMode,
)
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protected_write_preparation import ProtectedPreparationPolicy
from synapse_channel.core.protected_write_prepare_handler import ProtectedWritePrepareHandler
from synapse_channel.core.protected_write_proposal import parse_protected_write_proposal
from synapse_channel.core.protected_write_session_auth import authenticate_protected_request
from synapse_channel.core.protected_write_transport import ProtectedWriteTransport
from test_protected_write_admission import _claim
from test_protected_write_effects import _plan
from test_protected_write_proposal import LIMITS
from test_protected_write_request import _request
from test_protected_write_session_auth import KEY, enrollment
from test_protected_write_transport import AUTHORITY_KEY


def test_prepare_requires_durable_authority() -> None:
    """A volatile hub cannot issue a policy decision that appears durable."""
    with pytest.raises(ValueError, match="durable authority"):
        ProtectedWritePrepareHandler(
            hub=SynapseHub(),
            policy=ProtectedPreparationPolicy("enrollment", LIMITS, {}, {}, {}, {}, {}, 65536),
            current_enrollments=lambda: {},
            clock=time.time,
            sign_response=lambda frame: frame,
            reason_codes=frozenset(),
        )


@pytest.mark.parametrize(
    "failure",
    [
        None,
        "claim",
        "revision",
        "verb",
        "journal",
        "corrupt",
        "effect",
        "witness-owner",
        "witness-epoch",
        "witness-version",
    ],
)
async def test_prepare_over_real_socket_reads_claim_without_reserving(
    tmp_path: Path, failure: str | None
) -> None:
    """Preparation is an authenticated actor read and creates no custody."""
    now = time.time()
    store = EventStore(tmp_path / "authority.db")
    claim = _claim()
    claim.lease_expires_at = now - 1 if failure == "claim" else now + 60
    claim.worktree = str(tmp_path)
    record_claim(store, claim)
    hub = SynapseHub(journal=store, anti_rollback_checkpoint=False)
    proposal = _plan()
    proposal["claims"][0]["lease_expires_at"] = claim.lease_expires_at
    digest = parse_protected_write_proposal(json.dumps(proposal), limits=LIMITS).proposal_sha256
    registry = {"session": replace(enrollment(), proposal_sha256=digest, expires_at=now + 60)}
    root_stat = tmp_path.stat()
    policy = ProtectedPreparationPolicy(
        "different-enrollment" if failure == "revision" else "enrollment",
        LIMITS,
        {"memory": (root_stat.st_dev, root_stat.st_ino)},
        {} if failure == "effect" else {("memory", "records/note.md"): frozenset({"task-1"})},
        {
            ("memory", "records/note.md"): frozenset({"create", "fsync"}),
            ("memory", "records"): frozenset({"fsync"}),
        },
        {("memory", "records/note.md"): ("memory", "records")},
        {},
        65536,
    )
    handler = ProtectedWritePrepareHandler(
        hub=hub,
        policy=policy,
        current_enrollments=lambda: registry,
        clock=lambda: now,
        sign_response=lambda frame: sign_frame(
            frame, key=AUTHORITY_KEY, nonce="prepare-reply", sequence=1, timestamp=now
        ),
        reason_codes=frozenset(),
    )
    replay_store = DurableMessageAuthReplayStore(
        tmp_path / "nonces.db", max_entries=32, window_seconds=10
    )
    ingress = ProtectedWriteTransport(
        limits=LIMITS,
        current_enrollments=lambda: registry,
        replay_store=replay_store,
        sequence_floor_mode=SequenceFloorMode.STRICT,
        clock=lambda: now,
        dispatch=handler,
        reason_codes=frozenset(),
    )
    request = _request("prepare")
    request.update(proposal_sha256=digest, body={"proposal": proposal}, timestamp=now)
    if failure == "verb":
        request.update(type="protected_write_status", body={"reservation_id": None})
    if failure == "journal":
        hub.journal = None
    if failure == "corrupt":
        hub.journal_corrupt_rows = (CorruptEventRow(99, None, (), "a" * 64),)
    if failure == "witness-owner":
        hub.state.claims["task-1"].owner = "EXAMPLE/other-author"
    elif failure == "witness-epoch":
        hub.state.claims["task-1"].epoch += 1
    elif failure == "witness-version":
        hub.state.claims["task-1"].version += 1
    before = store.read_all()
    try:
        if failure:
            authenticated = authenticate_protected_request(
                json.dumps(
                    sign_frame(request, key=KEY, nonce="direct-prepare", sequence=1, timestamp=now)
                ),
                limits=LIMITS,
                enrollments=registry,
                authenticated_principal="EXAMPLE/author",
                replay_cache=MessageReplayCache(window_seconds=10, max_entries=32),
                now=now,
            )
            messages = {
                "claim": "preparation claim witness is missing, stale or not author-owned",
                "revision": "preparation effect enrollment revision mismatch",
                "verb": "preparation requires a protected proposal",
                "journal": "preparation authority journal unavailable or incomplete",
                "corrupt": "preparation authority journal unavailable or incomplete",
                "effect": "primary effect lacks exact enrolled claim permission",
                "witness-owner": "preparation claim witness is missing, stale or not author-owned",
                "witness-epoch": "preparation claim witness is missing, stale or not author-owned",
                "witness-version": (
                    "preparation claim witness is missing, stale or not author-owned"
                ),
            }
            with pytest.raises(ValueError, match=messages[failure]):
                await handler(authenticated)
        async with serve(
            ingress, "127.0.0.1", 0, max_size=LIMITS.json_limits.max_wire_bytes
        ) as server:
            port = server.sockets[0].getsockname()[1]
            async with connect(f"ws://127.0.0.1:{port}") as client:
                await client.send(
                    json.dumps(
                        sign_frame(request, key=KEY, nonce="prepare", sequence=1, timestamp=now)
                    )
                )
                if failure:
                    with pytest.raises(ConnectionClosed):
                        await asyncio.wait_for(client.recv(), 2)
                else:
                    response = json.loads(await asyncio.wait_for(client.recv(), 2))
                    assert (
                        verify_frame(
                            response,
                            keys={AUTHORITY_KEY.key_id: AUTHORITY_KEY},
                            replay_cache=MessageReplayCache(window_seconds=10, max_entries=32),
                            now=now,
                            required_sender="EXAMPLE/authority",
                        )
                        == VerificationResult.OK
                    )
                    assert response["body"]["disposition"] == "allowed"
                    assert response["body"]["reservation_id"] is None
        assert store.read_all() == before
        assert store.read_operations() == ()
        assert hub.state.protected_write_reservations == {}
    finally:
        replay_store.close()
        store.close()
