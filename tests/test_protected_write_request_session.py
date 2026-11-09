# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — live authenticated protected sessions
"""Exercise connection binding, live revocation and shared durable nonce state."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import pytest

from protected_write_control_examples import control_authority
from synapse_channel.core.message_auth import MessageReplayCache, VerificationResult, verify_frame
from synapse_channel.core.message_auth_durable import SequenceFloorMode
from synapse_channel.core.protected_write_refusal import ProtectedWriteRefusalError
from synapse_channel.core.protected_write_session_auth import ProtectedSessionEnrollment
from synapse_channel.core.protected_write_transport import ProtectedWriteTransport
from test_protected_write_proposal import LIMITS


async def test_request_sessions_share_real_actor_and_durable_replay(tmp_path: Path) -> None:
    async with control_authority(tmp_path) as authority:
        session = authority.ingress.open_session()
        request = authority.signed(1)
        result = json.loads(await session.exchange(request))
        assert result["body"]["disposition"] == "known"
        assert (
            verify_frame(
                result,
                keys={authority.authority_key.key_id: authority.authority_key},
                replay_cache=MessageReplayCache(window_seconds=10, max_entries=32),
                now=authority.now,
                required_sender="EXAMPLE/authority",
            )
            == VerificationResult.OK
        )
        before = authority.store.read_all()
        with pytest.raises(ValueError):
            await authority.ingress.open_session().exchange(request)
        assert authority.store.read_all() == before
        await authority.control.revoke(str(authority.request["session_id"]))
        with pytest.raises(ValueError):
            await session.exchange(authority.signed(2))
        with pytest.raises(ValueError, match="unavailable"):
            await session.exchange(authority.signed(3))


@pytest.mark.parametrize(
    "raw",
    [
        '{"type":"claim","sender":"EXAMPLE/writer","task_id":"forged"}',
        '{"op":"issue","metadata":{}}',
        '{"authenticated":true}',
    ],
)
async def test_request_session_refuses_nonprotected_intent_before_mutation(
    tmp_path: Path, raw: str
) -> None:
    async with control_authority(tmp_path) as authority:
        before = authority.store.read_all()
        operations = authority.store.read_operations()
        with pytest.raises(ValueError):
            await authority.ingress.open_session().exchange(raw)
        assert authority.store.read_all() == before
        assert authority.store.read_operations() == operations


async def test_concurrent_session_call_cannot_convert_an_admitted_mutation_into_a_refusal(
    tmp_path: Path,
) -> None:
    async with control_authority(tmp_path) as authority:
        before = len(authority.store.read_operations())
        session = authority.ingress.open_session()
        blocker = sqlite3.connect(tmp_path / "control-authority.db")
        blocker.execute("BEGIN IMMEDIATE")
        mutation = asyncio.create_task(session.exchange(authority.revoke(1)))
        try:
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(mutation), 0.1)
            with pytest.raises(ValueError, match="session unavailable"):
                await session.exchange(authority.signed(1))
        finally:
            blocker.close()
            await asyncio.gather(mutation, return_exceptions=True)
        assert isinstance(mutation.exception(), RuntimeError)
        assert len(authority.store.read_operations()) == before + 1
        with pytest.raises(ValueError, match="session unavailable"):
            await session.exchange(authority.signed(2))


async def test_postcommit_registry_refusal_marker_cannot_label_a_committed_mutation_refused(
    tmp_path: Path,
) -> None:
    async with control_authority(tmp_path) as authority:

        def current() -> dict[str, ProtectedSessionEnrollment]:
            registry = authority.control.current()
            reservation = next(iter(authority.hub.state.protected_write_reservations.values()))
            if json.loads(reservation.result_bytes)["body"]["revocation_phase"] == "requested":
                raise ProtectedWriteRefusalError("registry unavailable after dispatch")
            return registry

        ingress = ProtectedWriteTransport(
            limits=LIMITS,
            current_enrollments=current,
            replay_store=authority.replay,
            sequence_floor_mode=SequenceFloorMode.STRICT,
            clock=lambda: authority.now,
            dispatch=authority.dispatch,
            reason_codes=frozenset(),
        )
        before = len(authority.store.read_operations())
        with pytest.raises(RuntimeError, match="outcome unavailable"):
            await ingress.open_session().exchange(authority.revoke(1))
        assert len(authority.store.read_operations()) == before + 1
