# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — real native protected control server
"""Exercise native peer, correlation, capacity and shutdown admission."""

from __future__ import annotations

import asyncio
import json
import socket
import sqlite3
import struct
from contextlib import AsyncExitStack
from dataclasses import replace
from pathlib import Path

import pytest

from protected_write_control_examples import (
    authority_control,
    control_authority,
    control_policy,
    native_peer,
)
from protected_write_socket_examples import private_unix_directory as private_unix_directory
from protected_write_socket_examples import requires_linux_peer_credentials
from synapse_channel.core.protected_write_control_client import open_protected_write_control
from synapse_channel.core.protected_write_control_server import ProtectedWriteControlServer
from synapse_channel.core.protected_write_control_wire import (
    ProtectedWriteControlBinding,
    decode_protected_write_control_hello,
    read_protected_write_control_frame,
)


@pytest.mark.parametrize("field", ["pid", "uid", "gid"])
@requires_linux_peer_credentials
async def test_native_control_refuses_wrong_enrolled_peer_before_greeting(
    tmp_path: Path, private_unix_directory: Path, field: str
) -> None:
    peer = native_peer()
    if field == "pid":
        peer = replace(peer, pid=peer.pid + 1)
    elif field == "uid":
        peer = replace(peer, uid=peer.uid + 1)
    else:
        peer = replace(peer, gid=peer.gid + 1)
    async with control_authority(tmp_path) as authority:
        before = authority.store.read_all()
        async with authority_control(authority.ingress, private_unix_directory, peer=peer) as (
            path,
            _,
        ):
            async with AsyncExitStack() as stack:
                with pytest.raises(asyncio.IncompleteReadError):
                    await stack.enter_async_context(
                        open_protected_write_control(
                            path, authority_peer=native_peer(), policy=control_policy()
                        )
                    )
        assert authority.store.read_all() == before


@pytest.mark.parametrize(
    "field",
    [
        "connection_id",
        "authority_continuity",
        "frontend_incarnation",
        "sequence",
        "schema",
        "kind",
        "reply_target",
        "duplicate",
    ],
)
@requires_linux_peer_credentials
async def test_control_server_refuses_cross_connection_and_unbound_commands_before_actor(
    tmp_path: Path, private_unix_directory: Path, field: str
) -> None:
    policy = control_policy()
    async with control_authority(tmp_path) as authority:
        async with authority_control(authority.ingress, private_unix_directory) as (path, _):
            first, first_writer = await asyncio.open_unix_connection(path)
            second, second_writer = await asyncio.open_unix_connection(path)
            try:
                first_binding = decode_protected_write_control_hello(
                    await read_protected_write_control_frame(first, policy), policy
                )
                second_binding = decode_protected_write_control_hello(
                    await read_protected_write_control_frame(second, policy), policy
                )
                assert first_binding.connection_id != second_binding.connection_id
                raw = first_binding.encode("request", 1, authority.signed(1))[4:]
                frame = json.loads(raw)
                if field == "connection_id":
                    frame[field] = second_binding.connection_id
                elif field == "sequence":
                    frame[field] = 2
                elif field == "reply_target":
                    frame[field] = second_binding.connection_id
                else:
                    frame[field] = "foreign"
                raw = json.dumps(frame).encode()
                if field == "duplicate":
                    frame.pop("duplicate")
                    raw = b'{"sequence":1,' + json.dumps(frame).encode()[1:]
                before = authority.store.read_all()
                operations = authority.store.read_operations()
                first_writer.write(struct.pack("!I", len(raw)) + raw)
                await first_writer.drain()
                refusal = await read_protected_write_control_frame(first, policy)
                assert first_binding.decode(refusal, kind="refused", sequence=1) == ""
                assert await first.read() == b""
                assert authority.store.read_all() == before
                assert authority.store.read_operations() == operations
                second_writer.write(second_binding.encode("request", 1, authority.signed(2)))
                await second_writer.drain()
                result = await read_protected_write_control_frame(second, policy)
                assert (
                    json.loads(second_binding.decode(result, kind="result", sequence=1))[
                        "request_id"
                    ]
                    == "control-status-2"
                )
            finally:
                first_writer.close()
                second_writer.close()
                await asyncio.gather(first_writer.wait_closed(), second_writer.wait_closed())


@pytest.mark.parametrize("cap", ["connections", "bytes"])
@requires_linux_peer_credentials
async def test_control_server_reserves_connection_and_aggregate_bytes_before_accept(
    tmp_path: Path, private_unix_directory: Path, cap: str
) -> None:
    policy = control_policy()
    policy = (
        replace(policy, max_connections=1)
        if cap == "connections"
        else replace(policy, max_inflight_bytes=policy.connection_bytes)
    )
    async with control_authority(tmp_path) as authority:
        async with authority_control(authority.ingress, private_unix_directory, policy=policy) as (
            path,
            handler,
        ):
            async with open_protected_write_control(
                path, authority_peer=native_peer(), policy=policy
            ):
                with pytest.raises(asyncio.IncompleteReadError):
                    async with AsyncExitStack() as stack:
                        await stack.enter_async_context(
                            open_protected_write_control(
                                path, authority_peer=native_peer(), policy=policy
                            )
                        )
                await handler.aclose()
                await handler.aclose()
                with pytest.raises(asyncio.IncompleteReadError):
                    async with AsyncExitStack() as stack:
                        await stack.enter_async_context(
                            open_protected_write_control(
                                path, authority_peer=native_peer(), policy=policy
                            )
                        )
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as tcp:
                with pytest.raises(ValueError):
                    async with AsyncExitStack() as stack:
                        await stack.enter_async_context(handler.serve(tcp))


