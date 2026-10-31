# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — journal-backed status through real ingress
"""Read signed status from actual retained reservation history and custody."""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from synapse_channel.core.atomic_operations import canonical_request_digest
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import UnsupportedProtectedWriteHistoryError
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
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protected_write_admission import finalize_protected_write_admission
from synapse_channel.core.protected_write_admission_journal import (
    ADMISSION_EVENT_KIND,
    protected_admission_event_payload,
)
from synapse_channel.core.protected_write_request import protected_write_operation_key
from synapse_channel.core.protected_write_session_auth import authenticate_protected_request
from synapse_channel.core.protected_write_status import ProtectedWriteStatus
from synapse_channel.core.protected_write_transport import ProtectedWriteTransport
from test_protected_write_admission import CONTEXT
from test_protected_write_admission_journal import POLICIES, _committed
from test_protected_write_proposal import LIMITS
from test_protected_write_request import _request
from test_protected_write_session_auth import KEY, NOW, enrollment
from test_protected_write_transition_journal import _begin, _pending, _settle, _state, _transition
from test_protected_write_transport import AUTHORITY_KEY


@pytest.mark.parametrize("known", [False, True])
@pytest.mark.parametrize("reservation_id", [None, "reservation", "missing"])
async def test_status_from_reopened_authority_over_socket(
    tmp_path: Path,
    known: bool,
    reservation_id: str | None,
) -> None:
    """Query reopened durable authority status over a real authenticated socket."""
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
    """Protected history refuses replay without its policy and succeeds with the actual policy."""
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
    "case",
    [
        "other-verb",
        "expired",
        "budget",
        "foreign",
        "journal",
        "identity",
        "inconsistent-extra",
        "admission",
        "journal-result",
        "executing",
        "executing-no-custody",
        "executing-wrong-custody",
    ],
)
async def test_status_checks_current_scope_without_writes(tmp_path: Path, case: str) -> None:
    """Status refuses stale or conflicting authenticated scope without changing durable state."""
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
        max_reservations=3 if case in {"identity", "inconsistent-extra"} else 1,
    )
    if case.startswith("executing"):
        _begin(store, hub.state)
        if case == "executing-no-custody":
            hub.state.protected_claim_custody.pop("reservation")
        elif case == "executing-wrong-custody":
            hub.state.protected_claim_custody["reservation"] = ()
    if case == "budget":
        hub.state.protected_write_reservations["extra"] = hub.state.protected_write_reservations[
            "reservation"
        ]
    elif case == "journal":
        hub.journal = None
    elif case == "admission":
        hub.state.protected_write_admissions.pop("reservation")
    elif case == "journal-result":
        original = hub.state.protected_write_reservations["reservation"]
        reply = json.loads(original.result_bytes)
        reply["timestamp"] += 1.0
        hub.state.protected_write_reservations["reservation"] = replace(
            original, result_bytes=json.dumps(reply, sort_keys=True, separators=(",", ":")).encode()
        )
    elif case in {"identity", "inconsistent-extra"}:
        original = hub.state.protected_write_reservations["reservation"]
        if case == "inconsistent-extra":
            reply = json.loads(original.result_bytes)
            reply["body"]["reservation_id"] = "extra"
            original = replace(original, result_bytes=json.dumps(reply).encode())
        hub.state.protected_write_reservations["extra"] = original
    before_events, before_operations = store.read_all(), store.read_operations()
    try:
        if case == "executing":
            response = json.loads(await status(authenticated))
            assert response["body"]["disposition"] == "known"
            assert response["body"]["operation_phase"] == "executing"
            assert response["body"]["begin_sequence"] == 4
        elif case.startswith("executing-"):
            with pytest.raises(ValueError, match="protected lineage terminal custody mismatch"):
                await status(authenticated)
        elif case == "foreign":
            response = json.loads(await status(authenticated))
            assert response["body"]["disposition"] == "unknown"
            assert response["body"]["reservation_id"] is None
        else:
            messages = {
                "other-verb": "status handler cannot execute other operations",
                "expired": "session expired",
                "budget": "status reservation budget exceeded",
                "journal": "status authority journal unavailable or incomplete",
                "identity": "authority reservation identity inconsistent",
                "inconsistent-extra": "authority reservation admission inconsistent",
                "admission": "authority reservation admission inconsistent",
                "journal-result": "authority reservation differs from durable operation",
            }
            with pytest.raises(ValueError, match=messages[case]):
                await status(authenticated)
        assert store.read_all() == before_events
        assert store.read_operations() == before_operations
    finally:
        store.close()


