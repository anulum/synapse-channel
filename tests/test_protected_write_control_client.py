# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — real protected frontend control client
"""Exercise actual peer checks, connection epochs and terminal client closure."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import struct
from collections.abc import Callable
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
from synapse_channel.core.protected_write_control_client import (
    ProtectedWriteControlClient,
    open_protected_write_control,
)
from synapse_channel.core.protected_write_control_wire import (
    decode_protected_write_control_hello,
    read_protected_write_control_frame,
)
from synapse_channel.core.protected_write_operator_connections import retain_operator_connections


@requires_linux_peer_credentials
async def test_control_client_checks_live_authority_and_retains_no_retry_after_failure(
    tmp_path: Path, private_unix_directory: Path
) -> None:
    policy = control_policy()
    async with control_authority(tmp_path) as authority:
        async with authority_control(authority.ingress, private_unix_directory) as (path, _):
            async with AsyncExitStack() as stack:
                with pytest.raises(ValueError, match="peer mismatch"):
                    await stack.enter_async_context(
                        open_protected_write_control(
                            path,
                            authority_peer=replace(native_peer(), pid=native_peer().pid + 1),
                            policy=policy,
                        )
                    )
                with pytest.raises(ValueError, match="envelope refused"):
                    await stack.enter_async_context(
                        open_protected_write_control(
                            path,
                            authority_peer=native_peer(),
                            policy=replace(policy, authority_continuity="old"),
                        )
                    )
                with pytest.raises(FileNotFoundError):
                    await stack.enter_async_context(
                        open_protected_write_control(
                            path.with_name("absent.sock"),
                            authority_peer=native_peer(),
                            policy=policy,
                        )
                    )
            async with open_protected_write_control(
                path, authority_peer=native_peer(), policy=policy
            ) as client:
                assert (
                    json.loads(await client.exchange(authority.signed(1).encode()))["request_id"]
                    == "control-status-1"
                )
                with pytest.raises(ValueError):
                    await client.exchange('{"type":"claim","sender":"forged"}')
                with pytest.raises(ValueError, match="unavailable"):
                    await client.exchange(authority.signed(2))


class CloseAfterBodyReader(asyncio.StreamReader):
    """Schedule real client shutdown when its native response body has arrived."""

    after_body: Callable[[], None] | None = None

    async def readexactly(self, n: int) -> bytes:
        value = await super().readexactly(n)
        if n > 4 and self.after_body is not None:
            self.after_body()
            self.after_body = None
        return value


@requires_linux_peer_credentials
async def test_control_client_close_wins_after_native_result_read_before_exchange_return(
    tmp_path: Path, private_unix_directory: Path
) -> None:
    """Only shutdown timing is instrumented; bytes and proofs come from the real actor."""
    policy = control_policy()
    async with control_authority(tmp_path) as authority:
        async with authority_control(authority.ingress, private_unix_directory) as (path, _):
            loop = asyncio.get_running_loop()
            reader = CloseAfterBodyReader(limit=policy.json_limits.max_wire_bytes)
            protocol = asyncio.StreamReaderProtocol(reader)
            transport, _ = await loop.create_unix_connection(lambda: protocol, path=str(path))
            writer = asyncio.StreamWriter(transport, protocol, reader, loop)
            native_peer().verify(writer)
            binding = decode_protected_write_control_hello(
                await read_protected_write_control_frame(reader, policy), policy
            )
            client = ProtectedWriteControlClient(reader, writer, binding)
            closing: list[asyncio.Task[None]] = []

            def close_after_body() -> None:
                closing.append(asyncio.create_task(client.aclose()))

            reader.after_body = close_after_body
            try:
                with pytest.raises(RuntimeError, match="exchange closed"):
                    await client.exchange(authority.signed(1))
                assert len(closing) == 1
            finally:
                await client.aclose()
                await asyncio.gather(*closing)


@pytest.mark.parametrize("other_service", ["control", "operator"])
@requires_linux_peer_credentials
async def test_misdirected_native_reply_after_real_commit_is_unknown_not_refused(
    tmp_path: Path, private_unix_directory: Path, other_service: str
) -> None:
    """Cross two real native readers; never fabricate a response or committed row."""
    policy = control_policy()
    async with control_authority(tmp_path) as authority:
        async with authority_control(authority.ingress, private_unix_directory) as (path, _):
            async with AsyncExitStack() as stack:
                other_path = path
                if other_service == "operator":
                    accept = await stack.enter_async_context(
                        retain_operator_connections(
                            control=authority.control,
                            operator_uid=os.geteuid(),
                        )
                    )
                    other_path = private_unix_directory / "operator.sock"
                    server = await asyncio.start_unix_server(accept, path=other_path)
                    await stack.enter_async_context(server)
                streams = []
                for endpoint in (path, other_path):
                    reader, writer = await asyncio.open_unix_connection(endpoint)
                    stack.push_async_callback(writer.wait_closed)
                    stack.callback(writer.close)
                    native_peer().verify(writer)
                    streams.append((reader, writer))
                (first_reader, first_writer), (other_reader, other_writer) = streams
                binding = decode_protected_write_control_hello(
                    await read_protected_write_control_frame(first_reader, policy), policy
                )
                if other_service == "control":
                    other_binding = decode_protected_write_control_hello(
                        await read_protected_write_control_frame(other_reader, policy), policy
                    )
                    other_request = other_binding.encode("request", 1, authority.signed(1))
                else:
                    body = b'{"op":"invalid"}'
                    other_request = struct.pack("!I", len(body)) + body
                client = ProtectedWriteControlClient(other_reader, first_writer, binding)
                before = len(authority.store.read_operations())
                exchange = asyncio.create_task(client.exchange(authority.revoke(1)))
                try:
                    committed = binding.decode(
                        await asyncio.wait_for(
                            read_protected_write_control_frame(first_reader, policy), 2
                        ),
                        kind="result",
                        sequence=1,
                    )
                    assert json.loads(committed)["body"]["revocation_phase"] == "requested"
                    assert len(authority.store.read_operations()) == before + 1
                    other_writer.write(other_request)
                    await other_writer.drain()
                    with pytest.raises(RuntimeError, match="response unavailable"):
                        await asyncio.wait_for(exchange, 2)
                    assert len(authority.store.read_operations()) == before + 1
                finally:
                    await client.aclose()
                    await asyncio.gather(exchange, return_exceptions=True)
                await client.aclose()
                await client.aclose()


@requires_linux_peer_credentials
@pytest.mark.parametrize("action", ["cancel", "concurrent", "close", "timeout"])
async def test_control_client_interruption_has_no_automatic_replay_of_a_durable_operation(
    tmp_path: Path, private_unix_directory: Path, action: str
) -> None:
    """An actual SQLite writer lock holds the mutation beyond client interruption."""
    client_policy = control_policy()
    if action == "timeout":
        client_policy = replace(client_policy, timeout_seconds=0.1)
    async with control_authority(tmp_path) as authority:
        before = len(authority.store.read_operations())
        async with authority_control(authority.ingress, private_unix_directory) as (path, handler):
            async with open_protected_write_control(
                path, authority_peer=native_peer(), policy=client_policy
            ) as client:
                blocker = sqlite3.connect(tmp_path / "control-authority.db")
                blocker.execute("BEGIN IMMEDIATE")
                request = asyncio.create_task(client.exchange(authority.revoke(1)))
                try:
                    if action == "timeout":
                        with pytest.raises(TimeoutError):
                            await request
                    else:
                        with pytest.raises(TimeoutError):
                            await asyncio.wait_for(asyncio.shield(request), 0.1)
                        if action == "cancel":
                            request.cancel()
                        elif action == "concurrent":
                            with pytest.raises(ValueError, match="unavailable"):
                                await client.exchange(authority.signed(1))
                        else:
                            await client.aclose()
                        with pytest.raises(asyncio.CancelledError):
                            await request
                    current = next(iter(authority.hub.state.protected_write_reservations.values()))
                    assert json.loads(current.result_bytes)["body"]["revocation_phase"] == "open"
                finally:
                    blocker.rollback()
                    blocker.close()
                    await handler.aclose()
                    await asyncio.gather(request, return_exceptions=True)
                assert len(authority.store.read_operations()) == before + 1
                reservation = next(iter(authority.hub.state.protected_write_reservations.values()))
                assert (
                    json.loads(reservation.result_bytes)["body"]["revocation_phase"] == "requested"
                )
                with pytest.raises(ValueError, match="unavailable"):
                    await client.exchange(authority.signed(2))
