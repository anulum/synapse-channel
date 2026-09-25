# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real socket protected session ingress tests
from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

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
from synapse_channel.core.protected_write_session_auth import AuthenticatedProtectedRequest
from synapse_channel.core.protected_write_transport import ProtectedWriteTransport
from test_protected_write_proposal import LIMITS
from test_protected_write_request import _request
from test_protected_write_result import _result
from test_protected_write_session_auth import KEY, NOW, enrollment

AUTHORITY_KEY = MessageAuthKey("authority-key", b"z" * 32, frozenset({"EXAMPLE/authority"}))


async def test_default_hub_socket_does_not_activate_protected_protocol(tmp_path: Path) -> None:
    """The normal hub listener must never interpret a protected frame as a grant."""
    store = EventStore(tmp_path / "ordinary-hub.db")
    hub = SynapseHub(journal=store, anti_rollback_checkpoint=False)
    try:
        async with serve(hub.handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            async with connect(f"ws://127.0.0.1:{port}") as client:
                signed = sign_frame(
                    _request("admit"), key=KEY, nonce="default-hub", sequence=1, timestamp=NOW
                )
                await client.send(json.dumps(signed))
                try:
                    response = json.loads(await asyncio.wait_for(client.recv(), 0.5))
                except (TimeoutError, ConnectionClosed):
                    response = None
                assert response is None or response["type"] != "protected_write_result"
        assert hub.state.protected_write_reservations == {}
        assert not any(event.kind.startswith("protected_write") for event in store.read_all())
    finally:
        store.close()


def request(sequence: int = 1, **changes: object) -> str:
    frame = {**_request("status"), **changes}
    return json.dumps(
        sign_frame(frame, key=KEY, nonce=f"nonce-{sequence}", sequence=sequence, timestamp=NOW)
    )


class Responses:
    """Actual signed protocol responses for transport-only status tests."""

    def __init__(self) -> None:
        self.calls = 0
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()
        self.failure: str | None = None

    async def __call__(self, authenticated: AuthenticatedProtectedRequest) -> bytes:
        self.calls += 1
        self.entered.set()
        await self.release.wait()
        if self.failure == "storage":
            raise OSError("private filesystem detail must not escape")
        if self.failure == "type":
            return None  # type: ignore[return-value] # Exercise a broken trusted handler boundary.
        _, response = _result("status")
        source = json.loads(authenticated.parsed.canonical_bytes)
        response.update(
            {
                name: source[name]
                for name in (
                    "request_id",
                    "session_id",
                    "transaction_id",
                    "proposal_sha256",
                    "enrollment_revision",
                    "authority_id",
                    "authority_continuity",
                )
            }
        )
        body: Any = response["body"]
        body["request_digest"] = authenticated.parsed.request_digest
        if self.failure == "binding":
            response["transaction_id"] = "foreign"
        signed = sign_frame(
            response,
            key=AUTHORITY_KEY,
            nonce=f"reply-{self.calls}",
            sequence=self.calls,
            timestamp=NOW,
        )
        return json.dumps(signed).encode()


@pytest.mark.parametrize("restart", [False, True])
async def test_real_socket_signature_and_durable_replay(tmp_path: Path, restart: bool) -> None:
    path = tmp_path / "replay.db"
    registry = {"session": enrollment()}
    responses = Responses()
    store = DurableMessageAuthReplayStore(path, max_entries=32, window_seconds=10)

    def ingress(active: DurableMessageAuthReplayStore) -> ProtectedWriteTransport:
        return ProtectedWriteTransport(
            limits=LIMITS,
            current_enrollments=lambda: registry,
            replay_store=active,
            sequence_floor_mode=SequenceFloorMode.STRICT,
            clock=lambda: NOW,
            dispatch=responses,
            reason_codes=frozenset(),
        )

    try:
        async with serve(
            ingress(store), "127.0.0.1", 0, max_size=LIMITS.json_limits.max_wire_bytes
        ) as server:
            port = server.sockets[0].getsockname()[1]
            async with connect(f"ws://127.0.0.1:{port}") as client:
                await client.send(request())
                reply = json.loads(await asyncio.wait_for(client.recv(), 2))
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
                if not restart:
                    await client.send(request())
                    with pytest.raises(ConnectionClosed):
                        await asyncio.wait_for(client.recv(), 2)
                    assert client.close_code == 1008
        if restart:
            store.close()
            store = DurableMessageAuthReplayStore(path, max_entries=32, window_seconds=10)
            async with serve(ingress(store), "127.0.0.1", 0) as server:
                port = server.sockets[0].getsockname()[1]
                async with connect(f"ws://127.0.0.1:{port}") as client:
                    await client.send(request())
                    with pytest.raises(ConnectionClosed):
                        await asyncio.wait_for(client.recv(), 2)
                    assert client.close_code == 1008
        assert responses.calls == 1
    finally:
        store.close()


@pytest.mark.parametrize(
    "case",
    ["missing", "spoof", "switch", "revoked", "response-revoked", "storage", "type", "binding"],
)
async def test_socket_refusals_are_private(tmp_path: Path, case: str) -> None:
    registry = {"session": enrollment()}
    responses = Responses()
    store = DurableMessageAuthReplayStore(
        tmp_path / "refusal.db", max_entries=32, window_seconds=10
    )
    transport = ProtectedWriteTransport(
        limits=LIMITS,
        current_enrollments=lambda: registry,
        replay_store=store,
        sequence_floor_mode=SequenceFloorMode.STRICT,
        clock=lambda: NOW,
        dispatch=responses,
        reason_codes=frozenset(),
    )
    try:
        async with serve(transport, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            async with connect(f"ws://127.0.0.1:{port}") as client:
                if case in {"switch", "revoked"}:
                    await client.send(request())
                    await asyncio.wait_for(client.recv(), 2)
                raw = request(2)
                if case == "missing":
                    raw = request(session_id="unknown")
                elif case == "spoof":
                    raw = request(sender="EXAMPLE/attacker")
                elif case == "switch":
                    registry["other"] = replace(enrollment(), session_id="other")
                    raw = request(2, session_id="other")
                elif case == "revoked":
                    registry["session"] = replace(enrollment(), revoked=True)
                elif case == "response-revoked":
                    responses.release.clear()
                else:
                    responses.failure = case
                await client.send(raw)
                if case == "response-revoked":
                    await asyncio.wait_for(responses.entered.wait(), 2)
                    registry["session"] = replace(enrollment(), revoked=True)
                    responses.release.set()
                with pytest.raises(ConnectionClosed):
                    await asyncio.wait_for(client.recv(), 2)
                assert client.close_code == (1011 if case == "storage" else 1008)
                assert client.close_reason in {
                    "protected request refused",
                    "protected service unavailable",
                }
        assert responses.calls == (0 if case in {"missing", "spoof"} else 1)
    finally:
        responses.release.set()
        store.close()


@pytest.mark.parametrize("case", ["memory", "mode", "codes", "window"])
def test_invalid_listener_profile_is_refused(tmp_path: Path, case: str) -> None:
    store = DurableMessageAuthReplayStore(
        ":memory:" if case == "memory" else tmp_path / "config.db",
        max_entries=32,
        window_seconds=float("nan") if case == "window" else 10,
    )
    try:
        with pytest.raises(ValueError):
            ProtectedWriteTransport(
                limits=LIMITS,
                current_enrollments=lambda: {},
                replay_store=store,
                sequence_floor_mode=(
                    cast(SequenceFloorMode, "strict")
                    if case == "mode"
                    else SequenceFloorMode.STRICT
                ),
                clock=lambda: NOW,
                dispatch=Responses(),
                reason_codes=cast(frozenset[str], set()) if case == "codes" else frozenset(),
            )
    finally:
        store.close()


async def test_client_disconnect_during_response_leaves_no_handler(tmp_path: Path) -> None:
    store = DurableMessageAuthReplayStore(
        tmp_path / "disconnect.db", max_entries=32, window_seconds=10
    )
    responses = Responses()
    responses.release.clear()
    transport = ProtectedWriteTransport(
        limits=LIMITS,
        current_enrollments=lambda: {"session": enrollment()},
        replay_store=store,
        sequence_floor_mode=SequenceFloorMode.STRICT,
        clock=lambda: NOW,
        dispatch=responses,
        reason_codes=frozenset(),
    )
    try:
        async with serve(transport, "127.0.0.1", 0) as server:
            try:
                port = server.sockets[0].getsockname()[1]
                async with connect(f"ws://127.0.0.1:{port}") as client:
                    await client.send(request())
                    await asyncio.wait_for(responses.entered.wait(), 2)
                    await client.close()
            finally:
                responses.release.set()
        assert not server.connections
        assert responses.calls == 1
    finally:
        responses.release.set()
        store.close()