@pytest.mark.parametrize("durable", [False, True])
def test_status_requires_durable_hub_and_positive_bound(tmp_path: Path, durable: bool) -> None:
    """Status construction refuses volatile authority or a nonpositive reservation bound."""
    store = EventStore(tmp_path / "configuration.db")
    try:
        hub = SynapseHub(journal=store if durable else None, anti_rollback_checkpoint=False)
        with pytest.raises(
            ValueError,
            match="explicit reservation bound" if durable else "durable authority",
        ):
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


async def test_transaction_lookup_refuses_two_durable_reservations(tmp_path: Path) -> None:
    """Two real durable reservations for one transaction cannot produce an ambiguous status."""
    store = _committed(tmp_path)
    try:
        state = _state(store)
        admission = state.protected_write_admissions["reservation"]
        _transition(
            store,
            state,
            "revoke",
            disposition="effective",
            operation_phase="settled",
            revocation_phase="effective",
            outcome="no_write",
            evidence_reference=json.loads(admission.proposal_bytes)["content_reference"],
        )
        assert state.protected_claim_custody == {}
        request = json.loads(admission.request_bytes)
        request["request_id"] = "second-admission"
        raw = json.dumps(request)
        result = json.loads(admission.result_bytes)
        result["request_id"] = request["request_id"]
        result["body"].update(
            reservation_id="second-reservation",
            request_digest=canonical_request_digest(request),
        )
        candidate = deepcopy(state)
        store.commit_operation(
            operation_key=protected_write_operation_key(
                raw,
                limits=LIMITS,
                authenticated_principal=CONTEXT.author_principal,
                authority_id=CONTEXT.authority_id,
                authority_continuity=CONTEXT.authority_continuity,
            ),
            request_digest=canonical_request_digest(request),
            response=result,
            events=(
                (
                    ADMISSION_EVENT_KIND,
                    protected_admission_event_payload(raw, context=CONTEXT, limits=LIMITS),
                ),
            ),
            intent={"family": "protected-admission"},
            finalize_response=lambda draft, sequences: finalize_protected_write_admission(
                draft,
                sequences,
                candidate=candidate,
                request=raw,
                context=CONTEXT,
                limits=LIMITS,
                reason_codes=frozenset(),
                max_reservations=10,
                sign_response=lambda frame: sign_frame(
                    frame, key=AUTHORITY_KEY, nonce="second", sequence=2, timestamp=NOW
                ),
            ),
        )
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
                frame, key=AUTHORITY_KEY, nonce="response", sequence=3, timestamp=NOW
            ),
            reason_codes=frozenset(),
            max_reservations=10,
        )
        before_events, before_operations = store.read_all(), store.read_operations()
        for sequence, reservation_id in enumerate(
            ("reservation", "second-reservation", None), start=1
        ):
            query = _request("status")
            query["body"] = {"reservation_id": reservation_id}
            authenticated = authenticate_protected_request(
                json.dumps(
                    sign_frame(
                        query, key=KEY, nonce=str(sequence), sequence=sequence, timestamp=NOW
                    )
                ),
                limits=LIMITS,
                enrollments=registry,
                authenticated_principal="EXAMPLE/author",
                replay_cache=MessageReplayCache(window_seconds=10, max_entries=32),
                now=NOW,
            )
            if reservation_id is None:
                with pytest.raises(ValueError, match="ambiguous transaction reservation"):
                    await status(authenticated)
            else:
                body = json.loads(await status(authenticated))["body"]
                assert body["disposition"] == "known"
                assert body["reservation_id"] == reservation_id
                assert body["operation_phase"] == (
                    "settled" if reservation_id == "reservation" else "admitted"
                )
        assert store.read_all() == before_events
        assert store.read_operations() == before_operations
    finally:
        store.close()


