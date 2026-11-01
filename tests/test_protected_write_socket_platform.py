# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — native credential admission and portable socket path qualification
"""Keep unsupported native endpoints inert and portable socket paths bounded."""

from __future__ import annotations

import asyncio
import os
import socket
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import AsyncExitStack
from functools import partial
from pathlib import Path

import pytest

from protected_write_socket_examples import short_unix_socket_path as short_unix_socket_path
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.message_auth import MessageAuthKey, sign_frame
from synapse_channel.core.message_auth_durable import (
    DurableMessageAuthReplayStore,
    SequenceFloorMode,
)
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protected_write_admission_journal import ProtectedAdmissionReplayPolicy
from synapse_channel.core.protected_write_authority import (
    ProtectedWriteAuthority,
    assemble_protected_write_authority,
    serve_protected_write_authority,
)
from synapse_channel.core.protected_write_operator_connections import retain_operator_connections
from synapse_channel.core.protected_write_operator_socket import retained_listener_options
from synapse_channel.core.protected_write_preparation import ProtectedPreparationPolicy
from synapse_channel.core.protected_write_unix_ingress import ProtectedWriteUnixIngress
from test_protected_write_proposal import LIMITS


@pytest.fixture
def authority(tmp_path: Path) -> Iterator[tuple[ProtectedWriteAuthority, EventStore]]:
    """Assemble the real empty authority without optional Ed25519 test dependencies.

    No client key or effect scope is enrolled; physical recovery always refuses.
    The real HMAC signer is retained only to satisfy the authority assembly API.
    """
    policy = ProtectedAdmissionReplayPolicy(
        LIMITS, frozenset(), 10, verify_recovery=lambda _parent, _child: False
    )
    with (
        EventStore(tmp_path / "authority.db") as store,
        DurableMessageAuthReplayStore(
            tmp_path / "nonces.db", max_entries=32, window_seconds=10
        ) as replay,
    ):
        assembled = assemble_protected_write_authority(
            hub=SynapseHub(
                journal=store,
                protected_write_policies={"platform": policy},
                anti_rollback_checkpoint=False,
            ),
            sealed_keys={},
            effects=ProtectedPreparationPolicy("platform", LIMITS, {}, {}, {}, {}, {}, 1024),
            replay_policy=policy,
            writer=("EXAMPLE/writer", "incarnation"),
            operator_principals=frozenset({"EXAMPLE/operator"}),
            recovery_principals=frozenset({"EXAMPLE/recovery"}),
            clock=time.time,
            sign_response=partial(
                sign_frame,
                key=MessageAuthKey(
                    "platform-authority", b"a" * 32, frozenset({"EXAMPLE/authority"})
                ),
                nonce="platform-response",
                sequence=1,
                timestamp=time.time(),
            ),
            replay_store=replay,
            sequence_floor_mode=SequenceFloorMode.STRICT,
        )
        yield assembled, store


@pytest.mark.asyncio
async def test_actual_host_ingress_refuses_unsupported_credentials_before_resources(
    authority: tuple[ProtectedWriteAuthority, EventStore],
) -> None:
    """The actual platform admits Linux credentials or raises before threads and tasks."""
    assembled, store = authority
    threads = {thread.ident for thread in threading.enumerate()}
    tasks = asyncio.all_tasks()
    if sys.platform == "linux" and hasattr(socket, "SO_PEERCRED"):
        adapter = ProtectedWriteUnixIngress(assembled.ingress, allowed_uids=frozenset({1}))
        await adapter.aclose()
    else:
        with pytest.raises(OSError, match="Linux SO_PEERCRED"):
            ProtectedWriteUnixIngress(assembled.ingress, allowed_uids=frozenset({1}))
    assert asyncio.all_tasks() == tasks
    assert {thread.ident for thread in threading.enumerate()} == threads
    assert store.read_all() == [] and store.read_operations() == ()


@pytest.mark.asyncio
async def test_actual_host_operator_context_refuses_before_callback_or_task(
    authority: tuple[ProtectedWriteAuthority, EventStore],
) -> None:
    """Unsupported hosts cannot obtain an accept callback that would fail asynchronously."""
    assembled, store = authority
    tasks = asyncio.all_tasks()
    context = retain_operator_connections(control=assembled.operator_control, operator_uid=1)
    if sys.platform == "linux" and hasattr(socket, "SO_PEERCRED"):
        async with context as accept:
            assert callable(accept)
    else:
        with pytest.raises(OSError, match="Linux SO_PEERCRED"):
            async with AsyncExitStack() as owned:
                await owned.enter_async_context(context)
    assert asyncio.all_tasks() == tasks
    assert store.read_all() == [] and store.read_operations() == ()


@pytest.mark.asyncio
async def test_actual_host_paired_startup_refuses_before_listening(
    authority: tuple[ProtectedWriteAuthority, EventStore],
) -> None:
    """Refusal preserves caller-bound identities without listening or consuming sockets."""
    assembled, store = authority
    with (
        socket.socket(socket.AF_INET, socket.SOCK_STREAM) as operator,
        socket.socket(socket.AF_INET, socket.SOCK_STREAM) as requests,
    ):
        operator.bind(("127.0.0.1", 0))
        requests.bind(("127.0.0.1", 0))
        original_names = (operator.getsockname(), requests.getsockname())
        context = serve_protected_write_authority(
            assembled,
            operator_listener=operator,
            request_listener=requests,
            operator_uid=0,
            request_uids=frozenset({1}),
        )
        if sys.platform == "linux" and hasattr(socket, "SO_PEERCRED"):
            with pytest.raises(ValueError, match="separate dedicated UIDs"):
                async with AsyncExitStack() as owned:
                    await owned.enter_async_context(context)
        else:
            with pytest.raises(OSError, match="Linux SO_PEERCRED"):
                async with AsyncExitStack() as owned:
                    await owned.enter_async_context(context)
        for listener, original_name in zip((operator, requests), original_names, strict=True):
            assert listener.fileno() >= 0
            assert listener.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN) == 0
            assert listener.getsockname() == original_name
    assert store.read_all() == [] and store.read_operations() == ()


@pytest.mark.skipif(os.name != "posix", reason="asyncio Unix stream listeners require POSIX")
@pytest.mark.asyncio
async def test_portable_retained_listener_uses_short_relative_path(
    short_unix_socket_path: Path,
) -> None:
    """A real stream survives a cwd longer than macOS sun_path without alternate storage."""
    assert len(os.fsencode(Path.cwd())) > 104
    assert len(os.fsencode(short_unix_socket_path)) < 104
    assert not short_unix_socket_path.is_absolute()

    async def echo(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Complete one real stream exchange and close it before the server exits."""
        try:
            writer.write(await reader.readexactly(4))
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        listener.bind(str(short_unix_socket_path))
        listener.listen()
        before = short_unix_socket_path.stat()
        server = await asyncio.start_unix_server(echo, sock=listener, **retained_listener_options())
        async with server:
            reader, writer = await asyncio.open_unix_connection(short_unix_socket_path)
            try:
                writer.write(b"ping")
                await writer.drain()
                assert await asyncio.wait_for(reader.read(), 2) == b"ping"
            finally:
                writer.close()
                await writer.wait_closed()
        after = short_unix_socket_path.stat()
        assert (before.st_dev, before.st_ino) == (after.st_dev, after.st_ino)
