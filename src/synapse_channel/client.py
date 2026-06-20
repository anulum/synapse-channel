# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — reusable async WebSocket client for joining the hub
"""Reusable asynchronous agent client for the Synapse hub.

:class:`SynapseAgent` wraps a single WebSocket connection to the hub: it sends
the registration heartbeat, keeps the connection alive with periodic
heartbeats, forwards every inbound message to a user callback, and exposes
typed helpers for the coordination verbs (chat, claim, release, and the
``state``/``who``/``history`` queries). It is the building block the worker,
the CLI, and any embedding application use to appear on the channel.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosedError

from synapse_channel.protocol import MessageType, build_envelope

logging.basicConfig(level=logging.ERROR)

MessageCallback = Callable[[dict[str, Any]], Awaitable[None]]
"""Async callback invoked with each decoded inbound message."""

DEFAULT_HUB_URI = "ws://localhost:8876"
"""Default hub URI; matches the hub's default bind port."""

MINIMUM_HEARTBEAT_INTERVAL = 5.0
"""Floor applied to the configured heartbeat interval, in seconds."""


class SynapseAgent:
    """An async client that maintains one connection to the Synapse hub.

    Parameters
    ----------
    name : str
        Unique agent name presented to the hub.
    on_message_callback : MessageCallback or None, optional
        Coroutine called with every decoded inbound message. Self-originated
        chat echoes are filtered out before the callback runs.
    uri : str, optional
        Hub WebSocket URI. Defaults to :data:`DEFAULT_HUB_URI`.
    heartbeat_interval : float, optional
        Seconds between keepalive heartbeats, clamped up to
        :data:`MINIMUM_HEARTBEAT_INTERVAL`. Defaults to ``20.0``.
    verbose : bool, optional
        When ``True``, connection lifecycle notes are printed. Defaults to ``True``.
    """

    def __init__(
        self,
        name: str,
        on_message_callback: MessageCallback | None = None,
        *,
        uri: str = DEFAULT_HUB_URI,
        heartbeat_interval: float = 20.0,
        verbose: bool = True,
    ) -> None:
        self.name = name
        self.uri = uri
        self.connection: ClientConnection | None = None
        self.callback = on_message_callback
        self.running = True
        self.heartbeat_interval = max(float(heartbeat_interval), MINIMUM_HEARTBEAT_INTERVAL)
        self._heartbeat_task: asyncio.Task[None] | None = None
        self.ready_event = asyncio.Event()
        self.hub_id = "unknown"
        self.verbose = bool(verbose)

    async def connect(self) -> None:
        """Open the connection and run the inbound listener until it closes.

        Sends the registration heartbeat, starts the keepalive loop, then
        dispatches each inbound message to the callback. Connection failures are
        reported (when verbose) and end the loop; the heartbeat task is always
        cancelled on exit.
        """
        try:
            async with connect(self.uri) as websocket:
                self.connection = websocket
                if self.verbose:
                    print(f"[{self.name}] Online and connected to Synapse.")
                # Register identity immediately so presence and /who are accurate
                # before the first user-issued command.
                await self.send_message(MessageType.HEARTBEAT, target="System", payload="online")
                self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

                async for raw in websocket:
                    if not self.running:
                        break
                    await self._dispatch(raw)
        except ConnectionRefusedError:
            if self.verbose:
                print(f"[{self.name}] Error: could not connect. Is the hub running?")
        except (ConnectionResetError, OSError, ConnectionClosedError) as exc:
            print(f"[{self.name}] Connection lost: {exc}")
        finally:
            self.running = False
            if self._heartbeat_task is not None:
                self._heartbeat_task.cancel()
            self.connection = None

    async def _dispatch(self, raw: str | bytes) -> None:
        """Decode one raw frame and forward it to the callback.

        Parameters
        ----------
        raw : str or bytes
            The raw WebSocket frame received from the hub.
        """
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            if self.verbose:
                print(f"[{self.name}] Received malformed JSON from hub.")
            return

        if data.get("type") == MessageType.WELCOME:
            self.hub_id = str(data.get("hub_id", "unknown"))
            self.ready_event.set()

        # Ignore our own chat echoes, but still process system replies.
        if data.get("sender") == self.name and data.get("type") == MessageType.CHAT:
            return
        if self.callback is not None:
            await self.callback(data)

    async def wait_until_ready(self, timeout: float = 5.0) -> bool:
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
        except TimeoutError:
            return False

    async def _heartbeat_loop(self) -> None:
        """Send a keepalive heartbeat every ``heartbeat_interval`` seconds."""
        while self.running:
            await asyncio.sleep(self.heartbeat_interval)
            await self._heartbeat_tick()

    async def _heartbeat_tick(self) -> None:
        """Send one keepalive heartbeat if the connection is open."""
        if self.connection is None:
            return
        await self.send_message(MessageType.HEARTBEAT, target="System", payload="alive")

    async def send_message(
        self,
        msg_type: str,
        *,
        target: str = "all",
        payload: str = "",
        **extra: Any,
    ) -> None:
        """Serialise and send one message envelope to the hub.

        Parameters
        ----------
        msg_type : str
            One of the :class:`~synapse_channel.protocol.MessageType` constants.
        target : str, optional
            Recipient agent name, or ``"all"``. Defaults to ``"all"``.
        payload : str, optional
            Free-form text body.
        **extra : Any
            Additional protocol fields merged into the envelope.
        """
        if self.connection is None:
            return
        msg = build_envelope(self.name, msg_type, target=target, payload=payload, **extra)
        await self.connection.send(json.dumps(msg))

    async def chat(self, payload: str, *, target: str = "all") -> None:
        """Send a chat message to the room or a single agent.

        Parameters
        ----------
        payload : str
            Message text.
        target : str, optional
            Recipient agent name, or ``"all"``. Defaults to ``"all"``.
        """
        await self.send_message(MessageType.CHAT, target=target, payload=payload)

    async def claim(
        self,
        task_id: str,
        note: str = "",
        ttl_seconds: float | None = None,
        *,
        worktree: str = "",
        paths: tuple[str, ...] | list[str] = (),
    ) -> None:
        """Request a scoped lease on a task.

        Parameters
        ----------
        task_id : str
            Task identifier; surrounding whitespace is stripped.
        note : str, optional
            Human-readable context stored with the claim.
        ttl_seconds : float or None, optional
            Requested lease duration; ``None`` lets the hub apply its default.
        worktree : str, optional
            Worktree label; claims in different worktrees never contend for files.
        paths : tuple[str, ...] or list[str], optional
            Declared file/directory paths the claim intends to touch; empty claims
            the whole worktree.
        """
        extra: dict[str, Any] = {"task_id": task_id.strip(), "note": note}
        if ttl_seconds is not None:
            extra["ttl_seconds"] = float(ttl_seconds)
        if worktree:
            extra["worktree"] = worktree
        if paths:
            extra["paths"] = list(paths)
        await self.send_message(
            MessageType.CLAIM, target="System", payload=task_id.strip(), **extra
        )

    async def release(self, task_id: str, *, epoch: int | None = None) -> None:
        """Release a task lease.

        Parameters
        ----------
        task_id : str
            Task identifier; surrounding whitespace is stripped.
        epoch : int or None, optional
            Expected lease generation; when given, the hub refuses the release if
            the lease has since been superseded.
        """
        extra: dict[str, Any] = {"task_id": task_id.strip()}
        if epoch is not None:
            extra["epoch"] = int(epoch)
        await self.send_message(
            MessageType.RELEASE, target="System", payload=task_id.strip(), **extra
        )

    async def request_state(self) -> None:
        """Ask the hub for a full state snapshot."""
        await self.send_message(MessageType.STATE_REQUEST, target="System", payload="snapshot")

    async def request_who(self) -> None:
        """Ask the hub for the list of online agents."""
        await self.send_message(MessageType.WHO_REQUEST, target="System", payload="who")

    async def request_history(self, limit: int | None = 20) -> None:
        """Ask the hub for recent chat history.

        Parameters
        ----------
        limit : int or None, optional
            Number of recent messages to fetch (floored at ``1``), or ``None``
            for the full history. Defaults to ``20``.
        """
        if limit is None:
            await self.send_message(
                MessageType.HISTORY_REQUEST, target="System", payload="history"
            )
            return
        n = max(1, int(limit))
        await self.send_message(
            MessageType.HISTORY_REQUEST, target="System", payload="history", limit=n
        )

    def start(self) -> None:
        """Run :meth:`connect` to completion on a fresh event loop.

        Intended as a blocking entry point for scripts. ``Ctrl+C`` is caught and
        reported instead of raising.
        """
        try:
            asyncio.run(self.connect())
        except KeyboardInterrupt:
            if self.verbose:
                print(f"\n[{self.name}] Shutting down.")
