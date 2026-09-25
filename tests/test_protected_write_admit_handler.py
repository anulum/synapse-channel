# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real admission, custody and restart integration
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

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
from synapse_channel.core.protected_write_admit_handler import ProtectedWriteAdmitHandler
from synapse_channel.core.protected_write_preparation import ProtectedPreparationPolicy
from synapse_channel.core.protected_write_proposal import parse_protected_write_proposal
from synapse_channel.core.protected_write_status import ProtectedWriteStatus
from synapse_channel.core.protected_write_transport import ProtectedWriteTransport
from test_protected_write_admission import _claim
from test_protected_write_admission_journal import POLICIES
from test_protected_write_effects import _plan
from test_protected_write_proposal import LIMITS
from test_protected_write_request import _request
from test_protected_write_session_auth import KEY, enrollment
from test_protected_write_transport import AUTHORITY_KEY


@pytest.mark.parametrize(
    "failure",
    [
        None,
        "claim",
        "effect",
        "signer",
        "revision",
        "verb",
        "journal",
        "corrupt",
        "budget",
        "volatile",
    ],
)
async def test_real_admission_over_socket_and_status_after_restart(
    tmp_path: Path, failure: str | None
) -> None:
    now = time.time()
    store = EventStore(tmp_path / "authority.db")
    claim = _claim()
    claim.lease_expires_at = now + 60
    claim.worktree = str(tmp_path)
    if failure != "claim":
        record_claim(store, claim)
    hub = SynapseHub(
        journal=store, protected_write_policies=POLICIES, anti_rollback_checkpoint=False
    )
    proposal = _plan()
    proposal["claims"][0]["lease_expires_at"] = claim.lease_expires_at
    digest = parse_protected_write_proposal(json.dumps(proposal), limits=LIMITS).proposal_sha256
    registry = {"session": replace(enrollment(), proposal_sha256=digest, expires_at=now + 60)}
    (tmp_path / "records").mkdir()
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

    def sign(frame: dict[str, object]) -> dict[str, Any]:
        if failure == "signer":
            raise OSError("private signing failure")
        return sign_frame(frame, key=AUTHORITY_KEY, nonce="reply", sequence=1, timestamp=now)

    if failure in {"budget", "volatile"}:
        with pytest.raises(ValueError, match="budget|durable authority"):
            ProtectedWriteAdmitHandler(
                hub=SynapseHub() if failure == "volatile" else hub,
                policy=policy,
                current_enrollments=lambda: registry,
                writer=("EXAMPLE/writer", "incarnation"),
                clock=lambda: now,
                sign_response=sign,
                reason_codes=frozenset(),
                max_reservations=0 if failure == "budget" else 10,
            )
        store.close()
        return
    admit = ProtectedWriteAdmitHandler(
        hub=hub,
        policy=policy,
        current_enrollments=lambda: registry,
        writer=("EXAMPLE/writer", "incarnation"),
        clock=lambda: now,
        sign_response=sign,
        reason_codes=frozenset(),
        max_reservations=10,
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
        dispatch=admit,
        reason_codes=frozenset(),
    )
    request = _request("admit")
    request.update(proposal_sha256=digest, body={"proposal": proposal}, timestamp=now)
    if failure == "verb":
        request.update(type="protected_write_status", body={"reservation_id": None})
    if failure == "journal":
        hub.journal = None
    if failure == "corrupt":
        hub.journal_corrupt_rows = (CorruptEventRow(99, None, (), "a" * 64),)
    before = store.read_all()
    try:
        async with serve(
            ingress, "127.0.0.1", 0, max_size=LIMITS.json_limits.max_wire_bytes
        ) as server:
            port = server.sockets[0].getsockname()[1]
            async with connect(f"ws://127.0.0.1:{port}") as client:
                await client.send(
                    json.dumps(sign_frame(request, key=KEY, nonce="n1", sequence=1, timestamp=now))
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
                    assert response["body"]["admission_sequence"] == 2
                    assert response["body"]["operation_phase"] == "admitted"
                    await client.send(
                        json.dumps(
                            sign_frame(request, key=KEY, nonce="n2", sequence=2, timestamp=now)
                        )
                    )
                    assert json.loads(await asyncio.wait_for(client.recv(), 2)) == response
                    changed = dict(request, session_id="replacement-session")
                    registry["replacement-session"] = replace(
                        registry["session"], session_id="replacement-session"
                    )
            if not failure:
                async with connect(f"ws://127.0.0.1:{port}") as client:
                    await client.send(
                        json.dumps(
                            sign_frame(changed, key=KEY, nonce="changed", sequence=3, timestamp=now)
                        )
                    )
                    conflict = json.loads(await asyncio.wait_for(client.recv(), 2))
                    assert conflict["body"]["disposition"] == "conflict"
                    assert conflict["body"]["reservation_id"] is None
                async with connect(f"ws://127.0.0.1:{port}") as client:
                    overlap = dict(request, request_id="different-request")
                    await client.send(
                        json.dumps(
                            sign_frame(overlap, key=KEY, nonce="overlap", sequence=4, timestamp=now)
                        )
                    )
                    with pytest.raises(ConnectionClosed):
                        await asyncio.wait_for(client.recv(), 2)
        if failure:
            assert store.read_all() == before
            assert store.read_operations() == ()
            assert hub.state.protected_write_reservations == {}
        else:
            assert len(store.read_operations()) == 1
            reservation_id = response["body"]["reservation_id"]
            assert hub.state.protected_claim_custody[reservation_id]
            assert not (tmp_path / "records/note.md").exists()
            store.close()
            store = EventStore(tmp_path / "authority.db")
            restarted = SynapseHub(
                journal=store, protected_write_policies=POLICIES, anti_rollback_checkpoint=False
            )
            status = ProtectedWriteStatus(
                hub=restarted,
                limits=LIMITS,
                current_enrollments=lambda: registry,
                clock=lambda: now,
                sign_response=sign,
                reason_codes=frozenset(),
                max_reservations=10,
            )
            ingress = ProtectedWriteTransport(
                limits=LIMITS,
                current_enrollments=lambda: registry,
                replay_store=replay_store,
                sequence_floor_mode=SequenceFloorMode.STRICT,
                clock=lambda: now,
                dispatch=status,
                reason_codes=frozenset(),
            )
            status_request = _request("status")
            status_request.update(
                proposal_sha256=digest, timestamp=now, body={"reservation_id": reservation_id}
            )
            async with serve(ingress, "127.0.0.1", 0) as server:
                port = server.sockets[0].getsockname()[1]
                async with connect(f"ws://127.0.0.1:{port}") as client:
                    await client.send(
                        json.dumps(
                            sign_frame(
                                status_request, key=KEY, nonce="n5", sequence=5, timestamp=now
                            )
                        )
                    )
                    known = json.loads(await asyncio.wait_for(client.recv(), 2))
                    assert known["body"]["reservation_id"] == reservation_id
                    assert known["body"]["admission_sequence"] == 2
                    assert known["body"]["operation_phase"] == "admitted"
            assert not (tmp_path / "records/note.md").exists()
    finally:
        replay_store.close()
        store.close()
