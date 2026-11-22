# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — connection limits and per-host counts
"""How many sockets the hub will admit, in total and from one host."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from synapse_channel.core.numeric_coercion import safe_int


class ConnectionCapacity:
    """Own the admission limits and the host count of each live socket.

    The host is read once, through the registry's ``remote_host``, when the
    socket connects. A later drop uses that recorded host. A second drop of
    the same socket finds nothing to decrement.
    """

    def __init__(
        self,
        *,
        max_clients: int,
        max_unauth_clients: int | None,
        max_connections_per_host: int | None,
        remote_host: Callable[[Any], str],
    ) -> None:
        self._max_clients = safe_int(max_clients, default=1, min_value=1)
        self._max_unauth_clients = (
            self._max_clients
            if max_unauth_clients is None
            else safe_int(max_unauth_clients, default=self._max_clients, min_value=1)
        )
        self._max_connections_per_host = (
            None
            if max_connections_per_host is None
            else safe_int(max_connections_per_host, default=1, min_value=1)
        )
        self._remote_host = remote_host
        self._socket_hosts: dict[Any, str] = {}
        self._host_counts: dict[str, int] = {}

    @property
    def max_clients(self) -> int:
        """Total live sockets the hub will hold."""
        return self._max_clients

    @property
    def max_unauth_clients(self) -> int:
        """Sockets allowed to sit in the pre-authentication window."""
        return self._max_unauth_clients

    @property
    def max_connections_per_host(self) -> int | None:
        """Sockets allowed from one remote host, or ``None`` when uncapped."""
        return self._max_connections_per_host

    def host_count(self, host: str) -> int:
        """Return the live sockets recorded for ``host``."""
        return self._host_counts.get(host, 0)

    def note_connected(self, websocket: Any) -> None:
        """Record ``websocket`` under the host read once at connect time."""
        host = self._remote_host(websocket)
        self._socket_hosts[websocket] = host
        self._host_counts[host] = self._host_counts.get(host, 0) + 1

    def note_disconnected(self, websocket: Any) -> None:
        """Drop ``websocket``'s recorded host. A second call does nothing."""
        host = self._socket_hosts.pop(websocket, None)
        if host is None:
            return
        remaining = self._host_counts.get(host, 0) - 1
        if remaining > 0:
            self._host_counts[host] = remaining
        else:
            self._host_counts.pop(host, None)

    def host_at_capacity(self, websocket: Any) -> bool:
        """Return whether ``websocket``'s current host already holds its cap."""
        if self._max_connections_per_host is None:
            return False
        host = self._remote_host(websocket)
        return self._host_counts.get(host, 0) >= self._max_connections_per_host
