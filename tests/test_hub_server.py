# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — the listener component, exercised without a hub
"""Bind-address rules, and the limits a hub passes when its listener opens."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import socket
import ssl
from typing import Any, cast

import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus

from hub_e2e_helpers import running_hub
from synapse_channel.core import hub_server as hub_server_module
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.hub_server import HubServer
from synapse_channel.core.hub_service_lifetime import HubServiceLifetime


def _handler(*_args: Any, **_kwargs: Any) -> None:
    """Stand in for the hub callback the listener receives."""


def _server() -> HubServer:
    """Build a listener with fixed limits."""
    return HubServer(max_queue=2, ping_interval=3.0, ping_timeout=4.0)


def test_constructor_is_keyword_only_and_the_bind_is_read_only() -> None:
    """Limits are keywords. Callers cannot replace the host or the port."""
    factory: Any = HubServer
    with pytest.raises(TypeError):
        factory(2, 3.0, 4.0)
    server = _server()
    assert server.bind_host == "localhost"
    assert server.bind_port == 8876
    assert server.bound_address is None
    for name in ("bind_host", "bind_port"):
        with pytest.raises(AttributeError):
            setattr(server, name, "closed")


def test_record_bound_keeps_one_port_and_refuses_none_or_several() -> None:
    """The HTTP guard later reads the one port the sockets share."""
    server = _server()
    with pytest.raises(RuntimeError, match="without a listening socket"):
        server._record_bound([], "127.0.0.1")
    first = socket.socket()
    second = socket.socket()
    first.bind(("127.0.0.1", 0))
    second.bind(("127.0.0.1", 0))
    try:
        with pytest.raises(RuntimeError, match="different ports"):
            server._record_bound([first, second], "127.0.0.1")
        server._record_bound([first], "127.0.0.1")
        port = int(first.getsockname()[1])
        assert server.bind_port == port
        assert server.bound_address == ("127.0.0.1", port)
    finally:
        first.close()
        second.close()


def test_the_running_line_names_the_hub_logger(caplog: pytest.LogCaptureFixture) -> None:
    """The scheme and the assigned port are logged on ``synapse.hub``."""
    server = _server()
    server._bind_port = 9
    caplog.set_level(logging.INFO, logger="synapse.hub")
    server._log_address("127.0.0.1", None)
    server._log_address("127.0.0.1", ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT))
    assert [record.name for record in caplog.records] == ["synapse.hub", "synapse.hub"]
    assert "Synapse Hub running on ws://127.0.0.1:9" in caplog.text
    assert "Synapse Hub running on wss://127.0.0.1:9" in caplog.text


def test_open_uses_the_owned_limits_for_both_listeners(monkeypatch: pytest.MonkeyPatch) -> None:
    """A fresh socket and an adopted socket both receive the same limits."""
    seen: list[dict[str, Any]] = []

    def fake_serve(*args: Any, **kwargs: Any) -> str:
        """Record one listener opening without binding."""
        seen.append({"args": args, "kwargs": kwargs})
        return "listener"

    monkeypatch.setattr(hub_server_module, "serve", fake_serve)
    server = _server()
    handler = _handler
    fresh: Any = server._open("127.0.0.1", 0, None, None, handler, handler, 11, 6.0)
    assert fresh == "listener"
    adopted = socket.socket()
    adopted.bind(("127.0.0.1", 0))
    try:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        adopted_open: Any = server._open(
            "127.0.0.1", 0, context, adopted, handler, handler, 11, 6.0
        )
        assert adopted_open == "listener"
    finally:
        adopted.close()
    assert seen[0]["args"][:3] == (handler, "127.0.0.1", 0)
    assert seen[0]["kwargs"]["max_size"] == 11
    assert seen[0]["kwargs"]["max_queue"] == 2
    assert seen[0]["kwargs"]["ping_interval"] == 3.0
    assert seen[0]["kwargs"]["ping_timeout"] == 4.0
    assert seen[0]["kwargs"]["close_timeout"] == 6.0
    assert seen[0]["kwargs"]["process_request"] is handler
    assert seen[0]["kwargs"]["ssl"] is None
    assert seen[0]["kwargs"]["logger"].name == "synapse.hub.ws"
    assert seen[1]["kwargs"]["sock"] is adopted
    assert seen[1]["kwargs"]["ssl"] is context


class _Socket:
    """Stand-in for one already bound listening socket."""

    def __init__(self, port: int) -> None:
        self.port = port
        self.closed = False

    def getsockname(self) -> tuple[str, int]:
        """Return the address this stand-in claims."""
        return ("127.0.0.1", self.port)

    def close(self) -> None:
        """Record that a failed attempt released this socket."""
        self.closed = True


class _Listener:
    """Async context whose sockets are the ones ``serve`` records."""

    def __init__(self, port: int) -> None:
        self.sockets = [_Socket(port)]

    async def __aenter__(self) -> _Listener:
        """Return the same listener the stack will start."""
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        """Leave suppression to the caller."""
        return False

    async def start_serving(self) -> None:
        """Accepting is not part of this stand-in."""


class _Services:
    """The four operations ``serve`` calls, with no real background work."""

    def __init__(self) -> None:
        self.checked = False
        self.finished: bool | None = None

    def monitoring(self) -> _Listener:
        """A context that does not acquire anything."""
        return _Listener(0)

    def start(self, stop: Any) -> None:
        """Request the clean stop the success path waits for."""
        stop.set()

    def check_completed(self) -> None:
        """Record that the stop was observed before the listener was cleared."""
        self.checked = True

    async def finish(self, started: bool) -> None:
        """Record whether the bind had been published."""
        self.finished = started


def _services() -> HubServiceLifetime:
    """The stand-in, typed as the lifetime ``serve`` already accepts."""
    return cast(HubServiceLifetime, _Services())


async def test_serve_reaches_a_clean_stop_on_a_fresh_socket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No adopted sockets, then a stop, then the address is cleared."""
    monkeypatch.setattr(hub_server_module, "shared_port_sockets", lambda _host, _port: None)
    monkeypatch.setattr(HubServer, "_open", lambda *_args: _Listener(9))
    server = _server()
    services = _Services()
    await server.serve(
        "127.0.0.1",
        0,
        None,
        stop=asyncio.Event(),
        services=cast(HubServiceLifetime, services),
        handler=_handler,
        process_request=_handler,
        max_size=10,
        close_timeout=5.0,
    )
    assert services.checked
    assert services.finished is True
    assert server.bound_address is None
    assert server.bind_port == 9


