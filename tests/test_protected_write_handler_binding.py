# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real protected-write handler binding
"""Exercise concrete handler binding through real signed WebSocket operations."""

from __future__ import annotations

import asyncio
import itertools
import json
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from protected_write_authority_examples import (
    AUTHOR_KEY,
    AUTHORITY_KEY,
    RECOVERY_KEY,
    WRITER_KEY,
    _grant,
)
from protected_write_control_examples import frontend_entry
from protected_write_socket_examples import private_unix_directory as private_unix_directory
from protected_write_socket_examples import requires_linux_peer_credentials
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
from synapse_channel.core.protected_write_admission_journal import ProtectedAdmissionReplayPolicy
from synapse_channel.core.protected_write_authority import assemble_protected_write_authority
from synapse_channel.core.protected_write_enrollment_control import ProtectedEnrollmentControl
from synapse_channel.core.protected_write_handler_binding import assemble_protected_write_dispatch
from synapse_channel.core.protected_write_preparation import ProtectedPreparationPolicy
from synapse_channel.core.protected_write_proposal import parse_protected_write_proposal
from synapse_channel.core.protected_write_session_auth import ProtectedSessionEnrollment
from test_protected_write_admission import _claim
from test_protected_write_effects import _plan
from test_protected_write_proposal import LIMITS
from test_protected_write_request import _request


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "frontend", [False, pytest.param(True, marks=requires_linux_peer_credentials)]
)
async def test_seven_verbs_share_real_transport_actor_and_enrollment(
    tmp_path: Path, private_unix_directory: Path, frontend: bool
) -> None:
    """One assembled ingress routes all verbs without inventing writer effects."""
    now = time.time()
    store = EventStore(tmp_path / "authority.db")
    claim = _claim()
    claim.lease_expires_at = now + 60
    claim.worktree = str(tmp_path)
    record_claim(store, claim)
    policy = ProtectedAdmissionReplayPolicy(
        LIMITS,
        frozenset(),
        10,
        verify_recovery=lambda _parent, _child: False,
    )
    hub = SynapseHub(
        journal=store,
        protected_write_policies={"enrollment": policy},
        anti_rollback_checkpoint=False,
    )
    proposal = _plan()
    proposal["claims"][0]["lease_expires_at"] = claim.lease_expires_at
    digest = parse_protected_write_proposal(json.dumps(proposal), limits=LIMITS).proposal_sha256
    author = _grant(
        principal="EXAMPLE/author",
        session_id="session",
        key=AUTHOR_KEY,
        verbs=frozenset({"prepare", "admit", "status", "revoke"}),
        digest=digest,
        now=now,
    )
    writer = _grant(
        principal="EXAMPLE/writer",
        session_id="writer-session",
        key=WRITER_KEY,
        verbs=frozenset({"begin", "settle"}),
        digest=digest,
        now=now,
        incarnation="incarnation",
    )
    recovery = _grant(
        principal="EXAMPLE/recovery",
        session_id="recovery-session",
        key=RECOVERY_KEY,
        verbs=frozenset({"recover"}),
        digest=digest,
        now=now,
    )
    control = ProtectedEnrollmentControl(
        hub=hub,
        sealed_keys={grant.session_id: grant.key for grant in (author, writer, recovery)},
        clock=lambda: now,
    )
    for grant in (author, writer, recovery):
        await control.issue(grant)
    root_stat = tmp_path.stat()
    effects = ProtectedPreparationPolicy(
        "enrollment",
        LIMITS,
        {"memory": (root_stat.st_dev, root_stat.st_ino)},
        {("memory", "records/note.md"): frozenset({"task-1"})},
        {
            ("memory", "records/note.md"): frozenset({"create", "fsync"}),
            ("memory", "records"): frozenset({"fsync"}),
        },
        {("memory", "records/note.md"): ("memory", "records")},
        {},
        65536,
    )
    sequence = itertools.count(1)

    def sign(frame: dict[str, object]) -> dict[str, object]:
        """Sign dispatch replies with the shared authority key and increasing sequence."""
        number = next(sequence)
        return sign_frame(
            frame,
            key=AUTHORITY_KEY,
            nonce=f"dispatch-reply-{number}",
            sequence=number,
            timestamp=now,
        )

    with pytest.raises(ValueError, match="effect and replay limits differ"):
        assemble_protected_write_dispatch(
            hub=hub,
            effects=replace(effects, limits=replace(LIMITS, max_claims=LIMITS.max_claims + 1)),
            replay_policy=policy,
            current_enrollments=control.current,
            writer=("EXAMPLE/writer", "incarnation"),
            operator_principals=frozenset(),
            recovery_principals=frozenset({"EXAMPLE/recovery"}),
            clock=lambda: now,
            sign_response=sign,
        )
    before_policy_events = store.read_all()
    before_policy_operations = store.read_operations()
    with pytest.raises(
        ValueError, match="replay policy differs from the retained enrollment revision"
    ):
        assemble_protected_write_dispatch(
            hub=hub,
            effects=effects,
            replay_policy=replace(policy, verify_recovery=lambda _parent, _child: True),
            current_enrollments=control.current,
            writer=("EXAMPLE/writer", "incarnation"),
            operator_principals=frozenset(),
            recovery_principals=frozenset({"EXAMPLE/recovery"}),
            clock=lambda: now,
            sign_response=sign,
        )
    assert store.read_all() == before_policy_events
    assert store.read_operations() == before_policy_operations
    replay_store = DurableMessageAuthReplayStore(
        tmp_path / "nonces.db", max_entries=32, window_seconds=10
    )
    authority = assemble_protected_write_authority(
        hub=hub,
        sealed_keys={grant.session_id: grant.key for grant in (author, writer, recovery)},
        effects=effects,
        replay_policy=policy,
        writer=("EXAMPLE/writer", "incarnation"),
        operator_principals=frozenset(),
        recovery_principals=frozenset({"EXAMPLE/recovery"}),
        clock=lambda: now,
        sign_response=sign,
        replay_store=replay_store,
        sequence_floor_mode=SequenceFloorMode.STRICT,
    )
    try:
        async with frontend_entry(
            authority.ingress, private_unix_directory, frontend=frontend
        ) as uri:

            async def send(
                verb: str,
                grant: ProtectedSessionEnrollment,
                *,
                nonce: str,
                number: int,
                body: dict[str, object],
                refused: bool = False,
                request_id: str | None = None,
            ) -> dict[str, Any] | None:
                """Authenticate one real protected request and inspect its response or refused
                connection.
                """
                request = _request(verb)
                request.update(
                    sender=grant.principal,
                    session_id=grant.session_id,
                    request_id=request_id or f"dispatch-{verb}",
                    proposal_sha256=digest,
                    timestamp=now,
                    body=body,
                )
                signed = sign_frame(
                    request, key=grant.key, nonce=nonce, sequence=number, timestamp=now
                )
                async with connect(uri) as client:
                    await client.send(json.dumps(signed))
                    if refused:
                        with pytest.raises(ConnectionClosed):
                            await asyncio.wait_for(client.recv(), 2)
                        return None
                    result: dict[str, Any] = json.loads(await asyncio.wait_for(client.recv(), 2))
                    assert (
                        verify_frame(
                            result,
                            keys={AUTHORITY_KEY.key_id: AUTHORITY_KEY},
                            replay_cache=MessageReplayCache(window_seconds=10, max_entries=32),
                            now=now,
                            required_sender="EXAMPLE/authority",
                        )
                        == VerificationResult.OK
                    )
                    return result

            prepared = await send(
                "prepare", author, nonce="prepare", number=1, body={"proposal": proposal}
            )
            assert prepared is not None and prepared["body"]["disposition"] == "allowed"
            assert hub.state.protected_write_reservations == {}
            admitted = await send(
                "admit", author, nonce="admit", number=2, body={"proposal": proposal}
            )
            assert admitted is not None and admitted["body"]["disposition"] == "accepted"
            reservation_id = admitted["body"]["reservation_id"]
            status = await send(
                "status",
                author,
                nonce="status",
                number=3,
                body={"reservation_id": reservation_id},
            )
            assert status is not None and status["body"]["disposition"] == "known"
            revoked = await send(
                "revoke",
                author,
                nonce="revoke",
                number=4,
                body={"reservation_id": reservation_id},
            )
            assert revoked is not None and revoked["body"]["disposition"] == "effective"
            assert hub.state.protected_claim_custody == {}
            await send(
                "begin",
                writer,
                nonce="begin",
                number=1,
                body={"reservation_id": reservation_id, "writer_incarnation": "incarnation"},
                refused=True,
            )
            settlement = cast(dict[str, object], _request("settle")["body"])
            settlement["reservation_id"] = reservation_id
            await send("settle", writer, nonce="settle", number=2, body=settlement, refused=True)
            await send(
                "recover",
                recovery,
                nonce="recover",
                number=1,
                body={"parent_reservation_id": reservation_id, "proposal": proposal},
                refused=True,
            )
            assert len(store.read_operations()) == 2
            again = await send(
                "admit",
                author,
                nonce="admit-again",
                number=5,
                body={"proposal": proposal},
                request_id="dispatch-admit-again",
            )
            assert again is not None and again["body"]["disposition"] == "accepted"
            await send(
                "status",
                author,
                nonce="status-all",
                number=6,
                body={"reservation_id": None},
                refused=True,
            )
            assert len(store.read_operations()) == 3
    finally:
        replay_store.close()
        store.close()
