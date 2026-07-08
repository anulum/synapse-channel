# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL - unit tests for hub client connection accounting

from __future__ import annotations

import logging
from typing import Any, cast

import pytest

from synapse_channel.core.hub_clients import HubClientRegistry
from synapse_channel.core.wake_capability import WAKE_DIRECT, WAKE_UNKNOWN


class _Socket:
    def __init__(self, remote_address: object = ("127.0.0.1", 8876)) -> None:
        self.remote_address = remote_address
        self.close_calls: list[tuple[int, str]] = []

    async def close(self, *, code: int, reason: str) -> None:
        self.close_calls.append((code, reason))


class _WaitableSocket(_Socket):
    def __init__(self, remote_address: object = ("127.0.0.1", 8876)) -> None:
        super().__init__(remote_address)
        self.wait_closed_calls = 0

    async def wait_closed(self) -> None:
        self.wait_closed_calls += 1


class _FailingCloseSocket(_Socket):
    async def close(self, *, code: int, reason: str) -> None:
        raise RuntimeError(f"already closed: {code} {reason}")


def _registry(
    *,
    max_clients: int = 3,
    max_unauth_clients: int | None = 2,
    max_connections_per_host: int | None = 2,
    takeover_cooldown: float = 1.0,
) -> HubClientRegistry:
    return HubClientRegistry(
        max_clients=max_clients,
        max_unauth_clients=max_unauth_clients,
        max_connections_per_host=max_connections_per_host,
        takeover_cooldown=takeover_cooldown,
        clock=lambda: 100.0,
    )


def test_capacity_predicates_clamp_limits_and_track_unauthenticated_clients() -> None:
    registry = _registry(max_clients=0, max_unauth_clients=0)
    socket = _Socket()

    assert not registry.at_capacity()
    assert not registry.unauthenticated_at_capacity()

    registry.add_client(socket)
    registry.add_unauthenticated(socket)

    assert registry.at_capacity()
    assert registry.unauthenticated_at_capacity()

    registry.discard_unauthenticated(socket)

    assert not registry.unauthenticated_at_capacity()


def test_capacity_predicates_reject_non_finite_limits() -> None:
    registry = _registry(
        max_clients=cast(int, float("inf")),
        max_unauth_clients=cast(int, float("nan")),
        max_connections_per_host=cast(int, float("inf")),
    )
    socket = _Socket()

    assert registry.max_clients == 1
    assert registry.max_unauth_clients == 1
    assert registry.max_connections_per_host == 1
    assert not registry.at_capacity()
    registry.add_client(socket)
    assert registry.at_capacity()


def test_host_capacity_can_be_disabled_or_enforced_per_remote_host() -> None:
    disabled_registry = _registry(max_connections_per_host=None)
    disabled_socket = _Socket(("10.0.0.1", 5000))

    assert not disabled_registry.host_at_capacity(disabled_socket)

    registry = _registry(max_connections_per_host=2)
    first = _Socket(("10.0.0.2", 5000))
    second = _Socket(("10.0.0.2", 5001))

    assert not registry.host_at_capacity(first)

    registry.add_client(first)
    assert not registry.host_at_capacity(second)

    registry.add_client(second)
    assert registry.host_at_capacity(first)

    assert registry.drop_client(first) is None
    assert not registry.host_at_capacity(second)

    assert registry.drop_client(second) is None
    assert not registry.host_at_capacity(first)


def test_drop_client_removes_only_active_agent_bindings() -> None:
    registry = _registry()
    never_added_socket = _Socket()
    old_socket = _Socket()
    current_socket = _Socket()

    assert registry.drop_client(never_added_socket) is None

    registry.add_client(old_socket)
    registry.add_client(current_socket)
    registry.socket_agent[old_socket] = "agent"
    registry.agent_sockets["agent"] = current_socket

    assert registry.drop_client(old_socket) is None
    assert registry.agent_sockets["agent"] is current_socket

    registry.socket_agent[current_socket] = "agent"

    assert registry.drop_client(current_socket) == "agent"


def test_drop_client_releases_wake_capability_binding() -> None:
    registry = _registry()
    socket = _Socket()
    registry.add_client(socket)
    registry.socket_agent[socket] = "agent"
    registry.agent_sockets["agent"] = socket
    registry.set_wake_capability("agent", WAKE_DIRECT)

    assert registry.wake_capability_of("agent") == WAKE_DIRECT
    assert registry.drop_client(socket) == "agent"
    assert registry.wake_capability_of("agent") == WAKE_UNKNOWN


def test_unknown_wake_capability_is_not_bound() -> None:
    registry = _registry()

    registry.set_wake_capability("agent", "nonsense")

    assert registry.wake_capability_of("agent") == WAKE_UNKNOWN