async def test_serve_adopts_every_shared_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shared-port sockets are all started, and they must share one port."""
    sockets = [_Socket(9), _Socket(9)]
    monkeypatch.setattr(hub_server_module, "shared_port_sockets", lambda _host, _port: sockets)
    monkeypatch.setattr(HubServer, "_open", lambda *_args: _Listener(9))
    server = _server()
    services = _Services()
    await server.serve(
        "127.0.0.1",
        0,
        None,
        stop=asyncio.Event(),
        services=cast(HubServiceLifetime, services),
        handler=_handler,
        process_request=_handler,
        max_size=10,
        close_timeout=5.0,
    )
    assert services.checked
    assert server.bound_address is None
    assert not sockets[0].closed and not sockets[1].closed


async def test_a_failed_shared_bind_closes_the_sockets_still_waiting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second adopted socket that never opens is closed, and the waiter is woken."""
    sockets = [_Socket(9), _Socket(8)]
    calls = 0

    def opened(*_args: Any) -> _Listener:
        """Open the first listener and refuse the second."""
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("bind refused")
        return _Listener(9)

    monkeypatch.setattr(hub_server_module, "shared_port_sockets", lambda _host, _port: sockets)
    monkeypatch.setattr(HubServer, "_open", opened)
    server = _server()
    with pytest.raises(RuntimeError, match="bind refused"):
        await server.serve(
            "127.0.0.1",
            0,
            None,
            stop=asyncio.Event(),
            services=_services(),
            handler=_handler,
            process_request=_handler,
            max_size=10,
            close_timeout=5.0,
        )
    assert sockets[1].closed
    assert not sockets[0].closed
    assert server._serving.is_set()
    assert server.bound_address is None


