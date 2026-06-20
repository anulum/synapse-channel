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
from pathlib import Path
from typing import Any

import websockets
from websockets.exceptions import ConnectionClosed

from synapse_channel.auth import TokenAuthenticator
from synapse_channel.capability import CapabilityRegistry
from synapse_channel.deadlock import would_create_cycle
from synapse_channel.idempotency import IdempotencyCache
from synapse_channel.journal import (
    record_chat,
    record_claim,
    record_ledger_progress,
    record_ledger_task,
    record_release,
    record_resource,
    record_task_update,
    replay,
)
from synapse_channel.ledger import DEFAULT_MAX_PROGRESS, Blackboard, ProgressNote
from synapse_channel.persistence import EventStore
from synapse_channel.protocol import (
    RESOURCE_TYPE_ALIASES,
    MessageType,
    system_message,
)
from synapse_channel.ratelimit import RateLimiter
from synapse_channel.relay import append_jsonl, encode_lite, trim_jsonl_tail
from synapse_channel.state import SynapseState

logger = logging.getLogger("synapse.hub")

DEFAULT_HOST = "localhost"
DEFAULT_PORT = 8876
DEFAULT_MAX_HISTORY = 10000
DEFAULT_MAX_QUEUE = 64
DEFAULT_RELAY_MAX_LINES = 5000

LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
"""Bind hosts treated as loopback-only, where running without a token is fine."""


def is_loopback_host(host: str) -> bool:
    """Return whether ``host`` binds only the loopback interface."""
    return host.strip().lower() in LOOPBACK_HOSTS


