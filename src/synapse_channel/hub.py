# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — central WebSocket hub that routes messages and owns state
"""Central WebSocket hub for the Synapse coordination bus.

:class:`SynapseHub` is the single source of truth for the channel: it tracks
connected sockets and named agents, enforces unique agent names, relays chat and
targeted messages, persists chat history, and delegates claim/task/resource
bookkeeping to a :class:`~synapse_channel.state.SynapseState`. All routing state
lives on the instance — there are no module globals — so several hubs can run in
one process, which keeps the routing logic deterministic and unit-testable.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any

import websockets
from websockets.exceptions import ConnectionClosed

from synapse_channel.journal import (
    record_chat,
    record_claim,
    record_release,
    record_resource,
    record_task_update,
    replay,
)
from synapse_channel.persistence import EventStore
from synapse_channel.protocol import (
    RESOURCE_TYPE_ALIASES,
    MessageType,
    system_message,
)
from synapse_channel.state import SynapseState

logger = logging.getLogger("synapse.hub")

DEFAULT_HOST = "localhost"
DEFAULT_PORT = 8876


class SynapseHub:
    """Routing core that maintains presence, history, and coordination state.

    Parameters
    ----------
    default_ttl_seconds : float, optional
        Lease TTL passed to the underlying :class:`SynapseState`. Defaults to
        ``3600.0``.
    hub_id : str or None, optional
        Stable hub identifier stamped on outgoing system messages. When ``None``
        a random ``"syn-XXXXXXXX"`` id is generated.
    journal : EventStore or None, optional
        When given, authoritative mutations are appended to this durable log and
        the hub's state is rebuilt from it on construction, so a restart resumes
        live leases and history instead of an empty registry. When ``None`` the
        hub is purely in-memory.
    """

    def __init__(
        self,
        *,
        default_ttl_seconds: float = 3600.0,
        hub_id: str | None = None,
        journal: EventStore | None = None,
    ) -> None:
        self.journal = journal
        self.hub_id = hub_id or f"syn-{uuid.uuid4().hex[:8]}"
        self.connected_clients: set[Any] = set()
        self.agent_sockets: dict[str, Any] = {}
        self.socket_agent: dict[Any, str] = {}
        if journal is not None:
            replayed = replay(journal, default_ttl_seconds=default_ttl_seconds)
            self.state = replayed.state
            self.chat_history = replayed.chat_history
            self._message_seq = replayed.message_seq
        else:
            self.state = SynapseState(default_ttl_seconds=default_ttl_seconds)
            self.chat_history = []
            self._message_seq = 0

    # -- helpers --------------------------------------------------------------

    def _next_msg_id(self) -> int:
        """Return a strictly increasing per-hub message sequence number."""
        self._message_seq += 1
        return self._message_seq

    def _system(self, payload: str, **extra: Any) -> dict[str, Any]:
        """Build a hub system message stamped with this hub's id."""
        return system_message(payload, hub_id=self.hub_id, **extra)

    def online_agents(self) -> list[str]:
        """Return the sorted names of currently registered agents."""
        return sorted(self.agent_sockets.keys())

    async def _send_json(self, websocket: Any, data: dict[str, Any]) -> None:
        """Serialise and send one message to a single socket."""
        await websocket.send(json.dumps(data))

    async def _broadcast(self, data: dict[str, Any]) -> None:
        """Send one message to every connected socket, ignoring failures."""
        if not self.connected_clients:
            return
        raw = json.dumps(data)
        await asyncio.gather(
            *(client.send(raw) for client in self.connected_clients),
            return_exceptions=True,
        )

    async def _broadcast_presence(self, event: str, agent: str | None = None) -> None:
        """Broadcast a presence update naming who joined or left."""
        await self._broadcast(
            self._system(
                "Presence update",
                msg_type=MessageType.PRESENCE_UPDATE,
                online_agents=self.online_agents(),
                event=event,
                agent=agent,
            )
        )

    async def _send_to_agent(self, agent: str, data: dict[str, Any]) -> bool:
        """Send to a named agent's socket; return whether the send succeeded."""
        websocket = self.agent_sockets.get(agent)
        if websocket is None:
            return False
        try:
            await self._send_json(websocket, data)
            return True
        except Exception:
            return False

    # -- typed handlers -------------------------------------------------------

    async def _handle_claim(self, sender: str, data: dict[str, Any], websocket: Any) -> None:
        """Apply a scoped claim request and broadcast the grant, or deny the sender."""
        task_id = str(data.get("task_id") or data.get("payload") or "").strip()
        note = str(data.get("note") or "")
        ttl_seconds = data.get("ttl_seconds")
        worktree = str(data.get("worktree") or "")
        raw_paths = data.get("paths")
        paths = [str(p) for p in raw_paths] if isinstance(raw_paths, list) else []

        ttl_val: float | None
        if ttl_seconds is None:
            ttl_val = None
        else:
            try:
                ttl_val = float(ttl_seconds)
            except (TypeError, ValueError):
                ttl_val = None

        ok, message = self.state.claim(
            sender, task_id, note=note, ttl_seconds=ttl_val, worktree=worktree, paths=paths
        )
        if ok:
            claim = self.state.claims[task_id]
            if self.journal is not None:
                record_claim(self.journal, claim)
            await self._broadcast(
                self._system(
                    message,
                    msg_type=MessageType.CLAIM_GRANTED,
                    task_id=task_id,
                    owner=claim.owner,
                    note=claim.note,
                    lease_expires_at=claim.lease_expires_at,
                    status=claim.status,
                    worktree=claim.worktree,
                    paths=list(claim.paths),
                    epoch=claim.epoch,
                )
            )
            return
        await self._send_json(
            websocket,
            self._system(
                message,
                msg_type=MessageType.CLAIM_DENIED,
                target=sender,
                task_id=task_id,
            ),
        )

    @staticmethod
    def _epoch_of(data: dict[str, Any]) -> int | None:
        """Extract an optional integer epoch from a message, or ``None``."""
        value = data.get("epoch")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return int(value)

    async def _handle_task_update(self, sender: str, data: dict[str, Any], websocket: Any) -> None:
        """Apply an owner's status/note/data-ref update and broadcast it."""
        task_id = str(data.get("task_id") or data.get("id") or "").strip()
        status = data.get("status")
        note = data.get("note")
        data_ref = data.get("data_ref")

        ok, message = self.state.update_task(
            sender,
            task_id,
            status=str(status) if status else None,
            note=str(note) if note is not None else None,
            data_ref=str(data_ref) if data_ref is not None else None,
            epoch=self._epoch_of(data),
        )
        if ok:
            claim = self.state.claims.get(task_id)
            if self.journal is not None:
                record_task_update(self.journal, self.state.claims[task_id])
            await self._broadcast(
                self._system(
                    message,
                    msg_type=MessageType.TASK_UPDATED,
                    task_id=task_id,
                    owner=sender if claim else None,
                    status=claim.status if claim else None,
                    data_ref=claim.data_ref if claim else None,
                )
            )
        else:
            await self._send_json(
                websocket, self._system(message, msg_type=MessageType.ERROR, target=sender)
            )

    async def _handle_resource(self, sender: str, data: dict[str, Any], websocket: Any) -> None:
        """Register a resource offer and broadcast it, or reject bad input."""
        kind = str(data.get("kind") or data.get("resource_kind") or "").strip()
        name = str(data.get("name") or data.get("resource_name") or "").strip()
        capacity = data.get("capacity", 1)
        meta = data.get("meta") or {}

        if not kind or not name:
            await self._send_json(
                websocket,
                self._system(
                    "resource offer requires kind+name",
                    msg_type=MessageType.ERROR,
                    target=sender,
                ),
            )
            return

        key = self.state.offer_resource(sender, kind=kind, name=name, capacity=capacity, meta=meta)
        if self.journal is not None:
            record_resource(self.journal, self.state.resources[key])
        await self._broadcast(
            self._system(
                f"Resource offered by {sender}",
                msg_type=MessageType.RESOURCE_OFFERED,
                agent=sender,
                kind=kind,
                name=name,
                key=key,
            )
        )

    async def _handle_release(self, sender: str, data: dict[str, Any], websocket: Any) -> None:
        """Release a task and broadcast it, or deny the sender."""
        task_id = str(data.get("task_id") or data.get("payload") or "").strip()
        ok, message = self.state.release(sender, task_id, epoch=self._epoch_of(data))
        if ok:
            if self.journal is not None:
                record_release(self.journal, task_id)
            await self._broadcast(
                self._system(
                    message,
                    msg_type=MessageType.RELEASE_GRANTED,
                    task_id=task_id,
                    owner=sender,
                )
            )
            return
        await self._send_json(
            websocket,
            self._system(
                message,
                msg_type=MessageType.RELEASE_DENIED,
                target=sender,
                task_id=task_id,
            ),
        )

    async def _handle_state_request(self, sender: str, websocket: Any) -> None:
        """Send the requesting agent a full state snapshot."""
        await self._send_json(
            websocket,
            self._system(
                "State snapshot",
                msg_type=MessageType.STATE_SNAPSHOT,
                target=sender,
                snapshot=self.state.snapshot(),
            ),
        )

    async def _handle_who_request(self, sender: str, websocket: Any) -> None:
        """Send the requesting agent the online-agent roster."""
        await self._send_json(
            websocket,
            self._system(
                "Who snapshot",
                msg_type=MessageType.WHO_SNAPSHOT,
                target=sender,
                online_agents=self.online_agents(),
                connected_clients=len(self.connected_clients),
            ),
        )

    async def _handle_history_request(
        self, sender: str, data: dict[str, Any], websocket: Any
    ) -> None:
        """Send the requesting agent recent (or full) chat history."""
        raw_limit = data.get("limit")
        limit: int | None
        if raw_limit is None:
            limit = None
        else:
            try:
                limit = int(raw_limit)
            except (TypeError, ValueError):
                limit = None
        if limit is None:
            history = list(self.chat_history)
            requested_limit: int | str = "all"
        else:
            n = max(1, limit)
            history = list(self.chat_history)[-n:]
            requested_limit = n
        await self._send_json(
            websocket,
            self._system(
                "History snapshot",
                msg_type=MessageType.HISTORY_SNAPSHOT,
                target=sender,
                history=history,
                requested_limit=requested_limit,
            ),
        )

    # -- registration + name resolution --------------------------------------

    async def _resolve_sender(self, sender: str, websocket: Any) -> str | None:
        """Bind a socket to a sender name, enforcing uniqueness.

        Returns the resolved name, or ``None`` when a name conflict closed the
        socket.
        """
        known_sender = self.socket_agent.get(websocket)
        if known_sender is None:
            owner_ws = self.agent_sockets.get(sender)
            if owner_ws is not None and owner_ws != websocket:
                await self._send_json(
                    websocket,
                    self._system(
                        f"Name '{sender}' is already online from another session. "
                        "Use a unique --name.",
                        msg_type=MessageType.NAME_CONFLICT,
                        target=sender,
                    ),
                )
                await websocket.close(code=4009, reason="name conflict")
                return None
            self.socket_agent[websocket] = sender
            return sender
        if known_sender != sender:
            await self._send_json(
                websocket,
                self._system(
                    f"Sender name switch denied: '{known_sender}' -> '{sender}'. "
                    "Reconnect with a new --name.",
                    msg_type=MessageType.NAME_CONFLICT,
                    target=known_sender,
                ),
            )
            await websocket.close(code=4009, reason="name switch")
            return None
        return known_sender

    async def handle_message(self, raw_message: str | bytes, websocket: Any) -> None:
        """Parse and route one inbound frame.

        Parameters
        ----------
        raw_message : str or bytes
            The raw frame received from a client socket.
        websocket : Any
            The socket the frame arrived on.
        """
        try:
            data = json.loads(raw_message)
        except json.JSONDecodeError:
            await self._send_json(
                websocket, self._system("Malformed JSON.", msg_type=MessageType.ERROR)
            )
            return

        sender = str(data.get("sender") or "").strip() or f"anon-{id(websocket)}"
        target = str(data.get("target") or "all")
        msg_type = str(data.get("type") or MessageType.CHAT).strip().lower()
        payload = str(data.get("payload") or "")

        resolved = await self._resolve_sender(sender, websocket)
        if resolved is None:
            return
        sender = resolved

        self.state.heartbeat(sender)
        is_new_agent = sender not in self.agent_sockets
        self.agent_sockets[sender] = websocket
        if is_new_agent:
            await self._broadcast_presence("joined", sender)
        logger.info("[%s -> %s] (%s): %s", sender, target, msg_type, payload)

        await self._route(sender, msg_type, data, websocket)

    async def _route(
        self, sender: str, msg_type: str, data: dict[str, Any], websocket: Any
    ) -> None:
        """Dispatch a parsed, sender-resolved message to its handler."""
        if msg_type == MessageType.CHAT:
            data["timestamp"] = float(data.get("timestamp") or time.time())
            data["type"] = MessageType.CHAT
            data["hub_id"] = self.hub_id
            data["msg_id"] = self._next_msg_id()
            self.chat_history.append(data.copy())
            if self.journal is not None:
                record_chat(self.journal, data)
            await self._broadcast(data)
            return
        if msg_type == MessageType.HEARTBEAT:
            return
        if msg_type == MessageType.CLAIM:
            await self._handle_claim(sender, data, websocket)
            return
        if msg_type == MessageType.RELEASE:
            await self._handle_release(sender, data, websocket)
            return
        if msg_type == MessageType.STATE_REQUEST:
            await self._handle_state_request(sender, websocket)
            return
        if msg_type == MessageType.WHO_REQUEST:
            await self._handle_who_request(sender, websocket)
            return
        if msg_type == MessageType.HISTORY_REQUEST:
            await self._handle_history_request(sender, data, websocket)
            return
        if msg_type == MessageType.TASK_UPDATE:
            await self._handle_task_update(sender, data, websocket)
            return
        if msg_type in RESOURCE_TYPE_ALIASES:
            await self._handle_resource(sender, data, websocket)
            return
        await self._send_to_agent(
            sender,
            self._system(
                f"Unknown message type '{msg_type}'.",
                msg_type=MessageType.ERROR,
                target=sender,
            ),
        )

    async def register(self, websocket: Any) -> None:
        """Record a new socket and send it the welcome message."""
        self.connected_clients.add(websocket)
        logger.info("Client connected: %s (total=%d)", id(websocket), len(self.connected_clients))
        await self._send_json(
            websocket,
            self._system(
                "Welcome to Synapse",
                msg_type=MessageType.WELCOME,
                target="self",
                connected_clients=len(self.connected_clients),
                online_agents=self.online_agents(),
            ),
        )

    async def unregister(self, websocket: Any) -> None:
        """Drop a socket, releasing its agent name and broadcasting departure."""
        self.connected_clients.discard(websocket)
        name = self.socket_agent.pop(websocket, None)
        if name is not None and self.agent_sockets.get(name) == websocket:
            self.agent_sockets.pop(name, None)
            await self._broadcast_presence("left", name)
        logger.info(
            "Client disconnected: %s (total=%d)", id(websocket), len(self.connected_clients)
        )

    async def handler(self, websocket: Any) -> None:
        """Serve one client connection from registration to disconnect."""
        await self.register(websocket)
        try:
            async for raw in websocket:
                await self.handle_message(raw, websocket)
        except ConnectionClosed:
            pass
        finally:
            await self.unregister(websocket)

    async def serve(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
        """Run the hub's WebSocket server until cancelled.

        Parameters
        ----------
        host : str, optional
            Bind address. Defaults to :data:`DEFAULT_HOST`.
        port : int, optional
            Bind port. Defaults to :data:`DEFAULT_PORT`.
        """
        async with websockets.serve(self.handler, host, port):
            logger.info("Synapse Hub running on ws://%s:%d", host, port)
            await asyncio.Future()
