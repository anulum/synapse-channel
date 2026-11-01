# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real peer gate and request lifetime tests
"""Refuse actual foreign/TCP peers and drain real protected request connections."""

from __future__ import annotations

import asyncio
import itertools
import json
import os
import socket
import struct
from collections.abc import Iterator
from contextlib import AsyncExitStack
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest
from websockets.asyncio.client import connect, unix_connect
from websockets.asyncio.server import serve, unix_serve
from websockets.exceptions import ConnectionClosed

from protected_write_socket_examples import private_unix_directory as private_unix_directory
from protected_write_socket_examples import requires_linux_peer_credentials
from synapse_channel.core.hub import SynapseHub
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
from synapse_channel.core.protected_write_authority import (
    ProtectedWriteAuthority,
    assemble_protected_write_authority,
    serve_protected_write_authority,
)
from synapse_channel.core.protected_write_enrollment_journal import enrollment_issue_payload
from synapse_channel.core.protected_write_operator_socket import handle_operator_connection
from synapse_channel.core.protected_write_preparation import ProtectedPreparationPolicy
from synapse_channel.core.protected_write_session_auth import ProtectedSessionEnrollment
from synapse_channel.core.protected_write_unix_ingress import ProtectedWriteUnixIngress
from test_protected_write_admission_journal import POLICY

CLIENT_KEY = MessageAuthKey("peer-client", b"c" * 32, frozenset({"EXAMPLE/client"}))
AUTHORITY_KEY = MessageAuthKey("peer-authority", b"a" * 32, frozenset({"EXAMPLE/authority"}))


@pytest.fixture
def authority(tmp_path: Path) -> Iterator[tuple[ProtectedWriteAuthority, EventStore]]:
    """Build actual ingress without granting effects or recovery custody."""
    sequences = itertools.count(1)

    def sign(frame: dict[str, object]) -> dict[str, Any]:
        """Sign real ingress replies with fresh deterministic authority nonces and sequences."""
        number = next(sequences)
        return sign_frame(
            frame,
            key=AUTHORITY_KEY,
            nonce=f"peer-response-{number}",
            sequence=number,
            timestamp=1_900_000_000.0,
        )

    policy = replace(POLICY, verify_recovery=lambda _parent, _child: False)
    with (
        EventStore(tmp_path / "authority.db") as store,
        DurableMessageAuthReplayStore(
            tmp_path / "nonces.db", max_entries=32, window_seconds=10
        ) as replay,
    ):
        yield (
            assemble_protected_write_authority(
                hub=SynapseHub(
                    journal=store,
                    protected_write_policies={"enrollment": policy},
                    anti_rollback_checkpoint=False,
                ),
                sealed_keys={"peer-session": CLIENT_KEY},
                effects=ProtectedPreparationPolicy(
                    "enrollment", POLICY.limits, {}, {}, {}, {}, {}, 1024
                ),
                replay_policy=policy,
                writer=("EXAMPLE/writer", "incarnation"),
                operator_principals=frozenset({"EXAMPLE/operator"}),
                recovery_principals=frozenset({"EXAMPLE/recovery"}),
                clock=lambda: 1_900_000_000.0,
                sign_response=sign,
                replay_store=replay,
                sequence_floor_mode=SequenceFloorMode.STRICT,
            ),
            store,
        )


@pytest.mark.parametrize(
    ("uids", "bound"),
    [
        (frozenset(), 1),
        (frozenset(range(129)), 1),
        (frozenset({-1}), 1),
        (frozenset({True}), 1),
        (cast(frozenset[int], {1}), 1),
        (frozenset({1}), True),
        (frozenset({1}), 0),
        (frozenset({1}), 129),
    ],
)
def test_invalid_peer_and_capacity_policy_refuses_before_ingress(
    authority: tuple[ProtectedWriteAuthority, EventStore], uids: frozenset[int], bound: int
) -> None:
    """Invalid peer sets and connection bounds refuse before any real ingress work."""
    assembled, store = authority
    with pytest.raises(ValueError, match="invalid protected Unix ingress"):
        ProtectedWriteUnixIngress(assembled.ingress, allowed_uids=uids, max_connections=bound)
    assert store.read_all() == []


