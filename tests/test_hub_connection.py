# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — unit tests for the hub's socket-connection lifecycle

from __future__ import annotations

import asyncio
from typing import Any, cast

from websockets.exceptions import ConnectionClosed

from synapse_channel.core.auth import TokenAuthenticator
from synapse_channel.core.capability import CapabilityRegistry
from synapse_channel.core.hub_clients import HubClientRegistry
from synapse_channel.core.hub_connection import HubConnection
from synapse_channel.core.protocol import WIRE_PROTOCOL_VERSION, MessageType, system_message
from synapse_channel.core.ratelimit import RateLimiter


class _Socket:
    """A stand-in websocket serving queued frames via ``recv`` and async iteration."""

    def __init__(
        self,
        frames: tuple[str, ...] = (),
        *,
        remote: Any = None,
        recv_exc: BaseException | None = None,
        iter_exc: BaseException | None = None,
        close_exc: BaseException | None = None,
    ) -> None:
        self.remote_address = remote
        self._frames = list(frames)
        self._recv_exc = recv_exc
        self._iter_exc = iter_exc
        self._close_exc = close_exc
        self.closed: list[tuple[int, str]] = []

    async def recv(self) -> str:
        if self._recv_exc is not None:
            raise self._recv_exc
        return self._frames.pop(0)

    def __aiter__(self) -> _Socket:
        return self

    async def __anext__(self) -> str:
        if self._frames:
            return self._frames.pop(0)
        if self._iter_exc is not None:
            raise self._iter_exc
        raise StopAsyncIteration

    async def close(self, *, code: int, reason: str) -> None:
        self.closed.append((code, reason))
        if self._close_exc is not None:
            raise self._close_exc


class _Router:
    """Records routed frames; optionally binds the first socket it sees a name for."""

    def __init__(self, clients: HubClientRegistry | None = None, bind: str | None = None) -> None:
        self.seen: list[Any] = []
        self._clients = clients
        self._bind = bind

    async def handle_message(self, raw: str | bytes, websocket: Any) -> None:
        self.seen.append(raw)
        if self._clients is not None and self._bind is not None:
            self._clients.socket_agent.setdefault(websocket, self._bind)


class _Recorder:
    """Records every welcome (or other frame) the lifecycle sends."""

    def __init__(self) -> None:
        self.sent: list[tuple[Any, dict[str, Any]]] = []

    async def send_json(self, websocket: Any, data: dict[str, Any]) -> None:
        self.sent.append((websocket, data))


