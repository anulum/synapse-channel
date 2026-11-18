# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real-socket tests for atomic ephemeral hub binding

from __future__ import annotations

import asyncio
import contextlib
import errno
import socket

import pytest
from websockets.asyncio.client import connect

from synapse_channel import SynapseHub
from synapse_channel.core import hub_connection


async def test_port_zero_exposes_the_kernel_assigned_address() -> None:
    """A real hub binds port zero atomically and exposes its live address."""
    hub = SynapseHub(hub_id="ephemeral-bind")
    assert hub.bound_address is None

    task = asyncio.create_task(hub.serve("127.0.0.1", 0))
    host, port = await hub.wait_until_serving()
    try:
        assert host == "127.0.0.1"
        assert port > 0
        assert hub.bound_address == (host, port)
        connection = await connect(f"ws://{host}:{port}")
        await connection.close()
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    assert hub.bound_address is None


def _both_loopbacks_bind() -> bool:
    """Return whether this host can listen on ``127.0.0.1`` and ``::1`` together."""
    first = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    second: socket.socket | None = None
    try:
        first.bind(("127.0.0.1", 0))
        assigned = int(first.getsockname()[1])
        second = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        if hasattr(socket, "IPV6_V6ONLY"):
            second.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        second.bind(("::1", assigned))
    except OSError:
        return False
    finally:
        first.close()
        if second is not None:
            second.close()
    return True


async def test_port_zero_serves_every_resolved_address_on_one_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A name with several addresses listens on the one recorded port.

    Dropping the shared bind gives each address its own ephemeral port, so a
    client of the other address finds nothing where the hub says it serves.
    """
    if not _both_loopbacks_bind():
        pytest.skip("this host cannot listen on both loopback families")
    real_lookup = socket.getaddrinfo

    def both_families(
        host: str | bytes | None,
        port: str | int | None,
        family: int = 0,
        type: int = 0,
        proto: int = 0,
        flags: int = 0,
    ) -> list[tuple[int, int, int, str, tuple[object, ...]]]:
        if host == "localhost":
            ipv4: tuple[object, ...] = ("127.0.0.1", 0)
            ipv6: tuple[object, ...] = ("::1", 0, 0, 0)
            return [
                (int(socket.AF_INET), int(socket.SOCK_STREAM), int(socket.IPPROTO_TCP), "", ipv4),
                (int(socket.AF_INET6), int(socket.SOCK_STREAM), int(socket.IPPROTO_TCP), "", ipv6),
            ]
        return [
            (int(item[0]), int(item[1]), int(item[2]), str(item[3]), tuple(item[4]))
            for item in real_lookup(host, port, family, type, proto, flags)
        ]

    monkeypatch.setattr(socket, "getaddrinfo", both_families)
    hub = SynapseHub(hub_id="shared-port")
    task = asyncio.create_task(hub.serve("localhost", 0))
    try:
        _host, port = await hub.wait_until_serving()
        assert port > 0
        ipv4 = await connect(f"ws://127.0.0.1:{port}")
        await ipv4.close()
        ipv6 = await connect(f"ws://[::1]:{port}")
        await ipv6.close()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def _patch_both_families(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make ``localhost`` resolve to both loopback families."""
    real_lookup = socket.getaddrinfo

    def both_families(
        host: str | bytes | None,
        port: str | int | None,
        family: int = 0,
        type: int = 0,
        proto: int = 0,
        flags: int = 0,
    ) -> list[tuple[int, int, int, str, tuple[object, ...]]]:
        if host == "localhost":
            ipv4: tuple[object, ...] = ("127.0.0.1", 0)
            ipv6: tuple[object, ...] = ("::1", 0, 0, 0)
            return [
                (int(socket.AF_INET), int(socket.SOCK_STREAM), int(socket.IPPROTO_TCP), "", ipv4),
                (int(socket.AF_INET6), int(socket.SOCK_STREAM), int(socket.IPPROTO_TCP), "", ipv6),
            ]
        return [
            (int(item[0]), int(item[1]), int(item[2]), str(item[3]), tuple(item[4]))
            for item in real_lookup(host, port, family, type, proto, flags)
        ]

    monkeypatch.setattr(socket, "getaddrinfo", both_families)


def test_a_taken_port_starts_again_from_port_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """One collision closes every socket and binds port zero again.

    The later address is not moved to another port. Dropping the retry fails
    this on the first collision.
    """
    if not _both_loopbacks_bind():
        pytest.skip("this host cannot listen on both loopback families")
    _patch_both_families(monkeypatch)
    real_listen = hub_connection._listen
    collisions = 0

    def collide_once(address: tuple[int, tuple[object, ...]], port: int) -> socket.socket:
        nonlocal collisions
        if port != 0 and collisions == 0:
            collisions += 1
            raise OSError(errno.EADDRINUSE, "Address already in use")
        return real_listen(address, port)

    monkeypatch.setattr(hub_connection, "_listen", collide_once)
    opened = hub_connection.shared_port_sockets("localhost", 0)
    assert opened is not None
    try:
        ports = {int(listening.getsockname()[1]) for listening in opened}
        assert len(ports) == 1
        assert collisions == 1
    finally:
        for listening in opened:
            listening.close()


def test_a_port_that_stays_taken_fails_after_the_named_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A port that stays taken fails once the named bound is spent.

    The error names the addresses and the last port. The later address is
    never bound on a different port, and the number of fresh port-zero binds
    is exactly ``SHARED_PORT_ATTEMPTS``.
    """
    if not _both_loopbacks_bind():
        pytest.skip("this host cannot listen on both loopback families")
    _patch_both_families(monkeypatch)
    real_listen = hub_connection._listen
    fresh = 0

    def stay_taken(address: tuple[int, tuple[object, ...]], port: int) -> socket.socket:
        nonlocal fresh
        if port != 0:
            raise OSError(errno.EADDRINUSE, "Address already in use")
        fresh += 1
        return real_listen(address, port)

    monkeypatch.setattr(hub_connection, "_listen", stay_taken)
    with pytest.raises(hub_connection.SharedPortError, match="127.0.0.1") as caught:
        hub_connection.shared_port_sockets("localhost", 0)
    message = str(caught.value)
    assert "::1" in message
    assert "last port was" in message
    assert fresh == hub_connection.SHARED_PORT_ATTEMPTS


async def test_failed_bind_unblocks_readiness_without_a_false_address() -> None:
    """A real occupied socket fails promptly and never reports readiness."""
    listener = await asyncio.start_server(lambda _r, _w: None, "127.0.0.1", 0)
    port = int(listener.sockets[0].getsockname()[1])
    hub = SynapseHub(hub_id="occupied-bind")
    task = asyncio.create_task(hub.serve("127.0.0.1", port))
    try:
        with pytest.raises(RuntimeError, match="without a bound address"):
            await hub.wait_until_serving()
        with pytest.raises(OSError):
            await task
        assert hub.bound_address is None
    finally:
        listener.close()
        await listener.wait_closed()