@pytest.mark.asyncio
@requires_linux_peer_credentials
async def test_real_foreign_unix_peer_and_tcp_are_refused(
    authority: tuple[ProtectedWriteAuthority, EventStore], private_unix_directory: Path
) -> None:
    """Refuse real foreign credentials and TCP without changing the journal."""
    assembled, store = authority
    adapter = ProtectedWriteUnixIngress(
        assembled.ingress, allowed_uids=frozenset({os.geteuid() + 1})
    )
    async with unix_serve(adapter, path=private_unix_directory / "foreign.sock"):
        async with unix_connect(private_unix_directory / "foreign.sock") as client:
            with pytest.raises(ConnectionClosed) as refused:
                await client.recv()
            assert refused.value.rcvd is not None and refused.value.rcvd.code == 1008
    async with serve(adapter, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        async with connect(f"ws://127.0.0.1:{port}") as client:
            with pytest.raises(ConnectionClosed) as refused:
                await client.recv()
            assert refused.value.rcvd is not None and refused.value.rcvd.code == 1008
    await adapter.aclose()
    assert store.read_all() == []


@pytest.mark.asyncio
@requires_linux_peer_credentials
async def test_owned_request_capacity_shutdown_and_late_connections(
    authority: tuple[ProtectedWriteAuthority, EventStore], private_unix_directory: Path
) -> None:
    """Current UID tests ownership only, not dedicated deployment proof."""
    assembled, store = authority
    adapter = ProtectedWriteUnixIngress(
        assembled.ingress, allowed_uids=frozenset({os.geteuid()}), max_connections=1
    )
    path = private_unix_directory / "owned.sock"
    async with unix_serve(adapter, path=path):
        async with unix_connect(path) as first:
            async with unix_connect(path) as excess:
                with pytest.raises(ConnectionClosed) as refused:
                    await excess.recv()
                assert refused.value.rcvd is not None and refused.value.rcvd.code == 1013
            await adapter.aclose()
            with pytest.raises(ConnectionClosed):
                await first.recv()
        async with unix_connect(path) as late:
            with pytest.raises(ConnectionClosed) as refused:
                await late.recv()
            assert refused.value.rcvd is not None and refused.value.rcvd.code == 1013
    await adapter.aclose()
    assert store.read_all() == []


@pytest.mark.parametrize(
    "failure", ["shared_peer", "duplicate_socket", "shared_operator", "operator_as_request_peer"]
)
@pytest.mark.asyncio
@requires_linux_peer_credentials
async def test_paired_listener_refuses_unapproved_roles_and_endpoint_aliases(
    authority: tuple[ProtectedWriteAuthority, EventStore],
    private_unix_directory: Path,
    failure: str,
) -> None:
    """Refuse shared roles or socket aliases before either acceptor starts."""
    assembled, store = authority
    with (
        socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as operator,
        socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as requests,
    ):
        operator.bind(str(private_unix_directory / "operator.sock"))
        requests.bind(str(private_unix_directory / "requests.sock"))
        operator_uid = os.geteuid() if failure == "shared_operator" else os.geteuid() + 1
        uid = os.geteuid() if failure == "shared_peer" else os.geteuid() + 2
        if failure == "operator_as_request_peer":
            uid = operator_uid
        request_socket = operator if failure == "duplicate_socket" else requests
        messages = {
            "shared_peer": "protected request peers require separate dedicated UIDs",
            "duplicate_socket": "operator and request listeners must be distinct",
            "shared_operator": "operator and authority require separate dedicated UIDs",
            "operator_as_request_peer": "protected request peers require separate dedicated UIDs",
        }
        async with AsyncExitStack() as owned:
            with pytest.raises(ValueError, match=messages[failure]):
                await owned.enter_async_context(
                    serve_protected_write_authority(
                        assembled,
                        operator_listener=operator,
                        request_listener=request_socket,
                        operator_uid=operator_uid,
                        request_uids=frozenset({uid}),
                        forbidden_uids=frozenset({0}),
                    )
                )
        assert operator.fileno() >= 0 and requests.fileno() >= 0
        assert store.read_all() == []


@pytest.mark.asyncio
@requires_linux_peer_credentials
async def test_allowed_peer_requires_current_session_and_fresh_signed_request(
    authority: tuple[ProtectedWriteAuthority, EventStore], private_unix_directory: Path
) -> None:
    """An allowed kernel UID cannot bypass replay or durable session revocation.

    Both endpoints use the actual current UID to exercise component wiring.
    This does not qualify the separate dedicated-role deployment listener.
    """
    assembled, store = authority
    enrollment = ProtectedSessionEnrollment(
        session_id="peer-session",
        principal="EXAMPLE/client",
        target="EXAMPLE/authority",
        authority_id="authority",
        authority_continuity="continuity",
        enrollment_revision="enrollment",
        transaction_id="transaction",
        proposal_sha256="a" * 64,
        verbs=frozenset({"status"}),
        expires_at=1_900_000_020.0,
        key=CLIENT_KEY,
    )
    adapter = ProtectedWriteUnixIngress(assembled.ingress, allowed_uids=frozenset({os.geteuid()}))
    operator_path = private_unix_directory / "operator.sock"
    request_path = private_unix_directory / "requests.sock"

    async def command(value: dict[str, object]) -> None:
        """Exchange one complete command with the actual UID-authenticated endpoint."""
        reader, writer = await asyncio.open_unix_connection(operator_path)
        try:
            raw = json.dumps(value).encode("ascii")
            writer.write(struct.pack("!I", len(raw)) + raw)
            await writer.drain()
            assert await asyncio.wait_for(reader.readline(), 2) == b'{"ok":true}\n'
        finally:
            writer.close()
            await writer.wait_closed()

    operator = await asyncio.start_unix_server(
        lambda reader, writer: handle_operator_connection(
            reader, writer, control=assembled.operator_control, operator_uid=os.geteuid()
        ),
        path=operator_path,
    )
    before_events, before_operations = store.read_all(), store.read_operations()
    try:
        async with operator, unix_serve(adapter, path=request_path):
            metadata = {
                name: value
                for name, value in enrollment_issue_payload(enrollment).items()
                if name not in {"schema_version", "key_fingerprint"}
            }
            await command({"op": "issue", "metadata": metadata})
            assert assembled.operator_control.current() == {enrollment.session_id: enrollment}
            request: dict[str, object] = {
                "schema_version": "synapse-protected-write.v1",
                "type": "protected_write_status",
                "sender": enrollment.principal,
                "target": enrollment.target,
                "payload": "",
                "timestamp": 1_900_000_000.0,
                "request_id": "peer-status",
                "authority_id": enrollment.authority_id,
                "authority_continuity": enrollment.authority_continuity,
                "session_id": enrollment.session_id,
                "transaction_id": enrollment.transaction_id,
                "proposal_sha256": enrollment.proposal_sha256,
                "enrollment_revision": enrollment.enrollment_revision,
                "body": {"reservation_id": None},
            }
            signed = json.dumps(
                sign_frame(
                    request,
                    key=CLIENT_KEY,
                    nonce="peer-query",
                    sequence=1,
                    timestamp=1_900_000_000.0,
                )
            )
            async with unix_connect(request_path) as client:
                await client.send(signed)
                response = json.loads(await asyncio.wait_for(client.recv(), 2))
                assert (
                    verify_frame(
                        response,
                        keys={AUTHORITY_KEY.key_id: AUTHORITY_KEY},
                        replay_cache=MessageReplayCache(window_seconds=10, max_entries=32),
                        now=1_900_000_000.0,
                        required_sender=enrollment.target,
                    )
                    == VerificationResult.OK
                )
                assert response["body"]["disposition"] == "unknown"
                await client.send(signed)
                with pytest.raises(ConnectionClosed) as refused:
                    await asyncio.wait_for(client.recv(), 2)
                assert refused.value.rcvd is not None and refused.value.rcvd.code == 1008
            await command({"op": "revoke", "session_id": enrollment.session_id})
            async with unix_connect(request_path) as client:
                await client.send(
                    json.dumps(
                        sign_frame(
                            request,
                            key=CLIENT_KEY,
                            nonce="peer-after-revoke",
                            sequence=2,
                            timestamp=1_900_000_000.0,
                        )
                    )
                )
                with pytest.raises(ConnectionClosed) as refused:
                    await asyncio.wait_for(client.recv(), 2)
                assert refused.value.rcvd is not None and refused.value.rcvd.code == 1008
    finally:
        await adapter.aclose()
    assert assembled.operator_control.current() == {}
    assert len(store.read_all()) == len(before_events) + 2
    assert store.read_operations() == before_operations


@pytest.mark.asyncio
@requires_linux_peer_credentials
async def test_native_peer_gate_refuses_before_http_handshake(
    authority: tuple[ProtectedWriteAuthority, EventStore], private_unix_directory: Path
) -> None:
    """Foreign Unix and actual TCP peers receive no HTTP response or signed dispatch."""
    assembled, store = authority
    adapter = ProtectedWriteUnixIngress(
        assembled.ingress, allowed_uids=frozenset({os.geteuid() + 1})
    )
    path = private_unix_directory / "early.sock"
    async with unix_serve(adapter, path=path, create_connection=adapter.connection_type()):
        reader, writer = await asyncio.open_unix_connection(path)
        try:
            assert await asyncio.wait_for(reader.read(1), 2) == b""
        finally:
            writer.close()
            await writer.wait_closed()
    async with serve(
        adapter, "127.0.0.1", 0, create_connection=adapter.connection_type()
    ) as server:
        reader, writer = await asyncio.open_connection(
            "127.0.0.1", server.sockets[0].getsockname()[1]
        )
        try:
            assert await asyncio.wait_for(reader.read(1), 2) == b""
        finally:
            writer.close()
            await writer.wait_closed()
    await adapter.aclose()
    assert store.read_all() == [] and store.read_operations() == ()


@pytest.mark.asyncio
@requires_linux_peer_credentials
async def test_unfinished_handshakes_consume_capacity_and_release_on_disconnect(
    authority: tuple[ProtectedWriteAuthority, EventStore], private_unix_directory: Path
) -> None:
    """A real idle handshake holds the one slot; EOF releases it for a genuine upgrade."""
    assembled, store = authority
    adapter = ProtectedWriteUnixIngress(
        assembled.ingress, allowed_uids=frozenset({os.geteuid()}), max_connections=1
    )
    path = private_unix_directory / "capacity.sock"
    async with unix_serve(adapter, path=path, create_connection=adapter.connection_type()):
        first_reader, first = await asyncio.open_unix_connection(path)
        try:
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(first_reader.read(1), 0.05)
            excess_reader, excess = await asyncio.open_unix_connection(path)
            try:
                assert await asyncio.wait_for(excess_reader.read(1), 2) == b""
            finally:
                excess.close()
                await excess.wait_closed()
        finally:
            first.close()
            await first.wait_closed()
        # Socket EOF must be processed before this separate successful opening handshake.
        await asyncio.wait_for(first_reader.read(1), 2)
        async with unix_connect(path) as client:
            await adapter.aclose()
            with pytest.raises(ConnectionClosed):
                await client.recv()
        late_reader, late = await asyncio.open_unix_connection(path)
        try:
            assert await asyncio.wait_for(late_reader.read(1), 2) == b""
        finally:
            late.close()
            await late.wait_closed()
    assert store.read_all() == [] and store.read_operations() == ()
