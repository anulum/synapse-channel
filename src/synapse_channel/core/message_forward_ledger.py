# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — durable outbox and inbound dedupe for cross-hub message forwarding
"""Durable state for cross-hub message forwarding, on both ends of a forward.

Three tables share the event store's SQLite connection and lock (as
:class:`~synapse_channel.core.delivery_persistence.DeliveryPersistence` does), so a durable hub
keeps them across restarts and an in-memory hub uses a private in-memory database:

* ``message_forward_outbox`` — origin side. A forwarded chat is written here before its first
  attempt and retried until the peer answers or it expires, so a peer outage never loses a
  message silently. Rows settle as ``accepted``, ``duplicate``, ``refused`` or ``expired``.
* ``message_forward_inbound`` — receiving side. ``(origin_hub, forward_id)`` maps to the request
  digest and the answer given, so a retried forward gets the same answer instead of a second
  copy, and a reused id with different content is refused.
* ``message_forward_remote_deliveries`` — origin side. Maps a delivery intent's operation key,
  issued by the receiving hub, to that hub and the local sender, so status and cancel requests
  route to the hub that owns the delivery.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, cast

logger = logging.getLogger("synapse.message_forward")

OutboxState = Literal["pending", "accepted", "duplicate", "refused", "expired"]
"""Lifecycle of one origin-side forwarded chat."""

SETTLED_STATES: frozenset[str] = frozenset({"accepted", "duplicate", "refused", "expired"})
"""Outbox states that end retries."""


def request_digest(fields: Mapping[str, Any]) -> str:
    """Return the SHA-256 digest of a forward request's canonical JSON encoding.

    Parameters
    ----------
    fields : Mapping[str, Any]
        The encoded request fields.

    Returns
    -------
    str
        Lower-case hexadecimal digest.
    """
    encoded = json.dumps(dict(fields), sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _encode(value: Mapping[str, Any]) -> str:
    """Encode a JSON object for storage."""
    return json.dumps(dict(value), sort_keys=True, separators=(",", ":"), allow_nan=False)


def _decode(raw: str) -> dict[str, Any]:
    """Decode a stored JSON object, refusing anything else."""
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("stored message-forward record is not a JSON object")
    return value


@dataclass(frozen=True, slots=True)
class OutboxEntry:
    """One origin-side forwarded chat and its delivery state.

    Attributes
    ----------
    forward_id : str
        Origin-unique idempotency key sent with every attempt.
    peer_hub : str
        The configured message peer the chat is forwarded to.
    sender : str
        Local seat that sent the chat.
    target : str
        Hub-qualified target as the sender wrote it.
    request : dict[str, Any]
        Encoded forward request fields, resent unchanged on every attempt.
    created_at : float
        Wall-clock time the chat was accepted locally.
    expires_at : float
        Wall-clock time after which retries stop and the entry expires.
    attempts : int
        Attempts made so far.
    next_attempt_at : float
        Earliest wall-clock time of the next attempt.
    state : OutboxState
        Current lifecycle state.
    result : dict[str, Any]
        The peer's encoded answer once settled, or the last error detail while pending.
    notify_sender : bool
        Whether the sender asked for a receipt and so is told the settled outcome.
    """

    forward_id: str
    peer_hub: str
    sender: str
    target: str
    request: dict[str, Any]
    created_at: float
    expires_at: float
    attempts: int
    next_attempt_at: float
    state: OutboxState
    result: dict[str, Any]
    notify_sender: bool = False


@dataclass(frozen=True, slots=True)
class InboundRecord:
    """The receiving hub's stored answer to one forward.

    Attributes
    ----------
    digest : str
        Digest of the request that produced the answer.
    result : dict[str, Any]
        The encoded answer, replayed for a duplicate.
    """

    digest: str
    result: dict[str, Any]


@dataclass(frozen=True, slots=True)
class RemoteDelivery:
    """Where an origin hub routes status and cancel requests for one forwarded delivery.

    Attributes
    ----------
    operation_key : str
        The receiving hub's operation key for the delivery intent.
    peer_hub : str
        The hub that admitted and owns the delivery.
    sender : str
        The local seat that requested it; only this seat may query or cancel it.
    target : str
        The hub-qualified target the sender addressed.
    """

    operation_key: str
    peer_hub: str
    sender: str
    target: str


@dataclass(frozen=True, slots=True)
class PendingPeerSummary:
    """Outbox backlog towards one peer hub.

    Attributes
    ----------
    pending : int
        Forwarded chats not yet answered by the peer.
    oldest_pending_seconds : float
        Age of the oldest of them, measured from when the chat was accepted locally.
    """

    pending: int
    oldest_pending_seconds: float


class MessageForwardLedger:
    """Durable outbox, inbound dedupe and remote-delivery routes for message forwarding.

    Parameters
    ----------
    connection : sqlite3.Connection
        The connection to create the tables on; shared with the event store when durable.
    lock : Any
        The re-entrant lock guarding ``connection``; shared with the event store when
        durable. :meth:`expire_due` writes and re-reads rows while holding it.
    """

    def __init__(self, connection: sqlite3.Connection, lock: Any) -> None:
        self._conn = connection
        self._lock = lock
        with self._lock:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS message_forward_outbox ("
                "forward_id TEXT PRIMARY KEY, peer_hub TEXT NOT NULL, sender TEXT NOT NULL, "
                "target TEXT NOT NULL, request_json TEXT NOT NULL, created_at REAL NOT NULL, "
                "expires_at REAL NOT NULL, attempts INTEGER NOT NULL, "
                "next_attempt_at REAL NOT NULL, state TEXT NOT NULL, result_json TEXT NOT NULL, "
                "notify_sender INTEGER NOT NULL, sender_notified_at REAL)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS message_forward_outbox_due_idx "
                "ON message_forward_outbox(state, next_attempt_at)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS message_forward_outbox_notify_idx "
                "ON message_forward_outbox(sender, notify_sender, sender_notified_at)"
            )
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS message_forward_inbound ("
                "origin_hub TEXT NOT NULL, forward_id TEXT NOT NULL, digest TEXT NOT NULL, "
                "result_json TEXT NOT NULL, received_at REAL NOT NULL, "
                "PRIMARY KEY(origin_hub, forward_id))"
            )
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS message_forward_remote_deliveries ("
                "operation_key TEXT PRIMARY KEY, peer_hub TEXT NOT NULL, sender TEXT NOT NULL, "
                "target TEXT NOT NULL, created_at REAL NOT NULL)"
            )
            self._conn.commit()

    @classmethod
    def in_memory(cls) -> MessageForwardLedger:
        """Return a ledger on a private in-memory database, for a hub without a journal.

        Returns
        -------
        MessageForwardLedger
            A ledger whose state lives only as long as the process.
        """
        return cls(sqlite3.connect(":memory:", check_same_thread=False), threading.RLock())

    def _durable_write(self, sql: str, params: tuple[Any, ...]) -> int:
        """Run one write under the lock with a full sync, and return the changed row count.

        The outbox and inbound answers back promises made to agents ("forward pending",
        "already accepted"), so they are synced like the delivery ledger's transitions.
        """
        with self._lock:
            self._conn.execute("PRAGMA synchronous=FULL")
            try:
                cursor = self._conn.execute(sql, params)
                self._conn.commit()
            finally:
                self._conn.execute("PRAGMA synchronous=NORMAL")
        return int(cursor.rowcount)

    # --- origin side: outbox ---------------------------------------------------------------

    def enqueue(
        self,
        *,
        forward_id: str,
        peer_hub: str,
        sender: str,
        target: str,
        request: Mapping[str, Any],
        now: float,
        expires_at: float,
        notify_sender: bool = False,
    ) -> OutboxEntry:
        """Store one forwarded chat as ``pending`` before its first attempt.

        Parameters
        ----------
        forward_id : str
            Origin-unique idempotency key.
        peer_hub : str
            Destination message peer.
        sender : str
            Local sending seat.
        target : str
            Hub-qualified target.
        request : Mapping[str, Any]
            Encoded forward request fields.
        now : float
            Current wall-clock time.
        expires_at : float
            Wall-clock time after which retries stop.
        notify_sender : bool, optional
            Tell the sender the settled outcome (it asked for a receipt).

        Returns
        -------
        OutboxEntry
            The stored entry.
        """
        encoded = _encode(request)
        self._durable_write(
            "INSERT INTO message_forward_outbox (forward_id, peer_hub, sender, target, "
            "request_json, created_at, expires_at, attempts, next_attempt_at, state, "
            "result_json, notify_sender) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, 'pending', '{}', ?)",
            (
                forward_id,
                peer_hub,
                sender,
                target,
                encoded,
                now,
                expires_at,
                now,
                int(notify_sender),
            ),
        )
        return OutboxEntry(
            forward_id=forward_id,
            peer_hub=peer_hub,
            sender=sender,
            target=target,
            request=_decode(encoded),
            created_at=now,
            expires_at=expires_at,
            attempts=0,
            next_attempt_at=now,
            state="pending",
            result={},
            notify_sender=notify_sender,
        )

    def outbox_entry(self, forward_id: str) -> OutboxEntry | None:
        """Return one outbox entry, or ``None`` when it does not exist.

        Parameters
        ----------
        forward_id : str
            The entry's forward id.

        Returns
        -------
        OutboxEntry or None
            The entry.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT forward_id, peer_hub, sender, target, request_json, created_at, "
                "expires_at, attempts, next_attempt_at, state, result_json, notify_sender "
                "FROM message_forward_outbox WHERE forward_id = ?",
                (forward_id,),
            ).fetchone()
        return None if row is None else _outbox_entry(row)

    def due(self, now: float, *, limit: int = 64) -> list[OutboxEntry]:
        """Return pending entries whose next attempt is due and which have not expired.

        Parameters
        ----------
        now : float
            Current wall-clock time.
        limit : int, optional
            Maximum entries returned, oldest attempt first.

        Returns
        -------
        list[OutboxEntry]
            Due entries.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT forward_id, peer_hub, sender, target, request_json, created_at, "
                "expires_at, attempts, next_attempt_at, state, result_json, notify_sender "
                "FROM message_forward_outbox "
                "WHERE state = 'pending' AND next_attempt_at <= ? AND expires_at > ? "
                "ORDER BY next_attempt_at, created_at LIMIT ?",
                (now, now, max(1, int(limit))),
            ).fetchall()
        return [_outbox_entry(row) for row in rows]

    def record_failed_attempt(
        self, forward_id: str, *, next_attempt_at: float, error: str
    ) -> OutboxEntry | None:
        """Count one failed transport attempt and schedule the next.

        Parameters
        ----------
        forward_id : str
            The pending entry.
        next_attempt_at : float
            Earliest wall-clock time of the next attempt.
        error : str
            Short failure description kept for status views.

        Returns
        -------
        OutboxEntry or None
            The entry as now stored (unchanged when it had already settled), or ``None`` when
            it does not exist.
        """
        with self._lock:
            self._conn.execute(
                "UPDATE message_forward_outbox SET attempts = attempts + 1, "
                "next_attempt_at = ?, result_json = ? WHERE forward_id = ? AND state = 'pending'",
                (next_attempt_at, _encode({"last_error": error[:512]}), forward_id),
            )
            self._conn.commit()
        return self.outbox_entry(forward_id)

    def settle(
        self, forward_id: str, state: OutboxState, result: Mapping[str, Any]
    ) -> OutboxEntry | None:
        """Settle a pending entry with the peer's answer or its expiry.

        Parameters
        ----------
        forward_id : str
            The pending entry.
        state : OutboxState
            A settled state: ``accepted``, ``duplicate``, ``refused`` or ``expired``.
        result : Mapping[str, Any]
            The peer's encoded answer, or the expiry detail.

        Returns
        -------
        OutboxEntry or None
            The entry as now stored; an entry that had already settled keeps its first
            settlement. ``None`` when it does not exist.

        Raises
        ------
        ValueError
            If ``state`` is not a settled state.
        """
        if state not in SETTLED_STATES:
            raise ValueError(f"{state!r} is not a settled outbox state")
        self._durable_write(
            "UPDATE message_forward_outbox SET state = ?, result_json = ?, "
            "attempts = attempts + ? WHERE forward_id = ? AND state = 'pending'",
            (state, _encode(result), 0 if state == "expired" else 1, forward_id),
        )
        return self.outbox_entry(forward_id)

    def expire_due(self, now: float) -> list[OutboxEntry]:
        """Expire every pending entry whose deadline has passed.

        Selection and the state change happen under one lock, so an entry settled by a
        concurrent answer is never reported as expired.

        Parameters
        ----------
        now : float
            Current wall-clock time.

        Returns
        -------
        list[OutboxEntry]
            Entries this call moved to ``expired``.
        """
        detail = _encode({"detail": "peer did not answer before expiry"})
        with self._lock:
            ids = [
                str(row[0])
                for row in self._conn.execute(
                    "SELECT forward_id FROM message_forward_outbox "
                    "WHERE state = 'pending' AND expires_at <= ?",
                    (now,),
                ).fetchall()
            ]
            for forward_id in ids:
                self._durable_write(
                    "UPDATE message_forward_outbox SET state = 'expired', result_json = ? "
                    "WHERE forward_id = ?",
                    (detail, forward_id),
                )
            entries = [self.outbox_entry(forward_id) for forward_id in ids]
        return [entry for entry in entries if entry is not None]

    def pending_sender_notifications(self, sender: str) -> list[OutboxEntry]:
        """Return settled entries whose sender asked for a receipt and has not had it.

        Parameters
        ----------
        sender : str
            The local seat to notify.

        Returns
        -------
        list[OutboxEntry]
            Settled, unnotified entries for ``sender``, oldest first.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT forward_id, peer_hub, sender, target, request_json, created_at, "
                "expires_at, attempts, next_attempt_at, state, result_json, notify_sender "
                "FROM message_forward_outbox WHERE sender = ? AND notify_sender = 1 "
                "AND sender_notified_at IS NULL AND state != 'pending' ORDER BY created_at",
                (sender,),
            ).fetchall()
        return [_outbox_entry(row) for row in rows]

    def mark_sender_notified(self, forward_id: str, *, now: float) -> bool:
        """Record that the sender received the settled outcome of ``forward_id``.

        Parameters
        ----------
        forward_id : str
            The settled entry.
        now : float
            Current wall-clock time.

        Returns
        -------
        bool
            ``True`` when this call recorded the notification.
        """
        return bool(
            self._durable_write(
                "UPDATE message_forward_outbox SET sender_notified_at = ? "
                "WHERE forward_id = ? AND sender_notified_at IS NULL AND state != 'pending'",
                (now, forward_id),
            )
        )

    def pending_counts(self) -> dict[str, int]:
        """Return the number of pending forwarded chats per peer hub.

        Returns
        -------
        dict[str, int]
            Peer hub id to pending count; peers with none are omitted.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT peer_hub, COUNT(*) FROM message_forward_outbox "
                "WHERE state = 'pending' GROUP BY peer_hub"
            ).fetchall()
        return {str(peer): int(count) for peer, count in rows}

    def pending_summary(self, now: float) -> dict[str, PendingPeerSummary]:
        """Return the pending backlog per peer hub, for health and metrics.

        Parameters
        ----------
        now : float
            Current wall-clock time; ages are measured against it and never negative.

        Returns
        -------
        dict[str, PendingPeerSummary]
            Peer hub id to its backlog; peers with nothing pending are omitted.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT peer_hub, COUNT(*), MIN(created_at) FROM message_forward_outbox "
                "WHERE state = 'pending' GROUP BY peer_hub"
            ).fetchall()
        return {
            str(peer): PendingPeerSummary(
                pending=int(count), oldest_pending_seconds=max(0.0, now - float(oldest))
            )
            for peer, count, oldest in rows
        }

    # --- receiving side: inbound dedupe ----------------------------------------------------

    def inbound(self, origin_hub: str, forward_id: str) -> InboundRecord | None:
        """Return the stored answer for a forward already handled, if any.

        Parameters
        ----------
        origin_hub : str
            Authenticated id of the forwarding peer.
        forward_id : str
            The forward's id.

        Returns
        -------
        InboundRecord or None
            The stored digest and answer.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT digest, result_json FROM message_forward_inbound "
                "WHERE origin_hub = ? AND forward_id = ?",
                (origin_hub, forward_id),
            ).fetchone()
        if row is None:
            return None
        return InboundRecord(digest=str(row[0]), result=_decode(str(row[1])))

    def record_inbound(
        self,
        origin_hub: str,
        forward_id: str,
        *,
        digest: str,
        result: Mapping[str, Any],
        now: float,
    ) -> None:
        """Store the answer given to a forward so a retry replays it.

        Parameters
        ----------
        origin_hub : str
            Authenticated id of the forwarding peer.
        forward_id : str
            The forward's id.
        digest : str
            Digest of the handled request.
        result : Mapping[str, Any]
            The encoded answer.
        now : float
            Current wall-clock time.
        """
        self._durable_write(
            "INSERT OR IGNORE INTO message_forward_inbound "
            "(origin_hub, forward_id, digest, result_json, received_at) VALUES (?, ?, ?, ?, ?)",
            (origin_hub, forward_id, digest, _encode(result), now),
        )

    # --- origin side: remote delivery routes -----------------------------------------------

    def remember_remote_delivery(self, delivery: RemoteDelivery, *, now: float) -> None:
        """Record which peer owns a forwarded delivery intent.

        Parameters
        ----------
        delivery : RemoteDelivery
            The route to remember; an existing route for the same key is kept.
        now : float
            Current wall-clock time.
        """
        self._durable_write(
            "INSERT OR IGNORE INTO message_forward_remote_deliveries "
            "(operation_key, peer_hub, sender, target, created_at) VALUES (?, ?, ?, ?, ?)",
            (delivery.operation_key, delivery.peer_hub, delivery.sender, delivery.target, now),
        )

    def remote_delivery(self, operation_key: str) -> RemoteDelivery | None:
        """Return the route for a forwarded delivery intent, or ``None``.

        Parameters
        ----------
        operation_key : str
            The receiving hub's operation key.

        Returns
        -------
        RemoteDelivery or None
            The route.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT operation_key, peer_hub, sender, target "
                "FROM message_forward_remote_deliveries WHERE operation_key = ?",
                (operation_key,),
            ).fetchone()
        if row is None:
            return None
        return RemoteDelivery(
            operation_key=str(row[0]), peer_hub=str(row[1]), sender=str(row[2]), target=str(row[3])
        )


def _outbox_entry(row: tuple[Any, ...]) -> OutboxEntry:
    """Project one outbox row, refusing an unknown state."""
    state = str(row[9])
    if state != "pending" and state not in SETTLED_STATES:
        raise ValueError(f"stored message-forward outbox state {state!r} is unknown")
    return OutboxEntry(
        forward_id=str(row[0]),
        peer_hub=str(row[1]),
        sender=str(row[2]),
        target=str(row[3]),
        request=_decode(str(row[4])),
        created_at=float(row[5]),
        expires_at=float(row[6]),
        attempts=int(row[7]),
        next_attempt_at=float(row[8]),
        state=cast("OutboxState", state),
        result=_decode(str(row[10])),
        notify_sender=bool(row[11]),
    )
