# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — unit tests for the hub's pre-route ingress guards

from __future__ import annotations

from typing import Any

import pytest

from synapse_channel.core.auth import TokenAuthenticator
from synapse_channel.core.hub_clients import HubClientRegistry
from synapse_channel.core.hub_exposure import InsecureBindError
from synapse_channel.core.hub_ingress import HubIngress
from synapse_channel.core.protocol import MessageType, system_message


class _Socket:
    """A stand-in websocket recording its close calls (and optional wait_closed)."""

    def __init__(self, remote: Any = None, *, wait_closed: bool = True) -> None:
        self.remote_address = remote
        self.closed: list[tuple[int, str]] = []
        self.waited = 0
        self._has_wait_closed = wait_closed

    async def close(self, *, code: int, reason: str) -> None:
        self.closed.append((code, reason))

    def __getattr__(self, name: str) -> Any:
        # Expose wait_closed only when the socket was built to support it, so the
        # close-and-wait branch and the no-wait branch can both be exercised.
        if name == "wait_closed" and self.__dict__.get("_has_wait_closed"):

            async def _wait_closed() -> None:
                self.waited += 1

            return _wait_closed
        raise AttributeError(name)


class _RaisingSocket:
    """A websocket whose close raises, to prove close_socket is best-effort."""

    async def close(self, *, code: int, reason: str) -> None:
        raise RuntimeError("socket already gone")


class _Recorder:
    """Records every frame the ingress sends through the injected send callback."""

    def __init__(self) -> None:
        self.sent: list[tuple[Any, dict[str, Any]]] = []

    async def send_json(self, websocket: Any, data: dict[str, Any]) -> None:
        self.sent.append((websocket, data))


def _system(payload: str, **extra: Any) -> dict[str, Any]:
    return system_message(payload, hub_id="syn-test", **extra)


def _registry() -> HubClientRegistry:
    return HubClientRegistry(
        max_clients=8,
        max_unauth_clients=None,
        max_connections_per_host=None,
        takeover_cooldown=0.0,
        clock=lambda: 0.0,
    )


def _ingress(
    clients: HubClientRegistry,
    *,
    authenticator: TokenAuthenticator | None = None,
    enable_metrics: bool = False,
    metrics_token: str | None = None,
    metrics_query_token_ok: bool = False,
    insecure_off_loopback: bool = False,
    recorder: _Recorder | None = None,
) -> tuple[HubIngress, _Recorder]:
    rec = recorder or _Recorder()
    ingress = HubIngress(
        clients,
        authenticator=authenticator,
        enable_metrics=enable_metrics,
        metrics_token=metrics_token,
        metrics_query_token_ok=metrics_query_token_ok,
        insecure_off_loopback=insecure_off_loopback,
        send_json=rec.send_json,
        system=_system,
    )
    return ingress, rec


# -- authorise ---------------------------------------------------------------


async def test_authorise_open_hub_admits_without_checks() -> None:
    ingress, rec = _ingress(_registry())
    socket = _Socket()

    assert await ingress.authorise("a", {}, socket) is True
    assert rec.sent == []
    assert socket.closed == []


async def test_authorise_trusts_an_already_bound_socket() -> None:
    clients = _registry()
    socket = _Socket()
    clients.socket_agent[socket] = "a"
    # A denying authenticator is configured, yet the bound socket is trusted: auth
    # is checked once, at first bind, never again on the same socket.
    ingress, rec = _ingress(clients, authenticator=TokenAuthenticator(["secret"]))

    assert await ingress.authorise("a", {"token": "wrong"}, socket) is True
    assert rec.sent == []
    assert socket.closed == []


async def test_authorise_admits_a_valid_first_token() -> None:
    ingress, rec = _ingress(_registry(), authenticator=TokenAuthenticator(["secret"]))
    socket = _Socket()

    assert await ingress.authorise("a", {"token": "secret"}, socket) is True
    assert rec.sent == []
    assert socket.closed == []


async def test_authorise_refuses_and_closes_on_a_wrong_token() -> None:
    ingress, rec = _ingress(_registry(), authenticator=TokenAuthenticator(["secret"]))
    socket = _Socket()

    assert await ingress.authorise("a", {"token": "wrong"}, socket) is False
    assert len(rec.sent) == 1
    _, frame = rec.sent[0]
    assert frame["type"] == MessageType.AUTH_DENIED
    assert frame["target"] == "a"
    assert socket.closed == [(4010, "auth denied")]


async def test_authorise_treats_a_missing_token_as_empty() -> None:
    ingress, rec = _ingress(_registry(), authenticator=TokenAuthenticator(["secret"]))
    socket = _Socket()

    assert await ingress.authorise("a", {}, socket) is False
    assert rec.sent[0][1]["type"] == MessageType.AUTH_DENIED
    assert socket.closed == [(4010, "auth denied")]


# -- resolve_sender ----------------------------------------------------------


