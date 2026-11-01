# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real Unix-socket operator control tests
"""Exercise real peer-authenticated commands, uncertain outcomes and listener custody."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import struct
import sys
from contextlib import AsyncExitStack
from pathlib import Path

import pytest

from protected_write_socket_examples import requires_linux_peer_credentials
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protected_write_enrollment_control import ProtectedEnrollmentControl
from synapse_channel.core.protected_write_enrollment_journal import enrollment_issue_payload
from synapse_channel.core.protected_write_operator_socket import (
    handle_operator_connection,
    retained_listener_options,
    serve_operator_listener,
)
from test_protected_write_enrollment_journal import _enrollment


@pytest.fixture
def private_socket_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Keep real Unix socket spellings bounded inside the allocated private directory."""
    monkeypatch.chdir(tmp_path)
    return Path("operator.sock")


async def _exchange(path: Path, command: bytes) -> bytes:
    """Exchange one actual length-prefixed operator command and close its stream."""
    reader, writer = await asyncio.open_unix_connection(path)
    writer.write(struct.pack("!I", len(command)) + command)
    await writer.drain()
    response = await reader.readline()
    writer.close()
    await writer.wait_closed()
    return response


@pytest.mark.asyncio
@requires_linux_peer_credentials
async def test_real_unix_peer_issue_revoke_and_restart(
    tmp_path: Path, private_socket_path: Path
) -> None:
    """Real peer-authenticated issue/revoke commands survive durable hub replay."""
    path = private_socket_path
    enrollment = _enrollment()
    metadata = {
        key: value
        for key, value in enrollment_issue_payload(enrollment).items()
        if key not in {"schema_version", "key_fingerprint"}
    }
    with EventStore(tmp_path / "operator.db") as store:
        hub = SynapseHub(journal=store, protected_write_policies={}, anti_rollback_checkpoint=False)
        control = ProtectedEnrollmentControl(
            hub=hub,
            sealed_keys={enrollment.session_id: enrollment.key},
            clock=lambda: 1_900_000_000.0,
        )
        # This exercises the socket component with the real current peer UID.
        # Deployment still requires distinct operator/Core UIDs in serve_operator_listener.
        server = await asyncio.start_unix_server(
            lambda reader, writer: handle_operator_connection(
                reader, writer, control=control, operator_uid=os.getuid()
            ),
            path=path,
        )
        async with server:
            issued = json.dumps({"op": "issue", "metadata": metadata}).encode()
            assert await _exchange(path, issued) == b'{"ok":true}\n'
            assert control.current() == {enrollment.session_id: enrollment}
            assert await _exchange(path, b'{"op":"revoke","session_id":"writer-1"}') == (
                b'{"ok":true}\n'
            )
            assert control.current() == {}
    with EventStore(tmp_path / "operator.db") as reopened:
        restored = SynapseHub(
            journal=reopened, protected_write_policies={}, anti_rollback_checkpoint=False
        )
        assert restored.state.protected_enrollments[enrollment.session_id].revoked


@pytest.mark.asyncio
@requires_linux_peer_credentials
async def test_real_unix_peer_refusals_preserve_event_store(
    tmp_path: Path, private_socket_path: Path
) -> None:
    """Malformed frames and commands leave the actual event store unchanged."""
    path = private_socket_path
    with EventStore(tmp_path / "refusals.db") as store:
        hub = SynapseHub(journal=store, protected_write_policies={}, anti_rollback_checkpoint=False)
        control = ProtectedEnrollmentControl(hub=hub, sealed_keys={}, clock=lambda: 1.0)
        server = await asyncio.start_unix_server(
            lambda reader, writer: handle_operator_connection(
                reader, writer, control=control, operator_uid=os.getuid()
            ),
            path=path,
        )
        async with server:
            for command in (
                b"",
                b'{"op":"issue","op":"revoke"}',
                b'{"op":"revoke","session_id":"missing","extra":true}',
                b'{"op":"unknown"}',
                b'{"op":"issue","metadata":null}',
                b"[]",
                b"{",
                b"\xff",
            ):
                assert await _exchange(path, command) == b'{"ok":false}\n'
            reader, writer = await asyncio.open_unix_connection(path)
            writer.write(struct.pack("!I", 16_385))
            await writer.drain()
            assert await reader.readline() == b'{"ok":false}\n'
            writer.close()
            await writer.wait_closed()

            reader, writer = await asyncio.open_unix_connection(path)
            writer.write(struct.pack("!I", 10) + b"{}")
            await writer.drain()
            writer.write_eof()
            assert await reader.readline() == b'{"ok":false}\n'
            writer.close()
            await writer.wait_closed()
            assert list(store.iter_events()) == []