async def test_a_failure_after_readiness_leaves_the_waiter_cleared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Once the bind is published, a later failure clears readiness again."""
    monkeypatch.setattr(hub_server_module, "shared_port_sockets", lambda _host, _port: None)
    monkeypatch.setattr(HubServer, "_open", lambda *_args: _Listener(9))

    class _Boom(_Services):
        """Start the listener, then fail the background work."""

        def start(self, stop: Any) -> None:
            """Raise after readiness, without requesting stop."""
            raise RuntimeError("background failed")

    server = _server()
    with pytest.raises(RuntimeError, match="background failed"):
        await server.serve(
            "127.0.0.1",
            0,
            None,
            stop=asyncio.Event(),
            services=cast(HubServiceLifetime, _Boom()),
            handler=_handler,
            process_request=_handler,
            max_size=10,
            close_timeout=5.0,
        )
    assert server.bound_address is None
    assert not server._serving.is_set()


async def test_wait_returns_the_address_and_refuses_an_empty_one() -> None:
    """Readiness without an address is the bind error, not a timeout."""
    server = _server()
    with pytest.raises(TimeoutError):
        await server.wait_until_serving(timeout=0.01)
    server._serving.set()
    with pytest.raises(RuntimeError, match="without a bound address"):
        await server.wait_until_serving(timeout=0.1)
    server._bound_address = ("127.0.0.1", 9)
    assert await server.wait_until_serving(timeout=0.1) == ("127.0.0.1", 9)


class _BoundSocket:
    """One listening socket with a fixed assigned port."""

    def getsockname(self) -> tuple[str, int]:
        """Return the address the fake listener claims."""
        return ("127.0.0.1", 9)


class _OpenedListener:
    """Async context the hub can enter without opening a real socket."""

    sockets = [_BoundSocket()]

    async def __aenter__(self) -> _OpenedListener:
        """Return the listener the stack will start."""
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        """Leave suppression to the caller."""
        return False

    async def start_serving(self) -> None:
        """Accepting is not part of this stand-in."""


def _listener_module(hub: SynapseHub) -> Any:
    """The module whose ``serve`` factory opens the listener."""
    if hasattr(hub, "_server"):
        return hub_server_module
    from synapse_channel.core import hub as listener_module

    return listener_module


async def test_serve_reads_the_message_size_and_close_timeout_at_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Writes after construction reach the listener that ``serve`` opens."""
    hub = SynapseHub(hub_id="late-limits")
    hub.max_msg_bytes = 123456
    hub.shutdown_close_timeout = 2.5
    seen: list[dict[str, Any]] = []

    def fake_serve(*_args: Any, **kwargs: Any) -> _OpenedListener:
        """Record the limits and return a listener the hub can publish."""
        seen.append(kwargs)
        return _OpenedListener()

    monkeypatch.setattr(_listener_module(hub), "serve", fake_serve)
    async with running_hub(hub):
        assert seen
        assert seen[0]["max_size"] == 123456
        assert seen[0]["close_timeout"] == 2.5


async def test_a_bind_all_listener_records_its_host_and_refuses_localhost() -> None:
    """A bind-all listener keeps that host, so a localhost Host fails closed."""
    hub = SynapseHub(hub_id="bind-all", insecure_off_loopback=True)
    task = asyncio.create_task(hub.serve("0.0.0.0", 0))
    try:
        _host, port = await hub.wait_until_serving()
        assert hub._server.bind_host == "0.0.0.0"
        with pytest.raises(InvalidStatus) as refusal:
            await connect(f"ws://localhost:{port}")
        assert refusal.value.response.status_code == 403
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