_MUTATING_TYPES = (
    frozenset(
        {
            MessageType.CLAIM,
            MessageType.RELEASE,
            MessageType.TASK_UPDATE,
            MessageType.HANDOFF,
            MessageType.CHECKPOINT,
        }
    )
    | RESOURCE_TYPE_ALIASES
)
"""Inbound message types eligible for idempotent replay protection."""


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
    rate_limiter : RateLimiter or None, optional
        When given, non-heartbeat messages from an agent over its limit are
        refused, so one runaway agent cannot swamp the single hub. ``None``
        disables rate limiting.
    max_history : int, optional
        Maximum chat messages retained in memory; the oldest are dropped beyond
        this bound so history cannot grow without limit. The durable log (when a
        journal is attached) still records every message. Defaults to
        :data:`DEFAULT_MAX_HISTORY`.
    relay_log : str or pathlib.Path or None, optional
        When given, every broadcast message is also mirrored to this newline-
        delimited log in the compact lite format (see
        :func:`~synapse_channel.relay.encode_lite`), so a token-budgeted agent
        can observe the channel by tailing a file instead of holding a socket.
        ``None`` disables the mirror.
    relay_max_lines : int, optional
        Upper bound on the relay log: it is trimmed back to its last this-many
        lines once it grows that far past the bound, so the mirror cannot grow
        without limit. Defaults to :data:`DEFAULT_RELAY_MAX_LINES`.
    max_progress : int, optional
        Maximum progress notes retained on the shared blackboard; the oldest are
        dropped beyond this bound. The durable log (when attached) still records
        every note. Defaults to :data:`~synapse_channel.ledger.DEFAULT_MAX_PROGRESS`.
    authenticator : TokenAuthenticator or None, optional
        When given, a connecting agent must present a valid shared-secret token
        on its first message or the hub refuses and closes the socket. ``None``
        leaves the hub open, which is the right default for a loopback bind.
    """

    def __init__(
        self,
        *,
        default_ttl_seconds: float = 3600.0,
        hub_id: str | None = None,
        journal: EventStore | None = None,
        rate_limiter: RateLimiter | None = None,
        max_history: int = DEFAULT_MAX_HISTORY,
        relay_log: str | Path | None = None,
        relay_max_lines: int = DEFAULT_RELAY_MAX_LINES,
        max_progress: int = DEFAULT_MAX_PROGRESS,
        authenticator: TokenAuthenticator | None = None,
    ) -> None:
        self.journal = journal
        self.rate_limiter = rate_limiter
        self.authenticator = authenticator
        self.max_history = max(int(max_history), 1)
        self.relay_log = Path(relay_log) if relay_log else None
        self.relay_max_lines = max(int(relay_max_lines), 1)
        self._relay_appends = 0
        self.hub_id = hub_id or f"syn-{uuid.uuid4().hex[:8]}"
        self.connected_clients: set[Any] = set()
        self.agent_sockets: dict[str, Any] = {}
        self.socket_agent: dict[Any, str] = {}
        self._idempotency = IdempotencyCache()
        self._waits: dict[str, str] = {}
        self.capabilities = CapabilityRegistry()
        if journal is not None:
            replayed = replay(
                journal, default_ttl_seconds=default_ttl_seconds, max_progress=max_progress
            )
            self.state = replayed.state
            self.chat_history = replayed.chat_history[-self.max_history :]
            self._message_seq = replayed.message_seq
            self.blackboard = replayed.blackboard
        else:
            self.state = SynapseState(default_ttl_seconds=default_ttl_seconds)
            self.chat_history = []
            self._message_seq = 0
            self.blackboard = Blackboard(max_progress=max_progress)

    # -- helpers --------------------------------------------------------------

    def _next_msg_id(self) -> int:
        """Return a strictly increasing per-hub message sequence number."""
        self._message_seq += 1
        return self._message_seq

    @staticmethod
    def _idempotency_key(data: dict[str, Any]) -> str:
        """Return the client-supplied idempotency key, or an empty string."""
        return str(data.get("idem_key") or "")

    def _remember(self, data: dict[str, Any], response: dict[str, Any]) -> None:
        """Cache the response of an applied mutation under its idempotency key."""
        key = self._idempotency_key(data)
        if key:
            self._idempotency.put(key, response)

    async def _maybe_replay_duplicate(
        self, msg_type: str, data: dict[str, Any], websocket: Any
    ) -> bool:
        """Replay the cached response for a duplicate mutation, if any.

        Parameters
        ----------
        msg_type : str
            The inbound message type.
        data : dict[str, Any]
            The decoded message.
        websocket : Any
            The sender's socket.

        Returns
        -------
        bool
            ``True`` when the message was a recognised duplicate of an already
            applied mutation and its original response was re-sent to the sender;
            ``False`` when the message should be processed normally.
        """
        if msg_type not in _MUTATING_TYPES:
            return False
        key = self._idempotency_key(data)
        if not key:
            return False
        cached = self._idempotency.get(key)
        if cached is None:
            return False
        await self._send_json(websocket, cached)
        return True

    def _system(self, payload: str, **extra: Any) -> dict[str, Any]:
        """Build a hub system message stamped with this hub's id."""
        return system_message(payload, hub_id=self.hub_id, **extra)

    def online_agents(self) -> list[str]:
        """Return the sorted names of currently registered agents."""
        return sorted(self.agent_sockets.keys())

    async def _send_json(self, websocket: Any, data: dict[str, Any]) -> None:
        """Serialise and send one message to a single socket."""
        await websocket.send(json.dumps(data))

    def _mirror_to_relay(self, data: dict[str, Any]) -> None:
        """Append one broadcast message to the lite relay log, if configured.

        The log is written even when no socket is connected — its whole point is
        to let an observer catch up from the file later. It is trimmed back to
        :attr:`relay_max_lines` once that many lines have been appended since the
        last trim, bounding the file to roughly twice that many lines.
        """
        if self.relay_log is None:
            return
        append_jsonl(self.relay_log, encode_lite(data))
        self._relay_appends += 1
        if self._relay_appends >= self.relay_max_lines:
            trim_jsonl_tail(self.relay_log, self.relay_max_lines)
            self._relay_appends = 0

    async def _broadcast(self, data: dict[str, Any]) -> None:
        """Send one message to every connected socket, ignoring failures."""
        self._mirror_to_relay(data)
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
            self._waits.pop(sender, None)  # a successful claim means no longer blocked
            if self.journal is not None:
                record_claim(self.journal, claim)
            grant = self._system(
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
                version=claim.version,
                checkpoint=claim.checkpoint,
            )
            self._remember(data, grant)
            await self._broadcast(grant)
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
    def _optional_int(data: dict[str, Any], key: str) -> int | None:
        """Extract an optional integer field from a message, or ``None``.

        Booleans and non-numeric values are treated as absent so a stray ``true``
        is never read as a guard value.

        Parameters
        ----------
        data : dict[str, Any]
            The decoded message.
        key : str
            The field to read.

        Returns
        -------
        int or None
            The integer value, or ``None`` when the field is absent or not numeric.
        """
        value = data.get(key)
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
            epoch=self._optional_int(data, "epoch"),
            expected_version=self._optional_int(data, "expected_version"),
        )
        if ok:
            claim = self.state.claims.get(task_id)
            if self.journal is not None:
                record_task_update(self.journal, self.state.claims[task_id])
            updated = self._system(
                message,
                msg_type=MessageType.TASK_UPDATED,
                task_id=task_id,
                owner=sender if claim else None,
                status=claim.status if claim else None,
                data_ref=claim.data_ref if claim else None,
                version=claim.version if claim else None,
            )
            self._remember(data, updated)
            await self._broadcast(updated)
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
        offered = self._system(
            f"Resource offered by {sender}",
            msg_type=MessageType.RESOURCE_OFFERED,
            agent=sender,
            kind=kind,
            name=name,
            key=key,
        )
        self._remember(data, offered)
        await self._broadcast(offered)

    async def _handle_release(self, sender: str, data: dict[str, Any], websocket: Any) -> None:
        """Release a task and broadcast it, or deny the sender."""
        task_id = str(data.get("task_id") or data.get("payload") or "").strip()
        ok, message = self.state.release(sender, task_id, epoch=self._optional_int(data, "epoch"))
        if ok:
            if self.journal is not None:
                record_release(self.journal, task_id)
            granted = self._system(
                message,
                msg_type=MessageType.RELEASE_GRANTED,
                task_id=task_id,
                owner=sender,
            )
            self._remember(data, granted)
            await self._broadcast(granted)
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

    async def _handle_handoff(self, sender: str, data: dict[str, Any], websocket: Any) -> None:
        """Transfer an owned task to an online agent and broadcast it, or deny.

        The recipient must be currently online so the work actually moves to a
        present agent. On success the move is also recorded as a progress note on
        the shared blackboard, so the supervisor sees who handed what to whom.
        """
        task_id = str(data.get("task_id") or "").strip()
        to_agent = str(data.get("to_agent") or data.get("target") or "").strip()
        note = data.get("note")

        if to_agent and to_agent not in self.agent_sockets:
            await self._send_json(
                websocket,
                self._system(
                    f"Handoff target '{to_agent}' is not online.",
                    msg_type=MessageType.HANDOFF_DENIED,
                    target=sender,
                    task_id=task_id,
                ),
            )
            return

        ok, message = self.state.handoff(
            sender,
            task_id,
            to_agent,
            note=str(note) if note is not None else None,
            epoch=self._optional_int(data, "epoch"),
        )
        if not ok:
            await self._send_json(
                websocket,
                self._system(
                    message,
                    msg_type=MessageType.HANDOFF_DENIED,
                    target=sender,
                    task_id=task_id,
                ),
            )
            return

        claim = self.state.claims[task_id]
        self._waits.pop(to_agent, None)  # receiving the task clears any wait for it
        if self.journal is not None:
            record_claim(self.journal, claim)
        await self._record_handoff_progress(task_id, sender, to_agent, claim.note)
        granted = self._system(
            message,
            msg_type=MessageType.HANDOFF_GRANTED,
            task_id=task_id,
            owner=claim.owner,
            previous_owner=sender,
            note=claim.note,
            status=claim.status,
            worktree=claim.worktree,
            paths=list(claim.paths),
            epoch=claim.epoch,
            version=claim.version,
            lease_expires_at=claim.lease_expires_at,
            checkpoint=claim.checkpoint,
        )
        self._remember(data, granted)
        await self._broadcast(granted)

    async def _record_handoff_progress(
        self, task_id: str, from_agent: str, to_agent: str, context: str
    ) -> None:
        """Log a handoff as a progress note and broadcast it to observers."""
        text = f"handed off to {to_agent}: {context}" if context else f"handed off to {to_agent}"
        note = self.blackboard.note(task_id=task_id, author=from_agent, text=text)
        if self.journal is not None:
            record_ledger_progress(self.journal, note)
        await self._broadcast(
            self._system(
                f"Progress from {from_agent}",
                msg_type=MessageType.LEDGER_PROGRESS_POSTED,
                note=note.as_dict(),
            )
        )

    async def _handle_checkpoint(self, sender: str, data: dict[str, Any], websocket: Any) -> None:
        """Save a resume checkpoint on an owned task, acking the owner, or deny.

        The checkpoint is durable and survives lease expiry, so a later claimant
        of the same task resumes from it. The ack is private to the owner.
        """
        task_id = str(data.get("task_id") or "").strip()
        checkpoint = str(data.get("checkpoint") or data.get("payload") or "")
        ok, message = self.state.save_checkpoint(
            sender, task_id, checkpoint, epoch=self._optional_int(data, "epoch")
        )
        if ok:
            claim = self.state.claims[task_id]
            if self.journal is not None:
                record_claim(self.journal, claim)
            saved = self._system(
                message,
                msg_type=MessageType.CHECKPOINT_SAVED,
                target=sender,
                task_id=task_id,
                version=claim.version,
            )
            self._remember(data, saved)
            await self._send_json(websocket, saved)
            return
        await self._send_json(
            websocket,
            self._system(
                message,
                msg_type=MessageType.CHECKPOINT_DENIED,
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

    async def _handle_resume_request(
        self, sender: str, data: dict[str, Any], websocket: Any
    ) -> None:
        """Send the requesting agent every chat message after a cursor.

        Lets a reconnected agent catch up on exactly the messages it missed,
        identified by the ``since`` chat ``msg_id`` it last saw, rather than
        pulling a fixed-size history window.

        Parameters
        ----------
        sender : str
            The requesting agent.
        data : dict[str, Any]
            The request; ``since`` is the last ``msg_id`` the agent has seen.
        websocket : Any
            The requesting socket.
        """
        raw_since = data.get("since", 0)
        try:
            since = int(raw_since)
        except (TypeError, ValueError):
            since = 0
        tail = [m for m in self.chat_history if int(m.get("msg_id", 0)) > since]
        await self._send_json(
            websocket,
            self._system(
                "Resume snapshot",
                msg_type=MessageType.RESUME_SNAPSHOT,
                target=sender,
                since=since,
                messages=tail,
            ),
        )

    async def _handle_wait_request(self, sender: str, data: dict[str, Any], websocket: Any) -> None:
        """Register an advisory wait for a held task, refusing deadlock.

        The wait is advisory: the hub records that ``sender`` waits for whoever
        holds ``task_id`` and refuses the request if registering it would close a
        wait-for cycle (a hold-and-wait deadlock). The waiter is expected to retry
        its claim when the holder releases; the wait clears on its next successful
        claim or on disconnect.

        Parameters
        ----------
        sender : str
            The agent requesting to wait.
        data : dict[str, Any]
            The request; ``task_id`` is the task to wait for.
        websocket : Any
            The requesting socket.
        """
        task_id = str(data.get("task_id") or data.get("payload") or "").strip()
        claim = self.state.claims.get(task_id)
        if claim is None:
            await self._send_json(
                websocket,
                self._system(
                    f"Task '{task_id}' is not claimed; nothing to wait for.",
                    msg_type=MessageType.WAIT_DENIED,
                    target=sender,
                    task_id=task_id,
                ),
            )
            return
        holder = claim.owner
        if holder == sender:
            await self._send_json(
                websocket,
                self._system(
                    f"You already hold '{task_id}'.",
                    msg_type=MessageType.WAIT_DENIED,
                    target=sender,
                    task_id=task_id,
                ),
            )
            return
        if would_create_cycle(self._waits, sender, holder):
            await self._send_json(
                websocket,
                self._system(
                    f"Waiting for '{task_id}' held by {holder} would deadlock.",
                    msg_type=MessageType.WAIT_DENIED,
                    target=sender,
                    task_id=task_id,
                    holder=holder,
                ),
            )
            return
        self._waits[sender] = holder
        await self._send_json(
            websocket,
            self._system(
                f"Waiting for '{task_id}' held by {holder}.",
                msg_type=MessageType.WAIT_GRANTED,
                target=sender,
                task_id=task_id,
                holder=holder,
            ),
        )

    # -- shared blackboard ----------------------------------------------------

    async def _handle_ledger_task(self, sender: str, data: dict[str, Any], websocket: Any) -> None:
        """Declare or re-declare a plan task and broadcast it, or reject it."""
        task_id = str(data.get("task_id") or "").strip()
        raw_deps = data.get("depends_on")
        depends_on = [str(d) for d in raw_deps] if isinstance(raw_deps, list) else []
        ok, message = self.blackboard.post_task(
            task_id=task_id,
            title=str(data.get("title") or ""),
            author=sender,
            description=str(data.get("description") or ""),
            depends_on=depends_on,
            suggested_owner=str(data.get("suggested_owner") or ""),
        )
        if ok:
            task = self.blackboard.tasks[task_id]
            if self.journal is not None:
                record_ledger_task(self.journal, task)
            await self._broadcast(
                self._system(
                    message,
                    msg_type=MessageType.LEDGER_TASK_POSTED,
                    task=task.as_dict(),
                )
            )
            return
        await self._send_json(
            websocket, self._system(message, msg_type=MessageType.ERROR, target=sender)
        )

    async def _handle_ledger_task_update(
        self, sender: str, data: dict[str, Any], websocket: Any
    ) -> None:
        """Apply a plan-status/suggested-owner change and broadcast it, or reject."""
        task_id = str(data.get("task_id") or "").strip()
        status = data.get("status")
        suggested_owner = data.get("suggested_owner")
        ok, message = self.blackboard.update_task(
            task_id,
            status=str(status) if status is not None else None,
            suggested_owner=str(suggested_owner) if suggested_owner is not None else None,
        )
        if ok:
            task = self.blackboard.tasks[task_id]
            if self.journal is not None:
                record_ledger_task(self.journal, task)
            await self._broadcast(
                self._system(
                    message,
                    msg_type=MessageType.LEDGER_TASK_UPDATED,
                    task=task.as_dict(),
                )
            )
            return
        await self._send_json(
            websocket, self._system(message, msg_type=MessageType.ERROR, target=sender)
        )

    async def _handle_ledger_progress(
        self, sender: str, data: dict[str, Any], websocket: Any
    ) -> None:
        """Append a structured progress note and broadcast it, or reject the kind."""
        ok, result = self.blackboard.post_progress(
            task_id=str(data.get("task_id") or ""),
            author=sender,
            text=str(data.get("text") or data.get("payload") or ""),
            kind=str(data.get("kind") or "note"),
        )
        if not ok or not isinstance(result, ProgressNote):
            await self._send_json(
                websocket, self._system(str(result), msg_type=MessageType.ERROR, target=sender)
            )
            return
        if self.journal is not None:
            record_ledger_progress(self.journal, result)
        await self._broadcast(
            self._system(
                f"Progress from {sender}",
                msg_type=MessageType.LEDGER_PROGRESS_POSTED,
                note=result.as_dict(),
            )
        )

    async def _handle_board_request(self, sender: str, websocket: Any) -> None:
        """Send the requesting agent a snapshot of the shared blackboard."""
        await self._send_json(
            websocket,
            self._system(
                "Board snapshot",
                msg_type=MessageType.BOARD_SNAPSHOT,
                target=sender,
                board=self.blackboard.snapshot(),
            ),
        )

    # -- capability cards -----------------------------------------------------

    async def _handle_advertise(self, sender: str, data: dict[str, Any], websocket: Any) -> None:
        """Store an agent's capability card and broadcast it to the channel."""
        raw_skills = data.get("skills")
        raw_classes = data.get("task_classes")
        skills = [str(s) for s in raw_skills] if isinstance(raw_skills, list) else []
        task_classes = [str(c) for c in raw_classes] if isinstance(raw_classes, list) else []
        meta = data.get("meta")
        card = self.capabilities.advertise(
            sender,
            description=str(data.get("description") or ""),
            skills=skills,
            task_classes=task_classes,
            model=str(data.get("model") or ""),
            meta=meta if isinstance(meta, dict) else None,
        )
        await self._broadcast(
            self._system(
                f"Capability advertised by {sender}",
                msg_type=MessageType.CAPABILITY_ADVERTISED,
                agent=sender,
                card=card.as_dict(),
            )
        )

    async def _handle_manifest_request(self, sender: str, websocket: Any) -> None:
        """Send the requesting agent the capability manifest."""
        await self._send_json(
            websocket,
            self._system(
                "Manifest snapshot",
                msg_type=MessageType.MANIFEST_SNAPSHOT,
                target=sender,
                manifest=self.capabilities.manifest(),
            ),
        )

    def _drop_waits(self, agent: str) -> None:
        """Remove an agent's wait edge and any waits pointing at it."""
        self._waits.pop(agent, None)
        self._waits = {w: h for w, h in self._waits.items() if h != agent}

    # -- registration + name resolution --------------------------------------

    async def _authorise(self, sender: str, data: dict[str, Any], websocket: Any) -> bool:
        """Gate the first message from a socket on the shared-secret token.

        Authentication is checked once, when a socket first binds a name; later
        messages on an already-bound socket are trusted. With no authenticator
        the hub is open.

        Parameters
        ----------
        sender : str
            The agent name the connection claims.
        data : dict[str, Any]
            The decoded message; the token is read from its ``token`` field.
        websocket : Any
            The sender's socket, closed (code ``4010``) when authentication fails.

        Returns
        -------
        bool
            ``True`` when the message may proceed, ``False`` when it was refused
            and the socket closed.
        """
        if self.authenticator is None or self.socket_agent.get(websocket) is not None:
            return True
        ok, reason = self.authenticator.authenticate(str(data.get("token") or ""), sender)
        if ok:
            return True
        await self._send_json(
            websocket,
            self._system(reason, msg_type=MessageType.AUTH_DENIED, target=sender),
        )
        await websocket.close(code=4010, reason="auth denied")
        return False

    def _warn_if_exposed(self, host: str) -> None:
        """Warn when binding off-loopback with no token configured."""
        if not is_loopback_host(host) and self.authenticator is None:
            logger.warning(
                "Synapse Hub bound to non-loopback host %r with no token; set an "
                "authenticator (e.g. synapse hub --token ...) before exposing it.",
                host,
            )

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

        if not await self._authorise(sender, data, websocket):
            return

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

        if (
            msg_type != MessageType.HEARTBEAT
            and self.rate_limiter is not None
            and not self.rate_limiter.allow(sender)
        ):
            await self._send_json(
                websocket,
                self._system("Rate limit exceeded.", msg_type=MessageType.ERROR, target=sender),
            )
            return

        await self._route(sender, msg_type, data, websocket)

    async def _route(
        self, sender: str, msg_type: str, data: dict[str, Any], websocket: Any
    ) -> None:
        """Dispatch a parsed, sender-resolved message to its handler."""
        if await self._maybe_replay_duplicate(msg_type, data, websocket):
            return
        if msg_type == MessageType.CHAT:
            data["timestamp"] = float(data.get("timestamp") or time.time())
            data["type"] = MessageType.CHAT
            data["hub_id"] = self.hub_id
            data["msg_id"] = self._next_msg_id()
            self.chat_history.append(data.copy())
            if len(self.chat_history) > self.max_history:
                del self.chat_history[0]
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
        if msg_type == MessageType.RESUME_REQUEST:
            await self._handle_resume_request(sender, data, websocket)
            return
        if msg_type == MessageType.WAIT_REQUEST:
            await self._handle_wait_request(sender, data, websocket)
            return
        if msg_type == MessageType.TASK_UPDATE:
            await self._handle_task_update(sender, data, websocket)
            return
        if msg_type == MessageType.HANDOFF:
            await self._handle_handoff(sender, data, websocket)
            return
        if msg_type == MessageType.CHECKPOINT:
            await self._handle_checkpoint(sender, data, websocket)
            return
        if msg_type == MessageType.LEDGER_TASK:
            await self._handle_ledger_task(sender, data, websocket)
            return
        if msg_type == MessageType.LEDGER_TASK_UPDATE:
            await self._handle_ledger_task_update(sender, data, websocket)
            return
        if msg_type == MessageType.LEDGER_PROGRESS:
            await self._handle_ledger_progress(sender, data, websocket)
            return
        if msg_type == MessageType.BOARD_REQUEST:
            await self._handle_board_request(sender, websocket)
            return
        if msg_type == MessageType.ADVERTISE:
            await self._handle_advertise(sender, data, websocket)
            return
        if msg_type == MessageType.MANIFEST_REQUEST:
            await self._handle_manifest_request(sender, websocket)
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
            self._drop_waits(name)
            self.capabilities.forget(name)
            if self.rate_limiter is not None:
                self.rate_limiter.forget(name)
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
        self._warn_if_exposed(host)
        async with websockets.serve(self.handler, host, port, max_queue=DEFAULT_MAX_QUEUE):
            logger.info("Synapse Hub running on ws://%s:%d", host, port)
            await asyncio.Future()
