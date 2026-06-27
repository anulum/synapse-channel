# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL - unit tests for hub client connection accounting

from __future__ import annotations

from typing import Any

from synapse_channel.core.hub_clients import HubClientRegistry


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