@pytest.mark.parametrize(
    "phase", ["admitted", "executing", "revoke-requested", "uncertain", "uncertain-revoked"]
)
@pytest.mark.parametrize("damage", [None, "missing", "wrong"])
async def test_every_custody_holding_phase_requires_exact_current_custody(
    tmp_path: Path, phase: str, damage: str | None
) -> None:
    """Status validates real durable lifecycle custody, including requested/uncertain phases."""
    with _committed(tmp_path) as store:
        hub = SynapseHub(
            journal=store, protected_write_policies=POLICIES, anti_rollback_checkpoint=False
        )
        if phase != "admitted":
            _begin(store, hub.state)
        if phase in {"revoke-requested", "uncertain-revoked"}:
            _pending(store, hub.state)
        if phase in {"uncertain", "uncertain-revoked"}:
            _settle(store, hub.state, "unknown")
        registry = {"session": enrollment()}
        query = _request("status")
        authenticated = authenticate_protected_request(
            json.dumps(
                sign_frame(query, key=KEY, nonce="custody-status", sequence=1, timestamp=NOW)
            ),
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
            clock=lambda: NOW,
            sign_response=lambda frame: sign_frame(
                frame, key=AUTHORITY_KEY, nonce="custody-reply", sequence=1, timestamp=NOW
            ),
            reason_codes=frozenset(),
            max_reservations=10,
        )
        if damage == "missing":
            hub.state.protected_claim_custody.pop("reservation")
        elif damage == "wrong":
            hub.state.protected_claim_custody["reservation"] = ()
        before = store.read_all(), store.read_operations()
        if damage:
            with pytest.raises(ValueError, match="protected lineage terminal custody mismatch"):
                await status(authenticated)
        else:
            body = json.loads(await status(authenticated))["body"]
            assert body["disposition"] == "known"
            assert body["operation_phase"] == (
                "admitted"
                if phase == "admitted"
                else "recovery_required"
                if phase.startswith("uncertain")
                else "executing"
            )
            assert body["revocation_phase"] == (
                "requested" if phase in {"revoke-requested", "uncertain-revoked"} else "open"
            )
        assert (store.read_all(), store.read_operations()) == before


@pytest.mark.parametrize("principal", ["EXAMPLE/writer", "EXAMPLE/operator", "EXAMPLE/recovery"])
async def test_status_is_visible_to_enrolled_transaction_participants(
    tmp_path: Path, principal: str
) -> None:
    """A separately signed participant can query the author's reservation in its exact scope."""
    with _committed(tmp_path) as store:
        hub = SynapseHub(
            journal=store, protected_write_policies=POLICIES, anti_rollback_checkpoint=False
        )
        key = MessageAuthKey(
            "participant", b"participant-test-only-key-material", frozenset({principal})
        )
        grant = replace(enrollment(), principal=principal, key=key, verbs=frozenset({"status"}))
        registry = {grant.session_id: grant}
        query = _request("status")
        query["sender"] = principal
        authenticated = authenticate_protected_request(
            json.dumps(
                sign_frame(query, key=key, nonce="participant-query", sequence=1, timestamp=NOW)
            ),
            limits=LIMITS,
            enrollments=registry,
            authenticated_principal=principal,
            replay_cache=MessageReplayCache(window_seconds=10, max_entries=32),
            now=NOW,
        )
        status = ProtectedWriteStatus(
            hub=hub,
            limits=LIMITS,
            current_enrollments=lambda: registry,
            clock=lambda: NOW,
            sign_response=lambda frame: sign_frame(
                frame, key=AUTHORITY_KEY, nonce="participant-reply", sequence=1, timestamp=NOW
            ),
            reason_codes=frozenset(),
            max_reservations=10,
        )
        before = store.read_all(), store.read_operations()
        reply = json.loads(await status(authenticated))
        assert reply["target"] == principal
        assert reply["body"]["reservation_id"] == "reservation"
        assert reply["body"]["disposition"] == "known"
        assert (store.read_all(), store.read_operations()) == before
