# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — journal-backed status through real ingress
from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import UnsupportedProtectedWriteHistoryError
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
from synapse_channel.core.protected_write_session_auth import authenticate_protected_request
from synapse_channel.core.protected_write_status import ProtectedWriteStatus
from synapse_channel.core.protected_write_transport import ProtectedWriteTransport
from test_protected_write_admission_journal import POLICIES, _committed
from test_protected_write_proposal import LIMITS
from test_protected_write_request import _request
from test_protected_write_session_auth import KEY, NOW, enrollment
from test_protected_write_transport import AUTHORITY_KEY


@pytest.mark.parametrize("known", [False, True])
@pytest.mark.parametrize("reservation_id", [None, "reservation", "missing"])
async def test_status_from_reopened_authority_over_socket(
    tmp_path: Path,
    known: bool,
    reservation_id: str | None,
) -> None:
    path = tmp_path / "admission-replay.db"
    original = _committed(tmp_path) if known else EventStore(path)
    original.close()
    store = EventStore(path)
    replay_store = DurableMessageAuthReplayStore(
        tmp_path / "nonces.db", max_entries=32, window_seconds=10
    )
    try:
        hub = SynapseHub(
            journal=store, protected_write_policies=POLICIES, anti_rollback_checkpoint=False
        )
        registry = {"session": enrollment()}
        status = ProtectedWriteStatus(
            hub=hub,
            limits=LIMITS,
            current_enrollments=lambda: registry,
            clock=lambda: NOW,
            sign_response=lambda frame: sign_frame(
                frame, key=AUTHORITY_KEY, nonce="response", sequence=1, timestamp=NOW
            ),
            reason_codes=frozenset(),
            max_reservations=10,
        )
        transport = ProtectedWriteTransport(
            limits=LIMITS,
            current_enrollments=lambda: registry,
            replay_store=replay_store,
            sequence_floor_mode=SequenceFloorMode.STRICT,
            clock=lambda: NOW,
            dispatch=status,
            reason_codes=frozenset(),
        )
        request = _request("status")
        request["body"] = {"reservation_id": reservation_id}
        signed = sign_frame(request, key=KEY, nonce="request", sequence=1, timestamp=NOW)
        before_events, before_operations = store.read_all(), store.read_operations()
        async with serve(
            transport, "127.0.0.1", 0, max_size=LIMITS.json_limits.max_wire_bytes
        ) as server:
            port = server.sockets[0].getsockname()[1]
            async with connect(f"ws://127.0.0.1:{port}") as client:
                await client.send(json.dumps(signed))
                response = json.loads(await asyncio.wait_for(client.recv(), 2))
        assert (
            verify_frame(
                response,
                keys={AUTHORITY_KEY.key_id: AUTHORITY_KEY},
                replay_cache=MessageReplayCache(window_seconds=10, max_entries=32),
                now=NOW,
                required_sender="EXAMPLE/authority",
            )
            == VerificationResult.OK
        )
        body = response["body"]
        if known and reservation_id != "missing":
            assert body["disposition"] == "known"
            assert body["reservation_id"] == "reservation"
            assert body["operation_phase"] == "admitted"
            assert body["admission_sequence"] == 2
            assert hub.state.protected_claim_custody["reservation"]
        else:
            assert body["disposition"] == "unknown"
            assert body["reservation_id"] is None
        assert store.read_all() == before_events
        assert store.read_operations() == before_operations
    finally:
        replay_store.close()
        store.close()


def test_real_hub_boot_refuses_missing_protected_policy(tmp_path: Path) -> None:
    store = _committed(tmp_path)
    try:
        with pytest.raises(UnsupportedProtectedWriteHistoryError):
            SynapseHub(journal=store, anti_rollback_checkpoint=False)
        hub = SynapseHub(
            journal=store, protected_write_policies=POLICIES, anti_rollback_checkpoint=False
        )
        assert "reservation" in hub.state.protected_write_reservations
    finally:
        store.close()


@pytest.mark.parametrize(
    "case", ["other-verb", "expired", "budget", "foreign", "journal", "identity", "ambiguous"]
)
async def test_status_checks_current_scope_without_writes(tmp_path: Path, case: str) -> None:
    store = _committed(tmp_path)
    hub = SynapseHub(
        journal=store, protected_write_policies=POLICIES, anti_rollback_checkpoint=False
    )
    registry = {"session": enrollment()}
    request = _request("begin" if case == "other-verb" else "status")
    if case == "foreign":
        registry["session"] = replace(enrollment(), transaction_id="foreign")
        request["transaction_id"] = "foreign"
        request["body"] = {"reservation_id": "reservation"}
    authenticated = authenticate_protected_request(
        json.dumps(sign_frame(request, key=KEY, nonce="n", sequence=1, timestamp=NOW)),
        limits=LIMITS,
        enrollments=registry,
        authenticated_principal="EXAMPLE/author",
        replay_cache=MessageReplayCache(window_seconds=10, max_entries=32),
        now=NOW,
    )
    status = ProtectedWriteStatus(
        hub=hub,
        limits=LIMITS,
        current_enrollments=lambda: registry,
        clock=lambda: NOW + 40 if case == "expired" else NOW,
        sign_response=lambda frame: sign_frame(
            frame, key=AUTHORITY_KEY, nonce="response", sequence=1, timestamp=NOW
        ),
        reason_codes=frozenset(),
        max_reservations=3 if case in {"identity", "ambiguous"} else 1,
    )
    if case == "budget":
        hub.state.protected_write_reservations["extra"] = hub.state.protected_write_reservations[
            "reservation"
        ]
    elif case == "journal":
        hub.journal = None
    elif case in {"identity", "ambiguous"}:
        original = hub.state.protected_write_reservations["reservation"]
        if case == "ambiguous":
            reply = json.loads(original.result_bytes)
            reply["body"]["reservation_id"] = "extra"
            original = replace(original, result_bytes=json.dumps(reply).encode())
        hub.state.protected_write_reservations["extra"] = original
    before_events, before_operations = store.read_all(), store.read_operations()
    try:
        if case == "foreign":
            response = json.loads(await status(authenticated))
            assert response["body"]["disposition"] == "unknown"
            assert response["body"]["reservation_id"] is None
        else:
            with pytest.raises(ValueError):
                await status(authenticated)
        assert store.read_all() == before_events
        assert store.read_operations() == before_operations
    finally:
        store.close()


@pytest.mark.parametrize("durable", [False, True])
def test_status_requires_durable_hub_and_positive_bound(tmp_path: Path, durable: bool) -> None:
    store = EventStore(tmp_path / "configuration.db")
    try:
        hub = SynapseHub(journal=store if durable else None, anti_rollback_checkpoint=False)
        with pytest.raises(ValueError):
            ProtectedWriteStatus(
                hub=hub,
                limits=LIMITS,
                current_enrollments=lambda: {},
                clock=lambda: NOW,
                sign_response=lambda frame: frame,
                reason_codes=frozenset(),
                max_reservations=0 if durable else 1,
            )
    finally:
        store.close()
