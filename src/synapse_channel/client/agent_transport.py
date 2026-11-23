# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the socket, the listener and the close diagnostics
"""The socket one agent attaches, and the listener state around it."""

from __future__ import annotations

import asyncio

from websockets.asyncio.client import ClientConnection

MINIMUM_HEARTBEAT_INTERVAL = 5.0
"""Floor applied to the configured heartbeat interval, in seconds."""


class AgentTransport:
    """Own the socket, the listener flag and the close diagnostics.

    The constructor keywords are the ones ``SynapseAgent`` already accepts.
    A connection attempt attaches a socket and detaches it when the attempt
    ends. The close code and reason belong to that attempt.
    """

    def __init__(
        self,
        *,
        uri: str,
        token: str | None,
        heartbeat_interval: float,
        ping_interval: float,
        ping_timeout: float,
    ) -> None:
        self.uri = uri
        self.token = token
        self.connection: ClientConnection | None = None
        self.heartbeat_interval = max(float(heartbeat_interval), MINIMUM_HEARTBEAT_INTERVAL)
        self.ping_interval = float(ping_interval)
        self.ping_timeout = float(ping_timeout)
        self.ready_event = asyncio.Event()
        self.running = True
        self.last_close_code: int | None = None
        self.last_close_reason = ""
        self._connect_active = False
        self._heartbeat_task: asyncio.Task[None] | None = None

    def start_attempt(self, name: str) -> None:
        """Refuse a second connect while this one has not returned."""
        if self._connect_active:
            raise RuntimeError(f"{name}: connect() is already active; await it first")
        self._connect_active = True

    def reset_attempt(self) -> None:
        """Re-arm the listener and clear the previous close diagnostics.

        The socket, if one is still attached, stays until the attempt detaches
        it. Readiness belongs to the attempt that is about to start.
        """
        self.running = True
        self.ready_event.clear()
        self.last_close_code = None
        self.last_close_reason = ""

    def attach(self, connection: ClientConnection) -> None:
        """Remember the socket this attempt is reading."""
        self.connection = connection

    def bind_heartbeat(self, task: asyncio.Task[None]) -> None:
        """Remember the keepalive task so a later attempt can tell it apart."""
        self._heartbeat_task = task

    def record_close(self, code: int | None, reason: str) -> None:
        """Store the close code and reason from the socket that just ended."""
        self.last_close_code = code
        self.last_close_reason = reason

    def close_listener(self) -> None:
        """Stop the listener, clear readiness and detach the socket.

        The connect flag stays set until the keepalive task has been awaited,
        so a second connect cannot start in between.
        """
        self.running = False
        self.ready_event.clear()
        self.connection = None

    def release_attempt(self) -> None:
        """Allow a later connect once this attempt has finished its keepalive."""
        self._connect_active = False