async def test_resolve_sender_binds_a_fresh_socket() -> None:
    clients = _registry()
    ingress, rec = _ingress(clients)
    socket = _Socket()

    assert await ingress.resolve_sender("a", socket) == "a"
    assert clients.socket_agent[socket] == "a"
    assert rec.sent == []


async def test_resolve_sender_refuses_a_name_conflict() -> None:
    clients = _registry()
    holder = _Socket()
    clients.agent_sockets["a"] = holder
    ingress, rec = _ingress(clients)
    newcomer = _Socket()

    assert await ingress.resolve_sender("a", newcomer) is None
    assert newcomer.closed == [(4009, "name conflict")]
    assert rec.sent[0][1]["type"] == "name_conflict"


async def test_resolve_sender_takeover_evicts_the_stale_holder() -> None:
    clients = _registry()
    holder = _Socket()
    clients.agent_sockets["a"] = holder
    clients.socket_agent[holder] = "a"
    ingress, _ = _ingress(clients)
    newcomer = _Socket()

    assert await ingress.resolve_sender("a", newcomer, takeover=True) == "a"
    assert holder.closed == [(4010, "superseded")]
    assert clients.socket_agent[newcomer] == "a"


# -- exposure_problems -------------------------------------------------------


def test_exposure_problems_empty_on_loopback() -> None:
    ingress, _ = _ingress(_registry())
    assert ingress.exposure_problems("localhost") == []
    assert ingress.exposure_problems("127.0.0.1") == []


def test_exposure_problems_flags_missing_token_off_loopback() -> None:
    ingress, _ = _ingress(_registry())
    problems = ingress.exposure_problems("0.0.0.0")
    assert len(problems) == 1
    assert "no token" in problems[0]


def test_exposure_problems_empty_when_token_set() -> None:
    ingress, _ = _ingress(_registry(), authenticator=TokenAuthenticator(["t"]))
    assert ingress.exposure_problems("0.0.0.0") == []


def test_exposure_problems_flags_metrics_query_token_off_loopback() -> None:
    ingress, _ = _ingress(
        _registry(),
        authenticator=TokenAuthenticator(["t"]),
        enable_metrics=True,
        metrics_token="m",
        metrics_query_token_ok=True,
    )
    problems = ingress.exposure_problems("0.0.0.0")
    assert any("query-string token" in problem for problem in problems)


# -- guard_exposure ----------------------------------------------------------


def test_guard_exposure_refuses_off_loopback_without_token() -> None:
    ingress, _ = _ingress(_registry())
    with pytest.raises(InsecureBindError, match="Refusing to bind"):
        ingress.guard_exposure("0.0.0.0")


def test_guard_exposure_silent_on_loopback(caplog: pytest.LogCaptureFixture) -> None:
    ingress, _ = _ingress(_registry())
    with caplog.at_level("WARNING", logger="synapse.hub"):
        ingress.guard_exposure("localhost")
    assert caplog.records == []


def test_guard_exposure_warns_instead_of_refusing_when_overridden(
    caplog: pytest.LogCaptureFixture,
) -> None:
    ingress, _ = _ingress(_registry(), insecure_off_loopback=True)
    with caplog.at_level("WARNING", logger="synapse.hub"):
        ingress.guard_exposure("0.0.0.0")
    assert "no token" in caplog.text


def test_guard_exposure_refuses_unauthenticated_metrics_off_loopback() -> None:
    ingress, _ = _ingress(_registry(), authenticator=TokenAuthenticator(["t"]), enable_metrics=True)
    with pytest.raises(InsecureBindError, match="metrics"):
        ingress.guard_exposure("0.0.0.0")


# -- close_socket (static) ---------------------------------------------------


async def test_close_socket_closes_and_waits_when_supported() -> None:
    socket = _Socket(wait_closed=True)
    await HubIngress.close_socket(socket, code=4000, reason="bye")
    assert socket.closed == [(4000, "bye")]
    assert socket.waited == 1


async def test_close_socket_closes_without_wait_closed() -> None:
    socket = _Socket(wait_closed=False)
    await HubIngress.close_socket(socket, code=4001, reason="bye")
    assert socket.closed == [(4001, "bye")]
    assert socket.waited == 0


async def test_close_socket_is_best_effort_on_a_raising_socket() -> None:
    # A socket that is already gone raises on close; close_socket swallows it.
    await HubIngress.close_socket(_RaisingSocket(), code=4002, reason="bye")


# -- remote_host (static) ----------------------------------------------------


def test_remote_host_reads_a_tuple_address() -> None:
    assert HubIngress.remote_host(_Socket(("1.2.3.4", 9))) == "1.2.3.4"


def test_remote_host_reads_a_bare_address() -> None:
    assert HubIngress.remote_host(_Socket("sock-path")) == "sock-path"


def test_remote_host_falls_back_to_unknown() -> None:
    assert HubIngress.remote_host(_Socket(None)) == "unknown"
    assert HubIngress.remote_host(object()) == "unknown"
