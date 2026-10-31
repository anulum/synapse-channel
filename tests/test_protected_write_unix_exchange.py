# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — bounded Unix authority exchange
"""Exercise enrolled path and real peer credentials over a Unix WebSocket."""

from __future__ import annotations

import asyncio
import os
import select
import socket
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from websockets.asyncio.server import ServerConnection, unix_serve
from websockets.exceptions import ConnectionClosed

from protected_write_socket_examples import private_unix_directory as private_unix_directory
from synapse_channel.core.protected_write_unix_exchange import (
    ProtectedWriteUnixExchange,
    require_native_unix_credentials,
)


@pytest.mark.parametrize("parent_mode,socket_mode", [(0o700, 0o700), (0o710, 0o660)])
@pytest.mark.asyncio
async def test_unix_exchange_checks_real_peer_path_and_wire_bounds(
    private_unix_directory: Path, parent_mode: int, socket_mode: int
) -> None:
    """Exercise native credential refusal or actual same-UID path, peer and wire bounds."""
    if sys.platform != "linux" or not hasattr(socket, "SO_PEERCRED"):
        with pytest.raises(OSError, match="Linux SO_PEERCRED"):
            ProtectedWriteUnixExchange(
                private_unix_directory / "authority.sock",
                parent_identity=(0, 1),
                parent_uid=0,
                parent_gid=0,
                parent_mode=0o700,
                socket_identity=(0, 2),
                socket_uid=0,
                socket_gid=0,
                socket_mode=0o700,
                peer_uid=0,
                max_wire_bytes=64,
                timeout_seconds=2.0,
            )
        return

    async def handler(connection: ServerConnection) -> None:
        """Echo real binary requests or deliberately return text through the actual server."""
        request = await connection.recv()
        await connection.send("text" if request == b"text" else request)

    endpoint = private_unix_directory
    endpoint.chmod(parent_mode)
    path = endpoint / "authority.sock"
    async with unix_serve(handler, path=path, max_size=64):
        path.chmod(socket_mode)
        parent = endpoint.stat()
        listener = path.lstat()

        def enrolled(
            *, peer_uid: int, max_wire_bytes: int = 64, parent_gid: int | None = None
        ) -> ProtectedWriteUnixExchange:
            """Enroll the actual retained endpoint metadata with the selected peer or wire
            limit.
            """
            return ProtectedWriteUnixExchange(
                path,
                parent_identity=(parent.st_dev, parent.st_ino),
                parent_uid=os.geteuid(),
                parent_gid=parent.st_gid if parent_gid is None else parent_gid,
                parent_mode=parent.st_mode & 0o777,
                socket_identity=(listener.st_dev, listener.st_ino),
                socket_uid=os.geteuid(),
                socket_gid=listener.st_gid,
                socket_mode=listener.st_mode & 0o777,
                peer_uid=peer_uid,
                max_wire_bytes=max_wire_bytes,
                timeout_seconds=2.0,
            )

        exchange = enrolled(peer_uid=os.geteuid())
        assert await asyncio.to_thread(exchange, b"hello") == b"hello"
        with pytest.raises(ValueError, match="path identity or ownership"):
            enrolled(peer_uid=os.geteuid(), parent_gid=parent.st_gid + 1)
        with pytest.raises(ValueError, match="request size"):
            await asyncio.to_thread(exchange, b"")
        with pytest.raises(ValueError, match="response size"):
            await asyncio.to_thread(exchange, b"text")
        with pytest.raises(ValueError, match="socket enrollment"):
            enrolled(peer_uid=os.geteuid(), max_wire_bytes=0)
        wrong_peer = enrolled(peer_uid=os.geteuid() + 1)
        with pytest.raises(ValueError, match="peer UID differs"):
            await asyncio.to_thread(wrong_peer, b"hello")
        path.chmod(0o755)
        with pytest.raises(ValueError, match="path identity or ownership"):
            await asyncio.to_thread(exchange, b"hello")
        path.chmod(socket_mode)


@pytest.mark.parametrize(
    "parent_mode,socket_mode",
    [
        (0o600, 0o660),
        (0o720, 0o660),
        (0o702, 0o660),
        (0o710, 0o666),
        (0o710, 0o400),
        (0o710, True),
    ],
)
def test_unix_exchange_refuses_unsafe_permission_enrollment_before_path_access(
    tmp_path: Path, parent_mode: int, socket_mode: int
) -> None:
    """Unsafe ancestor/socket masks cannot become a usable endpoint enrollment."""
    path = tmp_path / "never-created.sock"
    with pytest.raises(ValueError, match="permission enrollment"):
        ProtectedWriteUnixExchange(
            path,
            parent_identity=(0, 1),
            parent_uid=0,
            parent_gid=0,
            parent_mode=parent_mode,
            socket_identity=(0, 2),
            socket_uid=0,
            socket_gid=0,
            socket_mode=socket_mode,
            peer_uid=0,
            max_wire_bytes=64,
            timeout_seconds=2.0,
        )
    assert not path.exists()