@requires_linux_peer_credentials
async def test_control_server_closes_partial_frame_on_deadline_without_effect(
    tmp_path: Path, private_unix_directory: Path
) -> None:
    policy = replace(control_policy(), timeout_seconds=0.1)
    async with control_authority(tmp_path) as authority:
        before = authority.store.read_all()
        async with authority_control(authority.ingress, private_unix_directory, policy=policy) as (
            path,
            _,
        ):
            reader, writer = await asyncio.open_unix_connection(path)
            try:
                await read_protected_write_control_frame(reader, policy)
                writer.write(struct.pack("!I", 50) + b"{")
                await writer.drain()
                assert await asyncio.wait_for(reader.read(), 1) == b""
            finally:
                writer.close()
                await writer.wait_closed()
        assert authority.store.read_all() == before


@requires_linux_peer_credentials
async def test_control_shutdown_drains_an_actually_blocked_sqlite_mutation_before_return(
    tmp_path: Path, private_unix_directory: Path
) -> None:
    """A native write lock, not a substituted journal, delays the real commit."""
    async with control_authority(tmp_path) as authority:
        before_operations = len(authority.store.read_operations())
        async with authority_control(authority.ingress, private_unix_directory) as (path, handler):
            async with open_protected_write_control(
                path, authority_peer=native_peer(), policy=control_policy()
            ) as client:
                blocker = sqlite3.connect(tmp_path / "control-authority.db")
                blocker.execute("BEGIN IMMEDIATE")
                request = asyncio.create_task(client.exchange(authority.revoke(1)))
                try:
                    with pytest.raises(TimeoutError):
                        await asyncio.wait_for(asyncio.shield(request), 0.1)
                    closing = asyncio.create_task(handler.aclose())
                    try:
                        with pytest.raises(TimeoutError):
                            await asyncio.wait_for(asyncio.shield(closing), 0.1)
                        current = next(
                            iter(authority.hub.state.protected_write_reservations.values())
                        )
                        assert (
                            json.loads(current.result_bytes)["body"]["revocation_phase"] == "open"
                        )
                    finally:
                        blocker.close()
                        await asyncio.wait_for(closing, 3)
                finally:
                    blocker.close()
                    await asyncio.gather(request, return_exceptions=True)
                assert request.done()
                assert isinstance(request.exception(), asyncio.IncompleteReadError)
                assert len(authority.store.read_operations()) == before_operations + 1
                reservation = next(iter(authority.hub.state.protected_write_reservations.values()))
                assert (
                    json.loads(reservation.result_bytes)["body"]["revocation_phase"] == "requested"
                )


@requires_linux_peer_credentials
async def test_control_listener_refuses_preexisting_listen_credentials_and_tcp(
    tmp_path: Path, private_unix_directory: Path
) -> None:
    async with control_authority(tmp_path) as authority:
        handler = ProtectedWriteControlServer(
            authority.ingress, frontend_peer=native_peer(), policy=control_policy()
        )
        for family in (socket.AF_UNIX, socket.AF_INET):
            with socket.socket(family, socket.SOCK_STREAM) as listener:
                if family == socket.AF_UNIX:
                    listener.bind(str(private_unix_directory / "already-listening.sock"))
                else:
                    listener.bind(("127.0.0.1", 0))
                listener.listen(1)
                async with AsyncExitStack() as stack:
                    with pytest.raises(ValueError, match="enrolled Unix control listener"):
                        await stack.enter_async_context(handler.serve(listener))
                assert listener.fileno() >= 0


@pytest.mark.parametrize("budget", ["wire", "string", "metadata"])
@requires_linux_peer_credentials
async def test_control_server_refuses_policy_that_cannot_carry_authority_limits(
    tmp_path: Path, budget: str
) -> None:
    async with control_authority(tmp_path) as authority:
        policy = control_policy()
        if budget == "wire":
            limits = replace(policy.json_limits, max_wire_bytes=authority.ingress.max_wire_bytes)
        elif budget == "string":
            limits = replace(
                policy.json_limits, max_string_bytes=authority.ingress.max_wire_bytes - 1
            )
        else:
            limits = replace(policy.json_limits, max_nodes=14)
        before = authority.store.read_all()
        with pytest.raises(ValueError):
            ProtectedWriteControlServer(
                authority.ingress,
                frontend_peer=native_peer(),
                policy=replace(policy, json_limits=limits),
            )
        assert authority.store.read_all() == before


@requires_linux_peer_credentials
async def test_control_server_enforces_exact_escaped_ingress_capacity_before_serving(
    tmp_path: Path, private_unix_directory: Path
) -> None:
    policy = control_policy()
    async with control_authority(tmp_path) as authority:
        binding = ProtectedWriteControlBinding(policy, "0" * 64)
        overhead = len(binding.encode("request", (1 << 53) - 1, "")) - 4
        minimum = overhead + 6 * authority.ingress.max_wire_bytes
        limits = replace(
            policy.json_limits,
            max_wire_bytes=minimum,
            max_string_bytes=authority.ingress.max_wire_bytes,
            max_nodes=15,
            max_depth=1,
        )
        before = authority.store.read_all()
        with pytest.raises(ValueError, match="cannot carry authority"):
            ProtectedWriteControlServer(
                authority.ingress,
                frontend_peer=native_peer(),
                policy=replace(policy, json_limits=replace(limits, max_wire_bytes=minimum - 1)),
            )
        assert authority.store.read_all() == before
        exact = replace(policy, json_limits=limits)
        async with authority_control(authority.ingress, private_unix_directory, policy=exact) as (
            path,
            _,
        ):
            async with open_protected_write_control(
                path, authority_peer=native_peer(), policy=exact
            ) as client:
                result = json.loads(await client.exchange(authority.signed(1)))
                assert result["body"]["disposition"] == "known"