def test_set_roles_binds_replaces_and_clears() -> None:
    registry = _registry()
    assert registry.roles_of("agent") == ()

    registry.set_roles("agent", ("proj/coordinator", "proj/git"))
    assert registry.roles_of("agent") == ("proj/coordinator", "proj/git")

    # a new set replaces the previous roles rather than merging
    registry.set_roles("agent", ("proj/reviewer",))
    assert registry.roles_of("agent") == ("proj/reviewer",)

    # an empty set clears the binding entirely
    registry.set_roles("agent", ())
    assert registry.roles_of("agent") == ()
    assert "agent" not in registry.agent_roles


def test_drop_client_releases_role_bindings() -> None:
    registry = _registry()
    socket = _Socket()
    registry.add_client(socket)
    registry.socket_agent[socket] = "agent"
    registry.agent_sockets["agent"] = socket
    registry.set_roles("agent", ("proj/coordinator",))

    assert registry.roles_of("agent") == ("proj/coordinator",)
    assert registry.drop_client(socket) == "agent"
    # the role binding is released together with the socket
    assert registry.roles_of("agent") == ()
    assert "agent" not in registry.agent_sockets


def test_agent_binding_helpers_report_existing_socket_state() -> None:
    registry = _registry()
    socket = _Socket()

    assert not registry.is_bound(socket)
    assert registry.bound_agent(socket) is None
    assert registry.set_agent_socket("agent", socket)

    registry.socket_agent[socket] = "agent"

    assert registry.is_bound(socket)
    assert registry.bound_agent(socket) == "agent"
    assert not registry.set_agent_socket("agent", socket)


async def test_resolve_sender_denies_name_switch_and_reports_system_message() -> None:
    registry = _registry()
    socket = _Socket()
    sent_messages: list[dict[str, Any]] = []

    async def send_json(_websocket: Any, message: dict[str, Any]) -> None:
        sent_messages.append(message)

    def system(payload: str, *, msg_type: str, target: str) -> dict[str, Any]:
        return {"payload": payload, "target": target, "type": msg_type}

    registry.socket_agent[socket] = "agent-a"

    assert (
        await registry.resolve_sender(
            "agent-b",
            socket,
            takeover=False,
            send_json=send_json,
            system=system,
        )
        is None
    )

    assert socket.close_calls == [(4009, "name switch")]
    assert sent_messages == [
        {
            "payload": "Sender name switch denied: 'agent-a' -> 'agent-b'. "
            "Reconnect with a new --name.",
            "target": "agent-a",
            "type": "name_conflict",
        }
    ]


async def test_close_socket_waits_when_supported_and_ignores_close_errors() -> None:
    waitable = _WaitableSocket()
    plain = _Socket()

    await HubClientRegistry.close_socket(waitable, code=4000, reason="done")
    await HubClientRegistry.close_socket(plain, code=4001, reason="plain")
    await HubClientRegistry.close_socket(_FailingCloseSocket(), code=4002, reason="already gone")

    assert waitable.close_calls == [(4000, "done")]
    assert waitable.wait_closed_calls == 1
    assert plain.close_calls == [(4001, "plain")]


def test_remote_host_normalises_supported_address_shapes() -> None:
    assert HubClientRegistry.remote_host(_Socket(("1.2.3.4", 8876))) == "1.2.3.4"
    assert HubClientRegistry.remote_host(_Socket(["5.6.7.8", 8876])) == "5.6.7.8"
    assert HubClientRegistry.remote_host(_Socket("unix-socket")) == "unix-socket"
    assert HubClientRegistry.remote_host(_Socket(None)) == "unknown"
    assert HubClientRegistry.remote_host(object()) == "unknown"


# ---------- takeover oscillation / quarantine ----------