@pytest.mark.parametrize(
    "parent_identity,socket_identity,parent_uid,socket_gid",
    [
        ((-1, 1), (0, 2), 0, 0),
        ((0, 1), (0, -2), 0, 0),
        ((0, 1), (0, 2), -1, 0),
        ((0, 1), (0, 2), 0, -1),
    ],
)
def test_invalid_endpoint_identity_refuses_before_filesystem_access(
    tmp_path: Path,
    parent_identity: tuple[int, int],
    socket_identity: tuple[int, int],
    parent_uid: int,
    socket_gid: int,
) -> None:
    """Malformed enrollment cannot reach an absent endpoint or create any filesystem object."""
    endpoint = tmp_path / "absent.sock"
    with pytest.raises(ValueError, match="socket enrollment"):
        ProtectedWriteUnixExchange(
            endpoint,
            parent_identity=parent_identity,
            parent_uid=parent_uid,
            parent_gid=0,
            parent_mode=0o700,
            socket_identity=socket_identity,
            socket_uid=0,
            socket_gid=socket_gid,
            socket_mode=0o700,
            peer_uid=0,
            max_wire_bytes=64,
            timeout_seconds=2.0,
        )
    assert not tuple(tmp_path.iterdir())


@pytest.mark.parametrize("relative,peer_uid", [(True, 1), (False, -1)])
def test_invalid_endpoint_path_or_peer_refuses_before_filesystem_access(
    tmp_path: Path, relative: bool, peer_uid: int
) -> None:
    """A relative spelling or negative kernel principal cannot enroll an endpoint."""
    path = Path("relative.sock") if relative else tmp_path / "absent.sock"
    with pytest.raises(ValueError, match="socket enrollment"):
        ProtectedWriteUnixExchange(
            path,
            parent_identity=(0, 1),
            parent_uid=0,
            parent_gid=0,
            parent_mode=0o700,
            socket_identity=(0, 2),
            socket_uid=0,
            socket_gid=0,
            socket_mode=0o700,
            peer_uid=peer_uid,
            max_wire_bytes=64,
            timeout_seconds=2.0,
        )
    assert not tuple(tmp_path.iterdir())


@pytest.mark.parametrize(
    "observed_platform,credentials_available",
    [("win32", True), ("darwin", True), ("linux", False)],
)
def test_observed_native_credential_domains_refuse_unsupported_backends(
    observed_platform: str, credentials_available: bool
) -> None:
    """Explicit observed-domain values test refusal without changing the running OS."""
    with pytest.raises(OSError, match="Linux SO_PEERCRED"):
        require_native_unix_credentials(observed_platform, credentials_available)


def test_actual_host_credential_domain_agrees_with_adapter() -> None:
    """The real adapter supplies exactly the current host platform and socket feature."""
    if sys.platform != "linux" or not hasattr(socket, "SO_PEERCRED"):
        with pytest.raises(OSError, match="Linux SO_PEERCRED"):
            require_native_unix_credentials(sys.platform, hasattr(socket, "SO_PEERCRED"))
    else:
        require_native_unix_credentials(sys.platform, hasattr(socket, "SO_PEERCRED"))


@pytest.mark.parametrize("peer_mode", ["hold", "close-during-send"])
def test_real_send_deadline_and_peer_failure_leave_no_timer(
    private_unix_directory: Path, peer_mode: str
) -> None:
    """A real upgraded peer cannot leave the public exchange blocked while it stops reading."""
    if sys.platform != "linux" or not hasattr(socket, "SO_PEERCRED"):
        with pytest.raises(OSError, match="Linux SO_PEERCRED"):
            require_native_unix_credentials(sys.platform, hasattr(socket, "SO_PEERCRED"))
        return
    path = private_unix_directory / "blocked.sock"
    script = r"""
import os,socket,sys,time
from websockets.server import ServerProtocol
listener=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
listener.bind(sys.argv[1]);os.chmod(sys.argv[1],0o600);listener.listen(1)
print('ready',flush=True)
connection,_=listener.accept()
protocol=ServerProtocol(max_size=4*1024**2)
while True:
    data=connection.recv(4096)
    if not data:raise RuntimeError('client closed before a real handshake')
    protocol.receive_data(data)
    events=protocol.events_received()
    if events:break
response=protocol.accept(events[0]);protocol.send_response(response)
for data in protocol.data_to_send():connection.sendall(data)
if sys.argv[2]=='close-during-send':
    assert connection.recv(1)==b'\x82'
else:
    time.sleep(5)
connection.close();listener.close()
"""
    process = subprocess.Popen(
        [sys.executable, "-I", "-B", "-c", script, str(path), peer_mode],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        assert process.stdout is not None
        assert select.select([process.stdout], [], [], 5)[0]
        assert process.stdout.readline() == b"ready\n"
        parent, node = private_unix_directory.stat(), path.lstat()
        exchange = ProtectedWriteUnixExchange(
            path,
            parent_identity=(parent.st_dev, parent.st_ino),
            parent_uid=parent.st_uid,
            parent_gid=parent.st_gid,
            parent_mode=parent.st_mode & 0o777,
            socket_identity=(node.st_dev, node.st_ino),
            socket_uid=node.st_uid,
            socket_gid=node.st_gid,
            socket_mode=node.st_mode & 0o777,
            peer_uid=os.geteuid(),
            max_wire_bytes=2 * 1024**2,
            timeout_seconds=0.2 if peer_mode == "hold" else 2.0,
        )
        if peer_mode == "hold":
            with pytest.raises(TimeoutError, match="send deadline elapsed"):
                exchange(b"x" * (2 * 1024**2))
            assert process.poll() is None
        else:
            with pytest.raises(ConnectionClosed):
                exchange(b"x" * (2 * 1024**2))
            assert process.wait(timeout=5) == 0
        assert not any(
            thread.name == "protected-authority-send-deadline" for thread in threading.enumerate()
        )
    finally:
        if process.poll() is None:
            process.terminate()
        process.communicate(timeout=5)
