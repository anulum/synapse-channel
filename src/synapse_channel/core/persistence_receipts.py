# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — append-only SQLite event store for durable hub state
"""ReceiptTable implementation for the existing EventStore facade."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any

from synapse_channel.core.persistence_connection import StoreConnection

if TYPE_CHECKING:
    from synapse_channel.core.persistence import (
        DeliveryReceiptAggregate,
        DeliveryReceiptState,
        DeliveryReceiptTransition,
        PendingDeliveryNotification,
    )
from synapse_channel.core.persistence_receipt_codec import (
    encode_object,
    legacy_receipt_row,
    prepare_transition,
)

logger = logging.getLogger("synapse_channel.core.persistence")


class ReceiptTable:
    """Own this table family under the shared connection and lock."""

    def __init__(self, storage: StoreConnection) -> None:
        """Bind the existing owner connection without opening a second one."""
        self.storage = storage

    def commit_delivery_receipt_request(
        self,
        *,
        chat: Mapping[str, Any],
        requested: Mapping[str, Any],
        stage_hook: Callable[[str], None] | None = None,
    ) -> int:
        """Atomically commit a directed chat and its requested-receipt aggregate.

        The chat sequence is assigned inside the transaction and injected into
        the requested event and aggregate. A crash therefore leaves either both
        facts or neither; it can never retain a directed body without the fact
        that its sender requested a receipt.
        """
        stamp = time.time()
        chat_payload = dict(chat)
        request_payload = dict(requested)
        sender = str(request_payload.get("sender") or "")
        target = str(request_payload.get("target") or "")
        message_id = request_payload.get("message_id")
        if (
            not sender
            or not target
            or isinstance(message_id, bool)
            or (not isinstance(message_id, int))
        ):
            raise ValueError(
                "delivery receipt request requires sender, target, and integer message id"
            )
        client_msg_id = str(request_payload.get("client_msg_id") or "")
        hook = stage_hook or (lambda _stage: None)
        with self.storage.lock:
            self.storage.connection.execute("PRAGMA synchronous=FULL")
            committed = False
            try:
                self.storage.connection.execute("BEGIN IMMEDIATE")
                message_seq = self.storage.insert_event(stamp, "chat", encode_object(chat_payload))
                hook("after_chat_insert")
                request_payload["message_seq"] = message_seq
                request_seq = self.storage.insert_event(
                    stamp, "delivery_receipt_requested", encode_object(request_payload)
                )
                self.storage.connection.execute(
                    (
                        "INSERT INTO delivery_receipts (message_seq, sender, target, "
                        "message_id, client_msg_id, state, delivered, deferred, acked_by, "
                        "updated_event_seq) VALUES (?, ?, ?, ?, ?, ?, NULL, 0, '', "
                        "?)"
                    ),
                    (
                        message_seq,
                        sender,
                        target,
                        message_id,
                        client_msg_id,
                        "requested",
                        request_seq,
                    ),
                )
                hook("after_aggregate_insert")
                self.storage.connection.commit()
                committed = True
                hook("after_commit")
                return message_seq
            except BaseException:
                if not committed:
                    self.storage.connection.rollback()
                raise
            finally:
                self.storage.restore_normal(committed)

    def backfill(self) -> None:
        """Project legacy receipt events into the additive aggregate table once."""
        rows = self.storage.connection.execute(
            "SELECT seq, kind, payload FROM events WHERE kind IN "
            "('delivery_receipt_requested', 'delivery_receipt_immediate', "
            "'delivery_receipt_deferred', 'delivery_receipt_expired') ORDER BY "
            "seq"
        ).fetchall()
        for event_seq, kind, encoded in rows:
            projected = legacy_receipt_row(event_seq, kind, encoded)
            if projected is None:
                continue
            self.storage.connection.execute(
                (
                    "INSERT INTO delivery_receipts (message_seq, sender, target, "
                    "message_id, client_msg_id, state, delivered, deferred, acked_by, "
                    "updated_event_seq) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON "
                    "CONFLICT(message_seq) DO UPDATE SET state = excluded.state, delivered "
                    "= excluded.delivered, deferred = excluded.deferred, acked_by = "
                    "excluded.acked_by, updated_event_seq = "
                    "excluded.updated_event_seq"
                ),
                projected,
            )

    def commit_delivery_receipt_transition(
        self,
        *,
        kind: str,
        payload: Mapping[str, Any],
        notification: Mapping[str, Any],
        state: DeliveryReceiptState,
        companion: DeliveryReceiptTransition | None = None,
        stage_hook: Callable[[str], None] | None = None,
    ) -> str:
        """Commit one receipt transition and its stable notification outbox row.

        The outbox identity is deterministic for ``(message_seq, state)``. A
        process crash after WebSocket acceptance but before acknowledgement may
        therefore replay the same frame, and the sender can deduplicate it
        without the hub pretending to know whether a model consumed it.
        """
        from synapse_channel.core.persistence import DeliveryReceiptTransition

        transitions = [DeliveryReceiptTransition(kind, payload, notification, state)]
        if companion is not None:
            transitions.append(companion)
        prepared = [prepare_transition(item) for item in transitions]
        notification_id = prepared[0][0]
        hook = stage_hook or (lambda _stage: None)
        stamp = time.time()
        with self.storage.lock:
            self.storage.connection.execute("PRAGMA synchronous=FULL")
            committed = False
            try:
                self.storage.connection.execute("BEGIN IMMEDIATE")
                for transition in prepared:
                    self.storage.insert_receipt(transition, stamp)
                    hook("after_transition_insert")
                hook("after_outbox_insert")
                self.storage.connection.commit()
                committed = True
                hook("after_commit")
                return notification_id
            except BaseException:
                if not committed:
                    self.storage.connection.rollback()
                raise
            finally:
                self.storage.restore_normal(committed)

    def insert_transition(
        self,
        transition: tuple[str, int, str, dict[str, Any], dict[str, Any], DeliveryReceiptState],
        *,
        stamp: float,
    ) -> None:
        """Insert one validated aggregate/event/outbox transition in the open transaction."""
        notification_id, message_seq, kind, receipt, frame, state = transition
        current = self.storage.connection.execute(
            (
                "SELECT sender, target, message_id, state FROM delivery_receipts WHERE "
                "message_seq = ?"
            ),
            (message_seq,),
        ).fetchone()
        if current is None:
            raise KeyError("delivery receipt aggregate is absent")
        sender = str(current[0])
        allowed_from = {
            "immediate_delivered": {"requested"},
            "pending": {"requested"},
            "deferred": {"pending"},
            "expired": {"pending"},
        }[state]
        if str(current[3]) not in allowed_from:
            raise ValueError("delivery receipt transition is invalid from current state")
        if (
            str(receipt.get("sender") or "") != sender
            or str(receipt.get("target") or "") != str(current[1])
            or receipt.get("message_id") != int(current[2])
            or (str(frame.get("target") or "") != sender)
        ):
            raise ValueError("delivery receipt transition does not match its aggregate")
        event_seq = self.storage.insert_event(stamp, kind, encode_object(receipt))
        self.storage.connection.execute(
            (
                "UPDATE delivery_receipts SET state = ?, delivered = ?, deferred = ?, "
                "acked_by = ?, updated_event_seq = ? WHERE message_seq = "
                "?"
            ),
            (
                state,
                int(receipt["delivered"]),
                int(bool(receipt.get("deferred", False))),
                str(receipt.get("acked_by") or ""),
                event_seq,
                message_seq,
            ),
        )
        self.storage.connection.execute(
            (
                "INSERT INTO delivery_receipt_outbox (notification_id, message_seq, "
                "phase, sender, frame_json, attempts, delivered_at) VALUES (?, ?, ?, "
                "?, ?, 0, "
                "NULL)"
            ),
            (
                notification_id,
                message_seq,
                kind.removeprefix("delivery_receipt_"),
                sender,
                encode_object(frame),
            ),
        )

    def delivery_receipt_aggregate(self, message_seq: int) -> DeliveryReceiptAggregate | None:
        """Return the current durable receipt aggregate for one chat sequence."""
        from synapse_channel.core.persistence import DeliveryReceiptAggregate

        with self.storage.lock:
            row = self.storage.connection.execute(
                (
                    "SELECT message_seq, sender, target, message_id, client_msg_id, state, "
                    "delivered, deferred, acked_by, updated_event_seq FROM "
                    "delivery_receipts WHERE message_seq = "
                    "?"
                ),
                (message_seq,),
            ).fetchone()
        if row is None:
            return None
        delivered = None if row[6] is None else bool(row[6])
        return DeliveryReceiptAggregate(
            int(row[0]),
            str(row[1]),
            str(row[2]),
            int(row[3]),
            str(row[4]),
            str(row[5]),
            delivered,
            bool(row[7]),
            str(row[8]),
            int(row[9]),
        )

    def pending_delivery_notifications(
        self, *, sender: str | None = None, limit: int = 100
    ) -> tuple[PendingDeliveryNotification, ...]:
        """Return undelivered receipt frames in stable insertion order."""
        from synapse_channel.core.persistence import PendingDeliveryNotification

        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1 or (limit > 10000):
            raise ValueError("delivery notification limit must be an integer from 1 through 10000")
        query = (
            "SELECT notification_id, message_seq, phase, sender, frame_json, "
            "attempts FROM delivery_receipt_outbox WHERE delivered_at IS "
            "NULL"
        )
        parameters: tuple[object, ...]
        if sender is None:
            query += " ORDER BY rowid LIMIT ?"
            parameters = (limit,)
        else:
            query += " AND sender = ? ORDER BY rowid LIMIT ?"
            parameters = (sender, limit)
        with self.storage.lock:
            rows = self.storage.connection.execute(query, parameters).fetchall()
        result: list[PendingDeliveryNotification] = []
        for row in rows:
            frame = json.loads(str(row[4]))
            if not isinstance(frame, dict):
                raise ValueError("stored delivery notification must be a JSON object")
            result.append(
                PendingDeliveryNotification(
                    str(row[0]), int(row[1]), str(row[2]), str(row[3]), frame, int(row[5])
                )
            )
        return tuple(result)

    def mark_delivery_notification_attempt(self, notification_id: str, *, delivered: bool) -> None:
        """Record one sender-delivery attempt and settle it only on transport success."""
        if not notification_id:
            raise ValueError("delivery notification identity must be non-empty")
        with self.storage.lock:
            self.storage.connection.execute("PRAGMA synchronous=FULL")
            committed = False
            try:
                cursor = self.storage.connection.execute(
                    (
                        "UPDATE delivery_receipt_outbox SET attempts = attempts + 1, "
                        "delivered_at = CASE WHEN ? THEN COALESCE(delivered_at, ?) ELSE "
                        "delivered_at END WHERE notification_id = ? AND delivered_at IS "
                        "NULL"
                    ),
                    (int(delivered), time.time(), notification_id),
                )
                if cursor.rowcount != 1:
                    raise KeyError("delivery notification is absent or already settled")
                self.storage.connection.commit()
                committed = True
            except BaseException:
                self.storage.connection.rollback()
                raise
            finally:
                self.storage.restore_normal(committed)
