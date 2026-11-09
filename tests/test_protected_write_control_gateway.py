# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — real protected frontend process integration
"""Verify original signed intent across an actual child frontend and native authority."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from protected_write_control_examples import control_authority, frontend_entry
from protected_write_socket_examples import private_unix_directory as private_unix_directory
from protected_write_socket_examples import requires_linux_peer_credentials
from synapse_channel.core.message_auth import MessageReplayCache, VerificationResult, verify_frame
from synapse_channel.core.message_auth_durable import SequenceFloorMode
from synapse_channel.core.protected_write_request import parse_protected_write_request
from synapse_channel.core.protected_write_result import parse_protected_write_result
from synapse_channel.core.protected_write_session_auth import AuthenticatedProtectedRequest
from synapse_channel.core.protected_write_transport import ProtectedWriteTransport
from test_protected_write_proposal import LIMITS


@requires_linux_peer_credentials
async def test_two_clients_receive_only_request_bound_signed_results_from_one_frontend(
    tmp_path: Path, private_unix_directory: Path
) -> None:
    async with control_authority(tmp_path) as authority:
        async with frontend_entry(authority.ingress, private_unix_directory) as uri:
            async with connect(uri) as first, connect(uri) as second:
                requests = (authority.signed(1), authority.signed(2))
                await first.send(requests[0])
                await second.send(requests[1])
                results = await asyncio.gather(first.recv(), second.recv())
                replay = MessageReplayCache(window_seconds=10, max_entries=32)
                for number, raw in enumerate(results):
                    frame = json.loads(raw)
                    assert frame["request_id"] == f"control-status-{number + 1}"
                    assert frame["body"]["disposition"] == "known"
                    assert (
                        verify_frame(
                            frame,
                            keys={authority.authority_key.key_id: authority.authority_key},
                            replay_cache=replay,
                            now=authority.now,
                            required_sender="EXAMPLE/authority",
                        )
                        == VerificationResult.OK
                    )
                    request = parse_protected_write_request(requests[number], limits=LIMITS)
                    parse_protected_write_result(
                        raw,
                        request=request.canonical_bytes,
                        limits=LIMITS,
                        reason_codes=frozenset(),
                    )
                    with pytest.raises(ValueError):
                        parse_protected_write_result(
                            results[1 - number],
                            request=request.canonical_bytes,
                            limits=LIMITS,
                            reason_codes=frozenset(),
                        )
                before = authority.store.read_all()
                await first.send(requests[0])
                with pytest.raises(ConnectionClosed):
                    await first.recv()
                assert authority.store.read_all() == before


@pytest.mark.parametrize(
    "form",
    [
        "duplicate",
        "nonfinite",
        "large_integer",
        "surrogate",
        "number_form",
        "unsigned",
        "ordinary",
        "operator",
        "invalid_utf8",
        "oversized",
    ],
)
@requires_linux_peer_credentials
async def test_actual_frontend_refuses_ambiguous_unsigned_and_nonprotected_intent(
    tmp_path: Path, private_unix_directory: Path, form: str
) -> None:
    async with control_authority(tmp_path) as authority:
        original = authority.signed(1)
        frame = json.loads(original)
        raw: str | bytes
        if form == "duplicate":
            raw = '{"sender":"EXAMPLE/forged",' + original[1:]
        elif form == "nonfinite":
            frame["timestamp"] = float("nan")
            raw = json.dumps(frame)
        elif form == "large_integer":
            frame["timestamp"] = 1 << 53
            raw = json.dumps(frame)
        elif form == "surrogate":
            raw = original.replace("EXAMPLE/writer", "EXAMPLE/\\ud800")
        elif form == "number_form":
            raw = original.replace(str(authority.now), str(int(authority.now)) + "e0")
            assert raw != original
        elif form == "unsigned":
            frame.pop("auth")
            raw = json.dumps(frame)
        elif form == "ordinary":
            raw = '{"type":"claim","sender":"EXAMPLE/writer","task_id":"forged"}'
        elif form == "operator":
            raw = '{"op":"issue","metadata":{}}'
        elif form == "invalid_utf8":
            raw = b"\xff"
        else:
            raw = " " * 65537
        before = authority.store.read_all()
        operations = authority.store.read_operations()
        async with frontend_entry(authority.ingress, private_unix_directory) as uri:
            async with connect(uri) as client:
                await client.send(raw)
                with pytest.raises(ConnectionClosed) as error:
                    await asyncio.wait_for(client.recv(), 4)
                assert error.value.rcvd is not None
                assert error.value.rcvd.code in {1008, 1009}
                assert "Traceback" not in error.value.rcvd.reason
        assert authority.store.read_all() == before
        assert authority.store.read_operations() == operations


@requires_linux_peer_credentials
async def test_authority_recanonicalises_escaped_client_text_and_rechecks_revocation(
    tmp_path: Path, private_unix_directory: Path
) -> None:
    async with control_authority(tmp_path) as authority:
        async with frontend_entry(authority.ingress, private_unix_directory) as uri:
            async with connect(uri) as client:
                original = authority.signed(1)
                escaped = original.replace("EXAMPLE/writer", "EXAMPLE/\\u0077riter")
                assert escaped != original
                await client.send(escaped)
                result = json.loads(await client.recv())
                assert result["body"]["disposition"] == "known"
                assert result["request_id"] == "control-status-1"
                await authority.control.revoke(str(authority.request["session_id"]))
                await client.send(authority.signed(2))
                with pytest.raises(ConnectionClosed):
                    await client.recv()


@requires_linux_peer_credentials
async def test_frontend_returns_only_fixed_safe_error_when_actual_authority_journal_is_closed(
    tmp_path: Path, private_unix_directory: Path
) -> None:
    async with control_authority(tmp_path) as authority:
        async with frontend_entry(authority.ingress, private_unix_directory) as uri:
            authority.store.close()
            async with connect(uri) as client:
                await client.send(authority.signed(1))
                with pytest.raises(ConnectionClosed) as error:
                    await asyncio.wait_for(client.recv(), 4)
                assert error.value.rcvd is not None
                assert error.value.rcvd.code == 1011
                assert error.value.rcvd.reason == "protected service unavailable"


@pytest.mark.parametrize(
    "frontend", [False, pytest.param(True, marks=requires_linux_peer_credentials)]
)
async def test_committed_operation_then_enrollment_revocation_reports_unknown_on_both_transports(
    tmp_path: Path, private_unix_directory: Path, frontend: bool
) -> None:
    """Only dispatch-return timing is delayed; both mutations use the same real actor."""
    async with control_authority(tmp_path) as authority:
        returned = asyncio.Event()
        revoked = asyncio.Event()

        async def delayed_return(authenticated: AuthenticatedProtectedRequest) -> bytes:
            response = await authority.dispatch(authenticated)
            returned.set()
            await revoked.wait()
            return response

        transport = ProtectedWriteTransport(
            limits=LIMITS,
            current_enrollments=authority.control.current,
            replay_store=authority.replay,
            sequence_floor_mode=SequenceFloorMode.STRICT,
            clock=lambda: authority.now,
            dispatch=delayed_return,
            reason_codes=frozenset(),
        )
        async with frontend_entry(transport, private_unix_directory, frontend=frontend) as uri:
            async with connect(uri) as client:
                before = len(authority.store.read_operations())
                blocker = sqlite3.connect(tmp_path / "control-authority.db")
                blocker.execute("BEGIN IMMEDIATE")
                await client.send(authority.revoke(1))
                response = asyncio.create_task(client.recv())
                revocations: list[asyncio.Task[None]] = []
                try:
                    with pytest.raises(TimeoutError):
                        await asyncio.wait_for(asyncio.shield(response), 0.1)
                    revoke = asyncio.create_task(authority.control.revoke("control-operator"))
                    revocations.append(revoke)
                    blocker.close()
                    await asyncio.wait_for(returned.wait(), 2)
                    await asyncio.wait_for(revoke, 2)
                    assert len(authority.store.read_operations()) == before + 1
                    assert "control-operator" not in authority.control.current()
                    revoked.set()
                    with pytest.raises(ConnectionClosed) as error:
                        await asyncio.wait_for(response, 2)
                    assert error.value.rcvd is not None
                    assert error.value.rcvd.code == 1011
                    assert error.value.rcvd.reason == "protected service unavailable"
                finally:
                    blocker.close()
                    revoked.set()
                    await asyncio.gather(response, *revocations, return_exceptions=True)
