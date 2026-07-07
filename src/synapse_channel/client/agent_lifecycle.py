# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — connection lifecycle helpers for the reusable client
"""Connection lifecycle helpers for :class:`synapse_channel.client.agent.SynapseAgent`."""

from __future__ import annotations

import asyncio
import errno
import os
from typing import Any, Protocol

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosedError

from synapse_channel.core.protocol import MessageType

DEFAULT_HUB_URI = "ws://localhost:8876"
"""Default hub URI; matches the hub's default bind port."""

HUB_URI_ENV_VAR = "SYNAPSE_URI"
"""Environment variable that overrides the default hub URI for the CLI."""

MINIMUM_HEARTBEAT_INTERVAL = 5.0
"""Floor applied to the configured heartbeat interval, in seconds."""


def default_hub_uri() -> str:
    """Return the hub URI a command should use when ``--uri`` is not given.

    Reads the ``SYNAPSE_URI`` environment variable so an operator can point the
    whole CLI at a non-default hub — a remote coordinator, a second local hub on
    another port — without repeating ``--uri`` on every command. A blank or unset
    variable falls back to :data:`DEFAULT_HUB_URI`, the loopback hub. Resolved
    each time a parser is built, so every fresh CLI process reads the current
    environment; an explicit ``--uri`` on the command line still wins.
    """
    override = os.environ.get(HUB_URI_ENV_VAR, "").strip()
    return override or DEFAULT_HUB_URI


def _is_connection_refused(exc: OSError) -> bool:
    """Return whether an OS connection error is a refused hub connection."""
    if isinstance(exc, ConnectionRefusedError):
        return True
    if exc.errno == errno.ECONNREFUSED:
        return True
    text = str(exc)
    return "Connect call failed" in text and f"[Errno {errno.ECONNREFUSED}]" in text


def _received_close(exc: ConnectionClosedError) -> tuple[int | None, str]:
    """Return the close code and reason the hub sent, if any.

    Reads the received Close frame (``exc.rcvd``) rather than the deprecated
    ``exc.code``/``exc.reason`` shortcuts. ``exc.rcvd`` is ``None`` when this side
    initiated the close, in which case there is no hub-supplied code to report.
    """
    received = getattr(exc, "rcvd", None)
    if received is None:
        return None, ""
    code = getattr(received, "code", None)
    reason = str(getattr(received, "reason", "") or "")
    return code, reason


class _LifecycleAgent(Protocol):
    """Attributes and helper methods required by the lifecycle mixin."""

    connection: ClientConnection | None
    heartbeat_interval: float
    last_close_code: int | None
    last_close_reason: str
    name: str
    ping_interval: float
    ping_timeout: float
    ready_event: Any
    roles: tuple[str, ...]
    running: bool
    takeover: bool
    token: str | None
    uri: str
    verbose: bool
    _heartbeat_task: asyncio.Task[None] | None

    async def _dispatch(self, raw: str | bytes) -> None:
        """Dispatch a raw frame received from the hub."""

    async def _heartbeat_loop(self) -> None:
        """Run the periodic heartbeat loop."""

    async def _heartbeat_tick(self) -> None:
        """Send one heartbeat tick if connected."""

    async def connect(self) -> None:
        """Connect to the hub and process frames until disconnected."""

    async def send_message(
        self,
        msg_type: str,
        *,
        target: str = "all",
        payload: str = "",
        **extra: Any,
    ) -> None:
        """Send one message envelope to the hub."""


class AgentLifecycleMixin:
    """Manage WebSocket connection setup, readiness, heartbeat, and shutdown."""

    connection: ClientConnection | None
    _heartbeat_task: asyncio.Task[None] | None

    async def connect(self: _LifecycleAgent) -> None:
        """Open the connection and run the inbound listener until it closes.

        Sends the registration heartbeat, starts the keepalive loop, then
        dispatches each inbound message to the callback. Connection failures are
        reported (when verbose) and end the loop; the heartbeat task is always
        cancelled on exit.
        """
        try:
            async with connect(
                self.uri, ping_interval=self.ping_interval, ping_timeout=self.ping_timeout
            ) as websocket:
                self.connection = websocket
                if self.verbose:
                    print(f"[{self.name}] Online and connected to Synapse.")
                # Register identity immediately so presence and /who are accurate
                # before the first user-issued command. The token (if any) rides
                # this first message, which is where the hub gates authentication;
                # ``takeover`` asks the hub to evict a stale holder of this name.
                extra: dict[str, Any] = {}
                if self.token:
                    extra["token"] = self.token
                if self.takeover:
                    extra["takeover"] = True
                if self.roles:
                    # Declare the roles this identity answers to so the hub binds them
                    # to this socket: /who shows them and a directed message to a role
                    # is delivered to its holder instead of counted a dead letter.
                    extra["roles"] = list(self.roles)
                await self.send_message(
                    MessageType.HEARTBEAT, target="System", payload="online", **extra
                )
                self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

                async for raw in websocket:
                    if not self.running:
                        break
                    await self._dispatch(raw)
        except ConnectionRefusedError:
            if self.verbose:
                print(f"[{self.name}] Error: could not connect. Is the hub running?")
        except OSError as exc:
            if _is_connection_refused(exc):
                if self.verbose:
                    print(f"[{self.name}] Error: could not connect. Is the hub running?")
            elif self.verbose:
                print(f"[{self.name}] Connection lost: {exc}")
        except ConnectionClosedError as exc:
            self.last_close_code, self.last_close_reason = _received_close(exc)
            if self.verbose:
                print(f"[{self.name}] Connection lost: {exc}")
        finally:
            self.running = False
            if self._heartbeat_task is not None:
                self._heartbeat_task.cancel()
            self.connection = None

    async def wait_until_ready(self: _LifecycleAgent, timeout: float = 5.0) -> bool:
        """Wait until the hub's welcome message has been received.

        Parameters
        ----------
        timeout : float, optional
            Maximum seconds to wait, floored at ``0.1``. Defaults to ``5.0``.

        Returns
        -------
        bool
            ``True`` if the welcome arrived in time, ``False`` on timeout.
        """
        try:
            await asyncio.wait_for(self.ready_event.wait(), timeout=max(timeout, 0.1))
            return True
        except asyncio.TimeoutError:
            # On Python 3.10 asyncio.wait_for raises asyncio.TimeoutError, which is
            # not the builtin TimeoutError (the two are only aliased on 3.11+).
            return False

    async def _heartbeat_loop(self: _LifecycleAgent) -> None:
        """Send a keepalive heartbeat every ``heartbeat_interval`` seconds."""
        while self.running:
            await asyncio.sleep(self.heartbeat_interval)
            await self._heartbeat_tick()

    async def _heartbeat_tick(self: _LifecycleAgent) -> None:
        """Send one keepalive heartbeat if the connection is open."""
        if self.connection is None:
            return
        await self.send_message(MessageType.HEARTBEAT, target="System", payload="alive")

    def start(self: _LifecycleAgent) -> None:
        """Run :meth:`connect` to completion on a fresh event loop.

        Intended as a blocking entry point for scripts. ``Ctrl+C`` is caught and
        reported instead of raising.
        """
        try:
            asyncio.run(self.connect())
        except KeyboardInterrupt:
            if self.verbose:
                print(f"\n[{self.name}] Shutting down.")
