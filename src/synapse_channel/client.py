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
    token : str or None, optional
        Shared-secret token presented on the registration message when the hub
        requires authentication. ``None`` sends no token (the default for an
        open, loopback hub).
    """

    def __init__(
        self,
        name: str,
        on_message_callback: MessageCallback | None = None,
        *,
        uri: str = DEFAULT_HUB_URI,
        heartbeat_interval: float = 20.0,
        verbose: bool = True,
        token: str | None = None,
        takeover: bool = False,
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
        self.token = token
        self.takeover = bool(takeover)

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
                # before the first user-issued command. The token (if any) rides
                # this first message, which is where the hub gates authentication;
                # ``takeover`` asks the hub to evict a stale holder of this name.
                extra: dict[str, Any] = {}
                if self.token:
                    extra["token"] = self.token
                if self.takeover:
                    extra["takeover"] = True
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
        except asyncio.TimeoutError:
            # On Python 3.10 asyncio.wait_for raises asyncio.TimeoutError, which is
            # not the builtin TimeoutError (the two are only aliased on 3.11+).
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

    async def chat(self, payload: str, *, target: str = "all", priority: bool = False) -> None:
        """Send a chat message to the room or a single agent.

        Parameters
        ----------
        payload : str
            Message text.
        target : str, optional
            Recipient agent name, or ``"all"``. Defaults to ``"all"``.
        priority : bool, optional
            Mark the message as priority so it wakes even directed-only waiters
            (use sparingly — for announcements that genuinely must reach everyone).
        """
        extra = {"priority": True} if priority else {}
        await self.send_message(MessageType.CHAT, target=target, payload=payload, **extra)

    async def claim(
        self,
        task_id: str,
        note: str = "",
        ttl_seconds: float | None = None,
        *,
        worktree: str = "",
        paths: tuple[str, ...] | list[str] = (),
        idem_key: str | None = None,
        git: dict[str, Any] | None = None,
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
        idem_key : str or None, optional
            Idempotency key; reuse the same key when retrying after a reconnect so
            the hub replays the original result instead of claiming twice.
        git : dict[str, Any] or None, optional
            Branch context (``branch``/``base``/``auto_release_on``) for a
            git-scoped claim, as built client-side by
            :mod:`synapse_channel.gitclaim`. The hub stores and displays it but
            never acts on it.
        """
        extra: dict[str, Any] = {"task_id": task_id.strip(), "note": note}
        if ttl_seconds is not None:
            extra["ttl_seconds"] = float(ttl_seconds)
        if worktree:
            extra["worktree"] = worktree
        if paths:
            extra["paths"] = list(paths)
        if idem_key:
            extra["idem_key"] = idem_key
        if git:
            extra["git"] = git
        await self.send_message(
            MessageType.CLAIM, target="System", payload=task_id.strip(), **extra
        )

    async def release(
        self, task_id: str, *, epoch: int | None = None, idem_key: str | None = None
    ) -> None:
        """Release a task lease.

        Parameters
        ----------
        task_id : str
            Task identifier; surrounding whitespace is stripped.
        epoch : int or None, optional
            Expected lease generation; when given, the hub refuses the release if
            the lease has since been superseded.
        idem_key : str or None, optional
            Idempotency key; reuse the same key when retrying after a reconnect so
            the hub replays the original result instead of releasing twice.
        """
        extra: dict[str, Any] = {"task_id": task_id.strip()}
        if epoch is not None:
            extra["epoch"] = int(epoch)
        if idem_key:
            extra["idem_key"] = idem_key
        await self.send_message(
            MessageType.RELEASE, target="System", payload=task_id.strip(), **extra
        )

    async def update_task(
        self,
        task_id: str,
        *,
        status: str | None = None,
        note: str | None = None,
        data_ref: str | None = None,
        epoch: int | None = None,
        expected_version: int | None = None,
        idem_key: str | None = None,
    ) -> None:
        """Update an owned task's status, note, or artefact reference.

        Parameters
        ----------
        task_id : str
            Task identifier; surrounding whitespace is stripped.
        status : str or None, optional
            New lifecycle status (see :mod:`synapse_channel.lifecycle`); the hub
            rejects an illegal transition.
        note : str or None, optional
            Replacement note.
        data_ref : str or None, optional
            Replacement artefact reference.
        epoch : int or None, optional
            Expected lease generation; a stale epoch is refused.
        expected_version : int or None, optional
            Expected field version for compare-and-swap; a mismatch is refused.
        idem_key : str or None, optional
            Idempotency key for safe retries after a reconnect.
        """
        extra: dict[str, Any] = {"task_id": task_id.strip()}
        if status is not None:
            extra["status"] = status
        if note is not None:
            extra["note"] = note
        if data_ref is not None:
            extra["data_ref"] = data_ref
        if epoch is not None:
            extra["epoch"] = int(epoch)
        if expected_version is not None:
            extra["expected_version"] = int(expected_version)
        if idem_key:
            extra["idem_key"] = idem_key
        await self.send_message(
            MessageType.TASK_UPDATE, target="System", payload=task_id.strip(), **extra
        )

    async def handoff(
        self,
        task_id: str,
        to_agent: str,
        *,
        note: str | None = None,
        epoch: int | None = None,
        idem_key: str | None = None,
    ) -> None:
        """Hand an owned task to another agent in one atomic step.

        Transfers ownership directly, with no release/re-claim window, carrying
        the task's scope, status, and artefact reference. The recipient must be
        online; the hub records the move on the shared blackboard.

        Parameters
        ----------
        task_id : str
            Identifier of the owned task; whitespace is stripped.
        to_agent : str
            The agent to receive the task; whitespace is stripped.
        note : str or None, optional
            Replacement note for the moved claim; the existing note is kept when
            ``None``.
        epoch : int or None, optional
            Expected lease generation; a stale epoch is refused.
        idem_key : str or None, optional
            Idempotency key for a safe retry after a reconnect.
        """
        extra: dict[str, Any] = {"task_id": task_id.strip(), "to_agent": to_agent.strip()}
        if note is not None:
            extra["note"] = note
        if epoch is not None:
            extra["epoch"] = int(epoch)
        if idem_key:
            extra["idem_key"] = idem_key
        await self.send_message(
            MessageType.HANDOFF, target="System", payload=task_id.strip(), **extra
        )

    async def save_checkpoint(
        self,
        task_id: str,
        checkpoint: str,
        *,
        epoch: int | None = None,
        idem_key: str | None = None,
    ) -> None:
        """Save a resume checkpoint on an owned task.

        The checkpoint is durable and survives lease expiry: if this agent's
        lease lapses, the next agent to claim the task inherits it (and receives
        it in the claim grant) instead of restarting.

        Parameters
        ----------
        task_id : str
            Identifier of the owned task; whitespace is stripped.
        checkpoint : str
            Opaque resume token to store.
        epoch : int or None, optional
            Expected lease generation; a stale epoch is refused.
        idem_key : str or None, optional
            Idempotency key for a safe retry after a reconnect.
        """
        extra: dict[str, Any] = {"task_id": task_id.strip(), "checkpoint": checkpoint}
        if epoch is not None:
            extra["epoch"] = int(epoch)
        if idem_key:
            extra["idem_key"] = idem_key
        await self.send_message(MessageType.CHECKPOINT, target="System", **extra)

    async def request_resume(self, since: int = 0) -> None:
        """Ask the hub for every chat message after a cursor.

        Use after a reconnect to catch up on exactly the messages missed.

        Parameters
        ----------
        since : int, optional
            The last chat ``msg_id`` already seen; the hub returns messages
            numbered above it. Defaults to ``0`` (the full history).
        """
        await self.send_message(
            MessageType.RESUME_REQUEST, target="System", payload="resume", since=int(since)
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
            await self.send_message(MessageType.HISTORY_REQUEST, target="System", payload="history")
            return
        n = max(1, int(limit))
        await self.send_message(
            MessageType.HISTORY_REQUEST, target="System", payload="history", limit=n
        )

    async def request_wait(self, task_id: str) -> None:
        """Register an advisory wait for a task another agent holds.

        The hub refuses the wait if it would close a hold-and-wait deadlock cycle.
        The wait is advisory: retry the claim once the holder releases.

        Parameters
        ----------
        task_id : str
            Identifier of the held task to wait for; whitespace is stripped.
        """
        await self.send_message(
            MessageType.WAIT_REQUEST,
            target="System",
            payload=task_id.strip(),
            task_id=task_id.strip(),
        )

    async def post_task(
        self,
        task_id: str,
        title: str,
        *,
        description: str = "",
        depends_on: tuple[str, ...] | list[str] = (),
        suggested_owner: str = "",
    ) -> None:
        """Declare or re-declare a task on the shared plan (an upsert).

        This is the planning surface, distinct from :meth:`claim` (the lease on
        doing the work). Re-posting the same id refines the declaration.

        Parameters
        ----------
        task_id : str
            Stable identifier, shared with any claim taken on the task.
        title : str
            Short human-readable name of the work.
        description : str, optional
            Longer description or acceptance notes.
        depends_on : tuple[str, ...] or list[str], optional
            Prerequisite task ids; the hub refuses dependencies that form a cycle.
        suggested_owner : str, optional
            Advisory proposed owner.
        """
        extra: dict[str, Any] = {"task_id": task_id.strip(), "title": title}
        if description:
            extra["description"] = description
        if depends_on:
            extra["depends_on"] = list(depends_on)
        if suggested_owner:
            extra["suggested_owner"] = suggested_owner
        await self.send_message(MessageType.LEDGER_TASK, target="System", **extra)

    async def update_ledger_task(
        self,
        task_id: str,
        *,
        status: str | None = None,
        suggested_owner: str | None = None,
    ) -> None:
        """Change a plan task's planning status or suggested owner.

        Parameters
        ----------
        task_id : str
            Identifier of the task to update.
        status : str or None, optional
            New planning status (``open``/``in_progress``/``blocked``/``done``/
            ``cancelled``); an unknown status is refused.
        suggested_owner : str or None, optional
            Replacement advisory owner (``""`` clears it).
        """
        extra: dict[str, Any] = {"task_id": task_id.strip()}
        if status is not None:
            extra["status"] = status
        if suggested_owner is not None:
            extra["suggested_owner"] = suggested_owner
        await self.send_message(MessageType.LEDGER_TASK_UPDATE, target="System", **extra)

    async def post_progress(self, task_id: str, text: str, *, kind: str = "note") -> None:
        """Append a structured progress note to the progress ledger.

        Parameters
        ----------
        task_id : str
            Task the note concerns; ``""`` for a board-wide note.
        text : str
            Body of the note.
        kind : str, optional
            One of ``note``/``blocked``/``assessment``. Defaults to ``"note"``.
        """
        await self.send_message(
            MessageType.LEDGER_PROGRESS,
            target="System",
            payload=text,
            task_id=task_id.strip(),
            kind=kind,
        )

    async def request_board(self) -> None:
        """Ask the hub for a snapshot of the shared blackboard."""
        await self.send_message(MessageType.BOARD_REQUEST, target="System", payload="board")

    async def advertise(
        self,
        *,
        description: str = "",
        skills: tuple[str, ...] | list[str] = (),
        task_classes: tuple[str, ...] | list[str] = (),
        model: str = "",
        meta: dict[str, Any] | None = None,
    ) -> None:
        """Advertise this agent's capability card to the hub.

        The card describes what the agent can do — its skills and the task
        classes it can take — so other agents can discover it and a router can
        pick it by task class. Re-advertising refreshes the card.

        Parameters
        ----------
        description : str, optional
            Free-form summary of what the agent does.
        skills : tuple[str, ...] or list[str], optional
            Capability tags the agent claims.
        task_classes : tuple[str, ...] or list[str], optional
            Routing classes the agent can take.
        model : str, optional
            Backing model identifier.
        meta : dict[str, Any] or None, optional
            Descriptive metadata.
        """
        extra: dict[str, Any] = {}
        if description:
            extra["description"] = description
        if skills:
            extra["skills"] = list(skills)
        if task_classes:
            extra["task_classes"] = list(task_classes)
        if model:
            extra["model"] = model
        if meta:
            extra["meta"] = meta
        await self.send_message(MessageType.ADVERTISE, target="System", **extra)

    async def request_manifest(self) -> None:
        """Ask the hub for the capability manifest of all advertised agents."""
        await self.send_message(MessageType.MANIFEST_REQUEST, target="System", payload="manifest")

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