class _Clock:
    """A mutable clock for driving takeover timing."""

    def __init__(self, start: float = 100.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


def _osc_registry(clock: _Clock) -> HubClientRegistry:
    return HubClientRegistry(
        max_clients=100,
        max_unauth_clients=None,
        max_connections_per_host=None,
        takeover_cooldown=1.0,
        clock=clock,
        takeover_oscillation_window=30.0,
        takeover_oscillation_threshold=3,
        takeover_quarantine=20.0,
    )


async def _noop_send(_ws: Any, _msg: dict[str, Any]) -> None:
    return None


def _system(payload: str, *, msg_type: str, target: str) -> dict[str, Any]:
    return {"payload": payload, "target": target, "type": msg_type}


async def _drive_takeover(
    registry: HubClientRegistry, name: str, at: float, clock: _Clock
) -> tuple[_Socket, _Socket, str | None]:
    """Set ``name``'s current owner, then attempt a takeover by a fresh challenger."""
    clock.now = at
    owner = _Socket()
    challenger = _Socket()
    registry.agent_sockets[name] = owner
    registry.socket_agent[owner] = name
    result = await registry.resolve_sender(
        name, challenger, takeover=True, send_json=_noop_send, system=_system
    )
    return owner, challenger, result


def test_classify_takeover_trips_quarantine_then_lapses() -> None:
    registry = _osc_registry(_Clock())
    # threshold is 3 within a 30s window; the first two are accepted
    assert registry._classify_takeover("w/rx", 100.0) == "accept"
    assert registry._classify_takeover("w/rx", 102.0) == "accept"
    # the third trip-wires the oscillation guard and pins the owner
    assert registry._classify_takeover("w/rx", 104.0) == "quarantine_enter"
    # further attempts during the 20s quarantine are refused without eviction
    assert registry._classify_takeover("w/rx", 104.5) == "quarantine_active"
    assert registry._classify_takeover("w/rx", 123.9) == "quarantine_active"
    # once quarantine lapses the history resets and takeovers are accepted again
    assert registry._classify_takeover("w/rx", 124.0) == "accept"


async def test_resolve_sender_pins_owner_when_takeover_oscillates(
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = _Clock()
    registry = _osc_registry(clock)
    name = "user/terminal-x-rx"

    owner1, chal1, r1 = await _drive_takeover(registry, name, 100.0, clock)
    owner2, chal2, r2 = await _drive_takeover(registry, name, 102.0, clock)
    # first two takeovers are accepted: each evicts the prior owner (4010 superseded)
    assert r1 == name and owner1.close_calls == [(4010, "superseded")]
    assert r2 == name and owner2.close_calls == [(4010, "superseded")]

    with caplog.at_level(logging.WARNING, logger="synapse.hub"):
        owner3, chal3, r3 = await _drive_takeover(registry, name, 104.0, clock)
    # the third trips quarantine: the challenger is refused, the OWNER is left in place
    assert r3 is None
    assert chal3.close_calls == [(4014, "takeover quarantine")]
    assert owner3.close_calls == []  # owner pinned, not evicted — the war ends here
    assert sum("quarantine" in rec.message for rec in caplog.records) == 1

    # a subsequent attempt during quarantine is refused the same way, owner still pinned
    owner4, chal4, r4 = await _drive_takeover(registry, name, 105.0, clock)
    assert r4 is None
    assert chal4.close_calls == [(4014, "takeover quarantine")]
    assert owner4.close_calls == []


async def test_resolve_sender_still_enforces_the_short_cooldown() -> None:
    clock = _Clock()
    registry = _osc_registry(clock)
    name = "w/rx"

    _owner1, _chal1, r1 = await _drive_takeover(registry, name, 100.0, clock)
    assert r1 == name
    # a second takeover within the 1s cooldown is refused as cooldown, not quarantine
    owner2, chal2, r2 = await _drive_takeover(registry, name, 100.5, clock)
    assert r2 is None
    assert chal2.close_calls == [(4014, "takeover cooldown")]
    assert owner2.close_calls == []


async def test_resolve_sender_reports_name_conflict_without_takeover() -> None:
    registry = _osc_registry(_Clock())
    name = "w/rx"
    owner = _Socket()
    challenger = _Socket()
    registry.agent_sockets[name] = owner
    registry.socket_agent[owner] = name
    sent: list[dict[str, Any]] = []

    async def send_json(_ws: Any, message: dict[str, Any]) -> None:
        sent.append(message)

    result = await registry.resolve_sender(
        name, challenger, takeover=False, send_json=send_json, system=_system
    )
    assert result is None
    assert challenger.close_calls == [(4009, "name conflict")]
    assert owner.close_calls == []  # the live owner is never disturbed
    assert sent and sent[0]["type"] == "name_conflict"


async def test_resolve_sender_returns_the_same_name_for_an_already_bound_socket() -> None:
    registry = _osc_registry(_Clock())
    socket = _Socket()
    registry.socket_agent[socket] = "w/rx"
    result = await registry.resolve_sender(
        "w/rx", socket, takeover=False, send_json=_noop_send, system=_system
    )
    assert result == "w/rx"
    assert socket.close_calls == []


async def test_resolve_sender_binds_a_free_name_for_a_new_socket() -> None:
    registry = _osc_registry(_Clock())
    socket = _Socket()
    result = await registry.resolve_sender(
        "w/rx", socket, takeover=False, send_json=_noop_send, system=_system
    )
    assert result == "w/rx"
    assert registry.socket_agent[socket] == "w/rx"
    assert socket.close_calls == []


async def test_hub_close_socket_wrapper_delegates_to_the_registry() -> None:
    """The hub's static close wrapper drives the registry's best-effort close."""
    from synapse_channel.core.hub import SynapseHub

    calls: list[tuple[int, str]] = []

    class _Socket:
        async def close(self, code: int, reason: str) -> None:
            calls.append((code, reason))

    await SynapseHub._close_socket(_Socket(), code=4000, reason="test close")
    assert calls == [(4000, "test close")]