@pytest.mark.asyncio
@requires_linux_peer_credentials
async def test_nested_operator_json_refuses_without_an_unhandled_parser_error(
    tmp_path: Path, private_socket_path: Path
) -> None:
    """Deep in-bound JSON refuses without escaping parser errors or mutating the journal."""
    depth = 8000
    command = b"[" * depth + b"0" + b"]" * depth
    assert len(command) <= 16384
    with EventStore(tmp_path / "nested.db") as store:
        hub = SynapseHub(journal=store, protected_write_policies={}, anti_rollback_checkpoint=False)
        control = ProtectedEnrollmentControl(hub=hub, sealed_keys={}, clock=lambda: 1.0)
        tasks: list[asyncio.Task[None]] = []

        def accepted(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            """Retain the actual public handler task so an escaping exception fails the test."""
            tasks.append(
                asyncio.create_task(
                    handle_operator_connection(
                        reader, writer, control=control, operator_uid=os.getuid()
                    )
                )
            )

        server = await asyncio.start_unix_server(accepted, path=private_socket_path)
        async with server:
            assert await _exchange(private_socket_path, command) == b'{"ok":false}\n'
            await asyncio.wait_for(asyncio.gather(*tasks), 2)
        assert list(store.iter_events()) == []


@pytest.mark.parametrize(
    ("command", "error_message"),
    [
        (
            b'{"op":"revoke","session_id":"missing"}',
            "protected enrollment revoke has no predecessor",
        ),
        (
            b'{"op":"issue","metadata":{}}',
            "operator enrollment metadata fields mismatch",
        ),
    ],
)
@pytest.mark.asyncio
@requires_linux_peer_credentials
async def test_mutation_stage_errors_close_without_a_refusal(
    tmp_path: Path, private_socket_path: Path, command: bytes, error_message: str
) -> None:
    """An actual control-path exception gives EOF rather than a fabricated negative receipt."""
    with EventStore(tmp_path / "uncertain.db") as store:
        hub = SynapseHub(journal=store, protected_write_policies={}, anti_rollback_checkpoint=False)
        control = ProtectedEnrollmentControl(hub=hub, sealed_keys={}, clock=lambda: 1.0)
        errors: list[str] = []
        finished = asyncio.Event()

        async def accepted(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            """Retain the real public handler failure without an unobserved server task."""
            try:
                await handle_operator_connection(
                    reader, writer, control=control, operator_uid=os.getuid()
                )
            except ValueError as error:
                errors.append(str(error))
            finally:
                finished.set()

        server = await asyncio.start_unix_server(accepted, path=private_socket_path)
        async with server:
            assert await _exchange(private_socket_path, command) == b""
            await asyncio.wait_for(finished.wait(), 2)
        assert errors == [error_message]
        assert list(store.iter_events()) == []


@pytest.mark.asyncio
async def test_tcp_peer_cannot_use_operator_handler(tmp_path: Path) -> None:
    """A TCP connection cannot supply the Unix credentials required by operator control."""
    with EventStore(tmp_path / "tcp.db") as store:
        hub = SynapseHub(journal=store, protected_write_policies={}, anti_rollback_checkpoint=False)
        control = ProtectedEnrollmentControl(hub=hub, sealed_keys={}, clock=lambda: 1.0)
        failures: list[OSError] = []

        async def accepted(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            """Retain the actual unsupported-host error from the public connection handler."""
            try:
                await handle_operator_connection(reader, writer, control=control, operator_uid=1)
            except OSError as error:
                failures.append(error)

        server = await asyncio.start_server(accepted, "127.0.0.1", 0)
        async with server:
            address = server.sockets[0].getsockname()
            reader, writer = await asyncio.open_connection(address[0], address[1])
            response = await asyncio.wait_for(reader.readline(), 2)
            if sys.platform == "linux" and hasattr(socket, "SO_PEERCRED"):
                assert response == b'{"ok":false}\n'
                assert failures == []
            else:
                assert response == b""
                assert len(failures) == 1 and "Linux SO_PEERCRED" in str(failures[0])
            writer.close()
            await writer.wait_closed()
            assert list(store.iter_events()) == []


@pytest.mark.asyncio
@requires_linux_peer_credentials
async def test_foreign_uid_is_refused_before_command_read(
    tmp_path: Path, private_socket_path: Path
) -> None:
    """The real current peer cannot act as a separately enrolled root operator."""
    path = private_socket_path
    with EventStore(tmp_path / "foreign.db") as store:
        hub = SynapseHub(journal=store, protected_write_policies={}, anti_rollback_checkpoint=False)
        control = ProtectedEnrollmentControl(hub=hub, sealed_keys={}, clock=lambda: 1.0)
        server = await asyncio.start_unix_server(
            lambda reader, writer: handle_operator_connection(
                reader, writer, control=control, operator_uid=0
            ),
            path=path,
        )
        async with server:
            assert await _exchange(path, b'{"op":"revoke","session_id":"x"}') == (b'{"ok":false}\n')
            assert list(store.iter_events()) == []


@pytest.mark.asyncio
@requires_linux_peer_credentials
async def test_prebound_listener_keeps_its_supervisor_owned_path_after_close(
    tmp_path: Path, private_socket_path: Path
) -> None:
    """Serve a real peer and close the listener without removing its retained pathname.

    This checks the public runtime options with an actual same-UID component
    socket. It proves no dedicated deployment identity or root-owned boundary.
    """
    path = private_socket_path
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        listener.bind(str(path))
        listener.listen()
        before = path.lstat()
        with EventStore(tmp_path / "retained-listener.db") as store:
            hub = SynapseHub(journal=store, anti_rollback_checkpoint=False)
            control = ProtectedEnrollmentControl(hub=hub, sealed_keys={}, clock=lambda: 1.0)
            server = await asyncio.start_unix_server(
                lambda reader, writer: handle_operator_connection(
                    reader, writer, control=control, operator_uid=os.getuid()
                ),
                sock=listener,
                start_serving=False,
                **retained_listener_options(),
            )
            async with server:
                await server.start_serving()
                assert await _exchange(path, b"{}") == b'{"ok":false}\n'
                assert store.read_all() == []
        assert listener.fileno() == -1
        after = path.lstat()
        assert (after.st_dev, after.st_ino) == (before.st_dev, before.st_ino)
    finally:
        listener.close()


@pytest.mark.asyncio
@requires_linux_peer_credentials
async def test_deployment_listener_refuses_shared_uid(
    tmp_path: Path, private_socket_path: Path
) -> None:
    """The strict serving entry point refuses shared/root roles before opening service."""
    path = private_socket_path
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        listener.bind(str(path))
        listener.listen()
        with EventStore(tmp_path / "deployment.db") as store:
            hub = SynapseHub(
                journal=store, protected_write_policies={}, anti_rollback_checkpoint=False
            )
            control = ProtectedEnrollmentControl(hub=hub, sealed_keys={}, clock=lambda: 1.0)
            async with AsyncExitStack() as refused:
                with pytest.raises(ValueError, match="separate dedicated UIDs"):
                    await refused.enter_async_context(
                        serve_operator_listener(listener, control=control, operator_uid=0)
                    )
    finally:
        listener.close()