class _Presence:
    """Records the departure broadcasts the lifecycle emits."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []

    async def broadcast_presence(self, event: str, agent: str | None = None) -> None:
        self.calls.append((event, agent))


class _RecordingRateLimiter:
    """A rate limiter that only records which agents were forgotten."""

    def __init__(self) -> None:
        self.forgotten: list[str] = []

    def forget(self, agent: str) -> None:
        self.forgotten.append(agent)


class _Loop:
    """A stand-in event loop recording (or refusing) signal-handler installs."""

    def __init__(self, *, unsupported: bool = False) -> None:
        self.handlers: list[Any] = []
        self._unsupported = unsupported

    def add_signal_handler(self, sig: Any, callback: Any) -> None:
        if self._unsupported:
            raise NotImplementedError
        self.handlers.append(sig)


def _system(payload: str, **extra: Any) -> dict[str, Any]:
    return system_message(payload, hub_id="syn-test", **extra)


def _registry(
    *,
    max_clients: int = 8,
    max_unauth_clients: int | None = None,
    max_connections_per_host: int | None = None,
) -> HubClientRegistry:
    return HubClientRegistry(
        max_clients=max_clients,
        max_unauth_clients=max_unauth_clients,
        max_connections_per_host=max_connections_per_host,
        takeover_cooldown=0.0,
        clock=lambda: 0.0,
    )


def _connection(
    clients: HubClientRegistry,
    *,
    authenticator: TokenAuthenticator | None = None,
    auth_timeout: float = 5.0,
    rate_limiter: RateLimiter | None = None,
    capabilities: CapabilityRegistry | None = None,
    router: _Router | None = None,
    recorder: _Recorder | None = None,
    presence: _Presence | None = None,
    dropped: list[str] | None = None,
    forgotten: list[str] | None = None,
) -> HubConnection:
    rec = recorder or _Recorder()
    pres = presence or _Presence()
    drop_sink = dropped if dropped is not None else []
    forget_sink = forgotten if forgotten is not None else []
    return HubConnection(
        clients,
        capabilities or CapabilityRegistry(),
        authenticator=authenticator,
        auth_timeout=auth_timeout,
        rate_limiter=rate_limiter,
        handle_message=(router or _Router()).handle_message,
        send_json=rec.send_json,
        system=_system,
        online_agents=lambda: ["a", "b"],
        broadcast_presence=pres.broadcast_presence,
        drop_waits=drop_sink.append,
        forget_liveness=forget_sink.append,
    )


# -- register ----------------------------------------------------------------


async def test_register_welcomes_on_an_open_hub() -> None:
    clients = _registry()
    recorder = _Recorder()
    conn = _connection(clients, recorder=recorder)
    socket = _Socket()

    await conn.register(socket)

    assert socket in clients.connected_clients
    assert len(recorder.sent) == 1
    assert recorder.sent[0][1]["type"] == MessageType.WELCOME


async def test_register_withholds_welcome_on_a_secured_hub() -> None:
    clients = _registry()
    recorder = _Recorder()
    conn = _connection(clients, authenticator=TokenAuthenticator(["t"]), recorder=recorder)
    socket = _Socket()

    await conn.register(socket)

    assert socket in clients.connected_clients
    assert recorder.sent == []


# -- send_welcome ------------------------------------------------------------


async def test_send_welcome_carries_the_roster_and_count() -> None:
    clients = _registry()
    clients.add_client(_Socket())
    recorder = _Recorder()
    conn = _connection(clients, recorder=recorder)
    socket = _Socket()

    await conn.send_welcome(socket)

    _, frame = recorder.sent[0]
    assert frame["type"] == MessageType.WELCOME
    assert frame["target"] == "self"
    assert frame["connected_clients"] == 1
    assert frame["online_agents"] == ["a", "b"]
    assert frame["protocol_version"] == WIRE_PROTOCOL_VERSION


# -- unregister --------------------------------------------------------------


async def test_unregister_releases_a_bound_name_and_announces_departure() -> None:
    clients = _registry()
    socket = _Socket()
    clients.add_client(socket)
    clients.socket_agent[socket] = "a"
    clients.agent_sockets["a"] = socket
    capabilities = CapabilityRegistry()
    capabilities.advertise("a", description="worker")
    limiter = _RecordingRateLimiter()
    presence = _Presence()
    dropped: list[str] = []
    forgotten: list[str] = []
    conn = _connection(
        clients,
        rate_limiter=cast(RateLimiter, limiter),
        capabilities=capabilities,
        presence=presence,
        dropped=dropped,
        forgotten=forgotten,
    )

    await conn.unregister(socket)

    assert socket not in clients.connected_clients
    assert dropped == ["a"]
    assert capabilities.get("a") is None
    assert limiter.forgotten == ["a"]
    assert forgotten == ["a"]
    assert presence.calls == [("left", "a")]


async def test_unregister_without_a_rate_limiter_skips_forget() -> None:
    clients = _registry()
    socket = _Socket()
    clients.add_client(socket)
    clients.socket_agent[socket] = "a"
    clients.agent_sockets["a"] = socket
    presence = _Presence()
    conn = _connection(clients, presence=presence)

    await conn.unregister(socket)

    assert presence.calls == [("left", "a")]


async def test_unregister_of_an_unnamed_socket_is_silent() -> None:
    clients = _registry()
    socket = _Socket()
    clients.add_client(socket)
    presence = _Presence()
    dropped: list[str] = []
    forgotten: list[str] = []
    conn = _connection(clients, presence=presence, dropped=dropped, forgotten=forgotten)

    await conn.unregister(socket)

    assert presence.calls == []
    assert dropped == []
    assert forgotten == []


# -- authenticate_or_close ---------------------------------------------------


async def test_authenticate_or_close_times_out() -> None:
    clients = _registry()
    conn = _connection(clients, authenticator=TokenAuthenticator(["t"]), auth_timeout=0.05)
    socket = _Socket(recv_exc=asyncio.TimeoutError())

    assert await conn.authenticate_or_close(socket) is False
    assert socket.closed == [(4012, "auth timeout")]


async def test_authenticate_or_close_open_hub_registration_timeout() -> None:
    clients = _registry()
    conn = _connection(clients, authenticator=None, auth_timeout=0.05)
    socket = _Socket(recv_exc=asyncio.TimeoutError())

    assert await conn.authenticate_or_close(socket) is False
    assert socket.closed == [(4012, "registration timeout")]


async def test_authenticate_or_close_open_hub_refuses_unbound_first_frame() -> None:
    clients = _registry()
    router = _Router(clients, bind=None)
    conn = _connection(clients, authenticator=None, router=router)
    socket = _Socket(("frame",))

    assert await conn.authenticate_or_close(socket) is False
    assert socket.closed == [(4010, "registration required")]


async def test_authenticate_or_close_on_a_dropped_socket() -> None:
    clients = _registry()
    conn = _connection(clients, authenticator=TokenAuthenticator(["t"]))
    socket = _Socket(recv_exc=ConnectionClosed(None, None))

    assert await conn.authenticate_or_close(socket) is False
    assert socket.closed == []


async def test_authenticate_or_close_admits_a_bound_first_frame() -> None:
    clients = _registry()
    router = _Router(clients, bind="a")
    conn = _connection(clients, authenticator=TokenAuthenticator(["t"]), router=router)
    socket = _Socket(("frame",))

    assert await conn.authenticate_or_close(socket) is True
    assert router.seen == ["frame"]
    assert socket.closed == []


async def test_authenticate_or_close_refuses_an_unbound_first_frame() -> None:
    clients = _registry()
    router = _Router(clients, bind=None)
    conn = _connection(clients, authenticator=TokenAuthenticator(["t"]), router=router)
    socket = _Socket(("frame",))

    assert await conn.authenticate_or_close(socket) is False
    assert socket.closed == [(4010, "auth required")]


async def test_authenticate_or_close_suppresses_a_failing_late_close() -> None:
    # The token gate may already have closed the socket; a second close that raises
    # is swallowed, and the refusal still returns False.
    clients = _registry()
    router = _Router(clients, bind=None)
    conn = _connection(clients, authenticator=TokenAuthenticator(["t"]), router=router)
    socket = _Socket(("frame",), close_exc=RuntimeError("already closed"))

    assert await conn.authenticate_or_close(socket) is False
    assert socket.closed == [(4010, "auth required")]


# -- handler -----------------------------------------------------------------


async def test_handler_refuses_when_at_capacity() -> None:
    clients = _registry(max_clients=1)
    clients.add_client(_Socket())
    conn = _connection(clients)
    socket = _Socket()

    await conn.handler(socket)

    assert socket.closed == [(4013, "hub at capacity")]
    assert socket not in clients.connected_clients


async def test_handler_refuses_when_host_at_capacity() -> None:
    clients = _registry(max_connections_per_host=1)
    clients.add_client(_Socket(remote=None))  # occupies the "unknown" host slot
    conn = _connection(clients)
    socket = _Socket(remote=None)

    await conn.handler(socket)

    assert socket.closed == [(4015, "too many connections from host")]


async def test_handler_refuses_an_unauthenticated_burst() -> None:
    clients = _registry(max_unauth_clients=1)
    clients.add_unauthenticated(_Socket())
    conn = _connection(clients, authenticator=TokenAuthenticator(["t"]))
    socket = _Socket()

    await conn.handler(socket)

    assert socket.closed == [(4014, "too many unauthenticated connections")]


async def test_handler_open_hub_pumps_frames_then_unregisters() -> None:
    clients = _registry()
    router = _Router(clients, bind="agent")
    recorder = _Recorder()
    conn = _connection(clients, router=router, recorder=recorder)
    socket = _Socket(("one", "two"))

    await conn.handler(socket)

    assert recorder.sent[0][1]["type"] == MessageType.WELCOME  # open hub welcomes on connect
    assert router.seen == ["one", "two"]
    assert socket not in clients.connected_clients  # unregistered in the finally


async def test_handler_open_hub_times_out_without_registration() -> None:
    clients = _registry()
    conn = _connection(clients, authenticator=None, auth_timeout=0.05)
    socket = _Socket(recv_exc=asyncio.TimeoutError())

    await conn.handler(socket)

    assert socket.closed == [(4012, "registration timeout")]
    assert socket not in clients.connected_clients


async def test_handler_open_hub_refuses_unbound_first_frame() -> None:
    clients = _registry()
    router = _Router(clients, bind=None)
    conn = _connection(clients, authenticator=None, router=router)
    socket = _Socket(("no-name",))

    await conn.handler(socket)

    assert socket.closed == [(4010, "registration required")]
    assert router.seen == ["no-name"]
    assert socket not in clients.connected_clients


async def test_handler_secured_hub_authenticates_then_pumps() -> None:
    clients = _registry()
    router = _Router(clients, bind="a")
    conn = _connection(clients, authenticator=TokenAuthenticator(["t"]), router=router)
    socket = _Socket(("auth", "msg"))

    await conn.handler(socket)

    assert router.seen == ["auth", "msg"]
    assert socket not in clients.unauth_clients  # discarded in the finally
    assert socket not in clients.connected_clients


async def test_handler_secured_hub_returns_when_auth_fails() -> None:
    clients = _registry()
    router = _Router(clients, bind=None)  # never binds → authentication fails
    conn = _connection(clients, authenticator=TokenAuthenticator(["t"]), router=router)
    socket = _Socket(("auth",))

    await conn.handler(socket)

    assert socket.closed == [(4010, "auth required")]
    assert router.seen == ["auth"]  # the pump loop never ran
    assert socket not in clients.connected_clients


async def test_handler_suppresses_connection_closed_mid_stream() -> None:
    clients = _registry()
    router = _Router(clients, bind="agent")
    conn = _connection(clients, router=router)
    socket = _Socket(("one",), iter_exc=ConnectionClosed(None, None))

    await conn.handler(socket)  # ConnectionClosed after the first frame is swallowed

    assert router.seen == ["one"]
    assert socket not in clients.connected_clients  # still unregistered


# -- install_signal_handlers -------------------------------------------------


def test_install_signal_handlers_wires_both_signals() -> None:
    import signal

    loop = _Loop()
    stop = asyncio.Event()

    HubConnection.install_signal_handlers(cast(asyncio.AbstractEventLoop, loop), stop)

    assert loop.handlers == [signal.SIGTERM, signal.SIGINT]


def test_install_signal_handlers_tolerates_unsupported_platforms() -> None:
    loop = _Loop(unsupported=True)
    stop = asyncio.Event()

    # A platform without add_signal_handler raises NotImplementedError, which is
    # suppressed — the call is a no-op rather than an error.
    HubConnection.install_signal_handlers(cast(asyncio.AbstractEventLoop, loop), stop)

    assert loop.handlers == []
