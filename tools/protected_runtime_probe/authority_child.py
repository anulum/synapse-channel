# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — distinct-UID prerequisite peer mechanics
"""Exercise actual Unix peer credentials without granting protected-write authority."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import socket
import struct
import sys
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from websockets.asyncio.server import ServerConnection, unix_serve

from synapse_channel.core.protected_write_unix_exchange import ProtectedWriteUnixExchange

from . import require_native_linux
from .resources import enter_resource_leaf
from .roles import NAMES, drop_enrolled_identity, resolve_role_identity

_FRAME = b"synapse-prerequisite-peer-check-v1"


def call_endpoint(path: Path, *, expected_peer_uid: int) -> None:
    """Exchange one bounded component probe with a kernel-authenticated authority.

    Parameters
    ----------
    path:
        Endpoint selected by the controller. Local metadata is captured for this
        exchange; it is not an independently enrolled endpoint-identity pin.
    expected_peer_uid:
        Independently read system authority UID, not filesystem owner UID.

    Raises
    ------
    ValueError
        On a change after local endpoint capture, wrong kernel peer or a different response.
    OSError
        On failed actual connection, filesystem or credential operations.

    Notes
    -----
    This probe tests connectivity and identity mechanics only. The echoed frame
    is not a signed CurrentStatus, execution grant, CLASS receipt or operation.
    """
    parent = path.parent.lstat()
    endpoint = path.lstat()
    exchange = ProtectedWriteUnixExchange(
        path,
        parent_identity=(parent.st_dev, parent.st_ino),
        parent_uid=parent.st_uid,
        parent_gid=parent.st_gid,
        parent_mode=parent.st_mode & 0o777,
        socket_identity=(endpoint.st_dev, endpoint.st_ino),
        socket_uid=endpoint.st_uid,
        socket_gid=endpoint.st_gid,
        socket_mode=endpoint.st_mode & 0o777,
        peer_uid=expected_peer_uid,
        max_wire_bytes=128,
        timeout_seconds=3.0,
    )
    if exchange(_FRAME) != _FRAME:
        raise ValueError("prerequisite authenticated echo differs")


@contextmanager
def _shutdown_signal(enabled: bool) -> Iterator[asyncio.Future[None]]:
    """Own SIGTERM only during the explicit finite collector listener interval."""
    loop = asyncio.get_running_loop()
    stopped: asyncio.Future[None] = loop.create_future()
    previous = signal.getsignal(signal.SIGTERM)

    def stop() -> None:
        if not stopped.done():
            stopped.set_result(None)

    if enabled:
        if previous not in {signal.SIG_DFL, signal.SIG_IGN}:
            raise ValueError("finite listener cannot replace an existing termination handler")
        loop.add_signal_handler(signal.SIGTERM, stop)
    try:
        yield stopped
    finally:
        if enabled:
            loop.remove_signal_handler(signal.SIGTERM)
            signal.signal(signal.SIGTERM, previous)
        stopped.cancel()


async def serve_prerequisite_listeners(
    request: socket.socket,
    operator: socket.socket,
    config: dict[str, Any],
    *,
    graceful_stop: bool = False,
) -> None:
    """Serve exact real peer UIDs on the two separate bounded listeners.

    Parameters
    ----------
    request, operator:
        Independently retained bound Unix sockets, before listening.
    config:
        Controller-selected writer_uid, client_uid and operator_uid.
    graceful_stop:
        Explicit finite instrumentation mode. Installs a scoped SIGTERM
        handler before readiness; termination exits both listener contexts
        so the launcher's finally block can stop and save its own collector.
        The default production listener still waits until its owner stops it.

    Raises
    ------
    ValueError, OSError
        On an unsupported platform, invalid mode or unavailable listener.
        A pre-existing custom termination handler is never replaced.

    Notes
    -----
    This is a component peer/framing probe, not a protected-operation grant.
    A controller must independently observe the graceful process exit and
    dataset; sending a signal alone is not successful instrumentation.
    """
    require_native_linux(sys.platform, operation="prerequisite listener")
    if type(graceful_stop) is not bool:
        raise ValueError("invalid finite listener termination mode")
    request.listen(4)
    operator.listen(4)

    def handler(allowed: frozenset[int]) -> Callable[[ServerConnection], Awaitable[None]]:
        async def exchange(connection: ServerConnection) -> None:
            transport_socket = connection.transport.get_extra_info("socket")
            credentials = transport_socket.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
            _pid, uid, _gid = struct.unpack("3i", credentials)
            if uid not in allowed:
                await connection.close(code=1008, reason="unexpected prerequisite principal")
                return
            if await connection.recv() != _FRAME:
                await connection.close(code=1008, reason="unexpected prerequisite frame")
                return
            await connection.send(_FRAME)

        return exchange

    with _shutdown_signal(graceful_stop) as stopped:
        await _serve_listeners(request, operator, config, handler, stopped)


async def _serve_listeners(
    request: socket.socket,
    operator: socket.socket,
    config: dict[str, Any],
    handler: Callable[[frozenset[int]], Callable[[ServerConnection], Awaitable[None]]],
    stopped: asyncio.Future[None],
) -> None:
    """Keep both server contexts alive until their owned shutdown future resolves."""
    async with (
        unix_serve(
            handler(frozenset((config["writer_uid"], config["client_uid"]))),
            sock=request,
            max_size=128,
            max_queue=4,
            compression=None,
            open_timeout=3,
            close_timeout=1,
            ping_interval=None,
        ),
        unix_serve(
            handler(frozenset((config["operator_uid"],))),
            sock=operator,
            max_size=128,
            max_queue=4,
            compression=None,
            open_timeout=3,
            close_timeout=1,
            ping_interval=None,
        ),
    ):
        print('{"event":"authority-listening"}', flush=True)
        await stopped


def probe_role_access(role: str, endpoints: Path, data_reference: int, authority_uid: int) -> None:
    """Require a role's real peer exchange, cross-role refusal and writer-data denial.

    Parameters
    ----------
    role:
        Exact client or operator role selected by the trusted controller.
    endpoints:
        Enrolled endpoint directory; authentication uses the actual kernel peer UID.
    data_reference:
        Borrowed O_PATH-only directory descriptor. It grants metadata access,
        not directory reads. The caller retains it throughout this probe.
    authority_uid:
        Independently enrolled authority UID for the successful connection.

    Raises
    ------
    ValueError
        On a wrong role/reference or any unexpectedly accessible private resource.
    OSError
        On unavailable native operations or failed expected peer exchange.

    Notes
    -----
    Reopening this process's retained O_PATH reference performs a fresh kernel
    read-permission check on the data inode. It never crosses the controller's
    procfs directory or an inaccessible backing ancestor. This is a finite
    component probe, not whole-runtime or multi-principal acceptance.
    """
    require_native_linux(sys.platform, operation="prerequisite peer data access")
    import fcntl

    if role not in {"synapse-client", "synapse-operator"}:
        raise ValueError("unexpected prerequisite peer role")
    if not fcntl.fcntl(data_reference, fcntl.F_GETFL) & os.O_PATH:
        raise ValueError("prerequisite peer data reference must be O_PATH only")
    target = "operator.sock" if role == "synapse-operator" else "request.sock"
    other = "request.sock" if target == "operator.sock" else "operator.sock"
    call_endpoint(endpoints / target, expected_peer_uid=authority_uid)
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as attempt:
            attempt.settimeout(3)
            attempt.connect(str(endpoints / other))
    except PermissionError:
        pass
    else:
        raise ValueError("prerequisite role reached the other role's endpoint")
    try:
        descriptor = os.open(
            f"/proc/self/fd/{data_reference}", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
        )
    except PermissionError:
        pass
    else:
        os.close(descriptor)
        raise ValueError("prerequisite non-writer role reached private data")
    print(json.dumps({"event": "peer-probe-complete", "role": role}), flush=True)


def main(argv: list[str] | None = None) -> int:
    """Enter the exact cgroup, drop credentials, then serve or run one peer probe.

    Parameters
    ----------
    argv:
        Root-controller arguments specifying only the retained resource leaf.

    Returns
    -------
    int
        Zero after a finite client probe; authority serves until its cgroup stops.

    Raises
    ------
    ValueError
        On an invalid role or missing actual account. No fallback principal exists.
    PermissionError
        Unless invoked by the root controller before dropping credentials.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cgroup", type=Path, required=True)
    parser.add_argument("--graceful-stop", action="store_true")
    arguments = parser.parse_args(argv)
    enter_resource_leaf(arguments.cgroup)
    raw = sys.stdin.buffer.readline(8193)
    if len(raw) > 8192:
        raise ValueError("prerequisite child configuration exceeds byte bound")
    config = json.loads(raw)
    name = config["role"]
    if name not in NAMES:
        raise ValueError("unexpected prerequisite child role")
    role = resolve_role_identity(name)
    if name == "synapse-authority":
        request = socket.socket(fileno=config["request_fd"])
        operator = socket.socket(fileno=config["operator_fd"])
        drop_enrolled_identity(role.uid, role.gid)
        try:
            asyncio.run(
                serve_prerequisite_listeners(
                    request, operator, config, graceful_stop=arguments.graceful_stop
                )
            )
        finally:
            request.close()
            operator.close()
    else:
        if name not in {"synapse-client", "synapse-operator"}:
            raise ValueError("writer must use its namespace entry point")
        drop_enrolled_identity(role.uid, role.gid)
        reference = config["data_reference"]
        try:
            probe_role_access(name, Path(config["endpoints"]), reference, config["authority_uid"])
        finally:
            os.close(reference)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
