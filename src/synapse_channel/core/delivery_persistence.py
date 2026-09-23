# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — atomic version-three delivery request ledger
"""Persist session-bound delivery intents, transitions, and notification identity.

The aggregate and append-only event row are committed in the same SQLite
transaction. A duplicate sender-scoped request or mutation replays the stored
disposition only when its canonical digest matches. Pending recipient offers
remain queryable after a socket write, allowing safe at-least-once redelivery
after a disconnect or process restart.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any, Literal

from synapse_channel.core.delivery_modes import (
    TERMINAL_STAGES,
    DeliveryIntent,
    DeliveryLifecycle,
    DeliveryQuality,
    DeliveryRefusal,
    DeliveryStage,
)

logger = logging.getLogger("synapse.delivery")

_DELIVERY_ROW_SELECT = (
    "SELECT operation_key, sender, idempotency_key, request_digest, request_json, "
    "target, target_incarnation, deadline, selected_mode, quality, stage, "
    "cancel_requested, boundary_delivered, explicitly_acknowledged, ordinal, "
    "latest_event_seq FROM delivery_requests WHERE "
)
_DELIVERY_ROW_QUERIES = {
    "key": _DELIVERY_ROW_SELECT + "operation_key = ?",
    "idempotency": _DELIVERY_ROW_SELECT + "sender = ? AND idempotency_key = ?",
    "either": _DELIVERY_ROW_SELECT + "operation_key = ? OR (sender = ? AND idempotency_key = ?)",
}

DELIVERY_ACCEPTED = "delivery_intent_accepted"
DELIVERY_QUEUED = "delivery_intent_queued"
DELIVERY_TRANSITION = "delivery_intent_transition"
DELIVERY_CANCEL_REQUESTED = "delivery_cancel_requested"
DELIVERY_EVENT_KINDS = frozenset(
    {DELIVERY_ACCEPTED, DELIVERY_QUEUED, DELIVERY_TRANSITION, DELIVERY_CANCEL_REQUESTED}
)
MAX_OPEN_DELIVERIES_PER_RECIPIENT = 128
MAX_OPEN_DELIVERIES_PER_SENDER_RECIPIENT = 16


def _encode(value: Mapping[str, Any]) -> str:
    """Encode one event or frame with a stable digest-safe JSON representation."""
    return json.dumps(
        dict(value), ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _stage(value: object) -> DeliveryStage:
    """Refuse an unknown persisted stage before projecting it into live state."""
    if value == "accepted":
        return "accepted"
    if value == "queued":
        return "queued"
    if value == "boundary_delivered":
        return "boundary_delivered"
    if value == "acknowledged":
        return "acknowledged"
    if value == "completed":
        return "completed"
    if value == "failed":
        return "failed"
    if value == "rejected":
        return "rejected"
    if value == "expired":
        return "expired"
    if value == "cancelled":
        return "cancelled"
    if value == "superseded":
        return "superseded"
    raise DeliveryRefusal("replay_incompatible", "stored delivery stage is unknown")


@dataclass(frozen=True)
class StoredDelivery:
    """Current durable aggregate for one sender-scoped delivery intent."""

    operation_key: str
    sender: str
    idempotency_key: str
    request_digest: str
    request: dict[str, Any]
    selected_mode: str
    quality: DeliveryQuality
    stage: DeliveryStage
    cancel_requested: bool
    boundary_delivered: bool
    explicitly_acknowledged: bool
    ordinal: int
    latest_event_seq: int


@dataclass(frozen=True)
class DeliveryWrite:
    """The exact stored state after an insertion, replay, or conflict."""

    disposition: Literal["inserted", "replayed", "conflict"]
    record: StoredDelivery


class DeliveryPersistence:
    """A version-three aggregate sharing the event store's connection and lock."""

    def __init__(self, connection: Any, lock: Any) -> None:
        self._conn = connection
        self._lock = lock
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS delivery_requests ("
            "operation_key TEXT PRIMARY KEY, sender TEXT NOT NULL, "
            "idempotency_key TEXT NOT NULL, request_digest TEXT NOT NULL, "
            "request_json TEXT NOT NULL, target TEXT NOT NULL, "
            "target_incarnation TEXT NOT NULL, deadline REAL NOT NULL, "
            "selected_mode TEXT NOT NULL, quality TEXT NOT NULL, stage TEXT NOT NULL, "
            "cancel_requested INTEGER NOT NULL, boundary_delivered INTEGER NOT NULL, "
            "explicitly_acknowledged INTEGER NOT NULL, ordinal INTEGER NOT NULL, "
            "latest_event_seq INTEGER NOT NULL, "
            "UNIQUE(sender, idempotency_key))"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS delivery_pending_idx "
            "ON delivery_requests(target, target_incarnation, stage, deadline)"
        )
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS delivery_mutations ("
            "operation_key TEXT NOT NULL, mutation_id TEXT NOT NULL, "
            "mutation_digest TEXT NOT NULL, event_seq INTEGER NOT NULL, "
            "PRIMARY KEY(operation_key, mutation_id))"
        )
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS delivery_notifications ("
            "notification_id TEXT PRIMARY KEY, operation_key TEXT NOT NULL, "
            "audience TEXT NOT NULL, frame_json TEXT NOT NULL, "
            "attempts INTEGER NOT NULL DEFAULT 0, delivered_at REAL, retired_at REAL)"
        )
        columns = {
            str(row[1]) for row in self._conn.execute("PRAGMA table_info(delivery_notifications)")
        }
        if "retired_at" not in columns:
            self._conn.execute("ALTER TABLE delivery_notifications ADD COLUMN retired_at REAL")
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS delivery_notification_pending_idx "
            "ON delivery_notifications(audience, delivered_at)"
        )
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS delivery_quarantine ("
            "operation_key TEXT PRIMARY KEY, reason_code TEXT NOT NULL, observed_at REAL NOT NULL)"
        )

    def verify_origin_hub(self, hub_id: str) -> None:
        """Refuse a delivery journal opened under another stable hub identity."""
        with self._lock:
            for (raw,) in self._conn.execute("SELECT request_json FROM delivery_requests"):
                try:
                    request = json.loads(raw)
                except (TypeError, ValueError) as exc:
                    raise DeliveryRefusal(
                        "replay_incompatible", "stored delivery request is malformed"
                    ) from exc
                if not isinstance(request, dict) or request.get("origin_hub") != hub_id:
                    raise DeliveryRefusal(
                        "hub_identity_mismatch",
                        "delivery journal belongs to a different stable hub id",
                    )

    def quarantine(self, operation_key: str, reason_code: str) -> None:
        """Retain a refused record for operator recovery without repeated retries."""
        with self._lock:
            committed = False
            try:
                self._begin()
                self._conn.execute(
                    "INSERT OR IGNORE INTO delivery_quarantine "
                    "(operation_key, reason_code, observed_at) VALUES (?, ?, ?)",
                    (operation_key, reason_code, time.time()),
                )
                self._conn.commit()
                committed = True
            except BaseException:
                if not committed:
                    self._conn.rollback()
                raise
            finally:
                self._finish(committed)

    def verify_replay(self) -> None:
        """Fail closed when the indexed state disagrees with its event stream."""
        from synapse_channel.core.delivery_replay import verify_delivery_replay

        with self._lock:
            verify_delivery_replay(self._conn)

    @staticmethod
    def _record(row: tuple[Any, ...]) -> StoredDelivery:
        """Decode one aggregate row without accepting a malformed stored profile."""
        request = json.loads(row[4])
        if not isinstance(request, dict) or request.get("profile") != 3:
            raise DeliveryRefusal("replay_incompatible", "stored delivery profile is incompatible")
        quality = row[9]
        if quality not in ("native", "emulated"):
            raise DeliveryRefusal("replay_incompatible", "stored delivery quality is unknown")
        return StoredDelivery(
            operation_key=str(row[0]),
            sender=str(row[1]),
            idempotency_key=str(row[2]),
            request_digest=str(row[3]),
            request=request,
            selected_mode=str(row[8]),
            quality=quality,
            stage=_stage(row[10]),
            cancel_requested=bool(row[11]),
            boundary_delivered=bool(row[12]),
            explicitly_acknowledged=bool(row[13]),
            ordinal=int(row[14]),
            latest_event_seq=int(row[15]),
        )

    def _fetch(self, selector: str, args: tuple[object, ...]) -> StoredDelivery | None:
        """Read a single indexed aggregate while the caller owns the store lock."""
        row = self._conn.execute(_DELIVERY_ROW_QUERIES[selector], args).fetchone()
        return None if row is None else self._record(row)

    def get(self, operation_key: str) -> StoredDelivery | None:
        """Return one current durable state by its server-derived operation key."""
        with self._lock:
            return self._fetch("key", (operation_key,))

    def find_by_idempotency(self, sender: str, idempotency_key: str) -> StoredDelivery | None:
        """Resolve a sender's existing request before checking live recipient state."""
        with self._lock:
            return self._fetch("idempotency", (sender, idempotency_key))

    def pending_for(
        self, target: str, incarnation: str, *, after_seq: int = 0, limit: int = 128
    ) -> tuple[StoredDelivery, ...]:
        """Return queued offers in journal order for this exact live session."""
        if limit < 1 or limit > 128:
            raise ValueError("delivery page limit must be between 1 and 128")
        with self._lock:
            rows = self._conn.execute(
                "SELECT operation_key, sender, idempotency_key, request_digest, "
                "request_json, target, target_incarnation, deadline, selected_mode, "
                "quality, stage, cancel_requested, boundary_delivered, "
                "explicitly_acknowledged, ordinal, latest_event_seq "
                "FROM delivery_requests WHERE target = ? AND target_incarnation = ? "
                "AND stage = 'queued' AND latest_event_seq > ? "
                "ORDER BY latest_event_seq LIMIT ?",
                (target, incarnation, after_seq, limit),
            ).fetchall()
            return tuple(self._record(row) for row in rows)

    def due_for_expiry(
        self, now: float, *, after_key: str = "", limit: int = 128
    ) -> tuple[StoredDelivery, ...]:
        """Page unfinished requests whose deadline elapsed in key order."""
        if limit < 1 or limit > 128:
            raise ValueError("delivery page limit must be between 1 and 128")
        with self._lock:
            rows = self._conn.execute(
                "SELECT operation_key, sender, idempotency_key, request_digest, "
                "request_json, target, target_incarnation, deadline, selected_mode, "
                "quality, stage, cancel_requested, boundary_delivered, "
                "explicitly_acknowledged, ordinal, latest_event_seq "
                "FROM delivery_requests WHERE operation_key > ? AND deadline <= ? "
                "AND stage IN ('queued', 'boundary_delivered', 'acknowledged') "
                "AND operation_key NOT IN (SELECT operation_key FROM delivery_quarantine) "
                "ORDER BY operation_key LIMIT ?",
                (after_key, now, limit),
            ).fetchall()
            return tuple(self._record(row) for row in rows)

    def open_for_other_incarnations(
        self, target: str, incarnation: str, *, after_key: str = "", limit: int = 128
    ) -> tuple[StoredDelivery, ...]:
        """Page intents made for older processes using the same agent name."""
        if limit < 1 or limit > 128:
            raise ValueError("delivery page limit must be between 1 and 128")
        with self._lock:
            rows = self._conn.execute(
                "SELECT operation_key, sender, idempotency_key, request_digest, "
                "request_json, target, target_incarnation, deadline, selected_mode, "
                "quality, stage, cancel_requested, boundary_delivered, "
                "explicitly_acknowledged, ordinal, latest_event_seq "
                "FROM delivery_requests WHERE target = ? AND target_incarnation != ? "
                "AND operation_key > ? AND stage IN "
                "('queued', 'boundary_delivered', 'acknowledged') "
                "AND operation_key NOT IN (SELECT operation_key FROM delivery_quarantine) "
                "ORDER BY operation_key LIMIT ?",
                (target, incarnation, after_key, limit),
            ).fetchall()
            return tuple(self._record(row) for row in rows)

    def pending_notifications(
        self, audience: str, *, after_rowid: int = 0, limit: int = 128
    ) -> tuple[tuple[int, str, dict[str, Any]], ...]:
        """Page stable unsent notifications for one authenticated audience."""
        if limit < 1 or limit > 128:
            raise ValueError("delivery page limit must be between 1 and 128")
        with self._lock:
            rows = self._conn.execute(
                "SELECT rowid, notification_id, frame_json FROM delivery_notifications "
                "WHERE audience = ? AND delivered_at IS NULL AND retired_at IS NULL "
                "AND rowid > ? "
                "ORDER BY rowid LIMIT ?",
                (audience, after_rowid, limit),
            ).fetchall()
        return tuple((int(rowid), str(identity), json.loads(raw)) for rowid, identity, raw in rows)

    def mark_notification_delivered(self, notification_id: str) -> None:
        """Record a successful socket write; a crash before this may resend it."""
        with self._lock:
            self._conn.execute(
                "UPDATE delivery_notifications SET attempts = attempts + 1, "
                "delivered_at = COALESCE(delivered_at, ?) WHERE notification_id = ?",
                (time.time(), notification_id),
            )
            self._conn.commit()

    def _begin(self) -> None:
        """Begin a synced write transaction under the event store's shared lock."""
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.execute("BEGIN IMMEDIATE")

    def _finish(self, committed: bool) -> None:
        """Restore ordinary sync without contradicting an already committed write."""
        try:
            self._conn.execute("PRAGMA synchronous=NORMAL")
        except BaseException:
            if not committed:
                raise
            logger.exception("Could not restore SQLite synchronous=NORMAL")

    def _event(self, kind: str, payload: Mapping[str, Any], stamp: float) -> int:
        """Insert one event inside the active transaction and return its sequence."""
        cursor = self._conn.execute(
            "INSERT INTO events (ts, kind, payload) VALUES (?, ?, ?)",
            (stamp, kind, _encode(payload)),
        )
        return int(cursor.lastrowid or 0)

    def _notification(
        self, notification_id: str, operation_key: str, audience: str, frame: Mapping[str, Any]
    ) -> None:
        """Insert the exact notification identity and frame before publishing it."""
        self._conn.execute(
            "INSERT INTO delivery_notifications "
            "(notification_id, operation_key, audience, frame_json) VALUES (?, ?, ?, ?)",
            (notification_id, operation_key, audience, _encode(frame)),
        )

    def create(
        self,
        intent: DeliveryIntent,
        *,
        selected_mode: str,
        quality: DeliveryQuality,
        offer: Mapping[str, Any],
    ) -> DeliveryWrite:
        """Atomically admit and queue one request with a stable recipient offer.

        A repeated request id or idempotency key returns the existing row only
        when both refer to the same canonical content. No offer is published by
        this method; the caller sends it after the commit.
        """
        request = {"profile": 3, **asdict(intent)}
        key = intent.operation_key
        digest = intent.digest
        notification_id = f"delivery:{key}:1"
        if selected_mode not in (intent.mode, *intent.allowed_fallbacks) or quality not in (
            "native",
            "emulated",
        ):
            raise DeliveryRefusal("invalid_shape", "selected delivery capability is inconsistent")
        if (
            offer.get("notification_id") != notification_id
            or offer.get("operation_key") != key
            or offer.get("target") != intent.target
            or offer.get("target_incarnation") != intent.target_incarnation
        ):
            raise DeliveryRefusal("invalid_shape", "recipient offer does not bind the request")
        with self._lock:
            committed = False
            try:
                self._begin()
                existing = self._fetch("either", (key, intent.sender, intent.idempotency_key))
                if existing is not None:
                    self._conn.rollback()
                    disposition: Literal["replayed", "conflict"] = (
                        "replayed"
                        if existing.operation_key == key and existing.request_digest == digest
                        else "conflict"
                    )
                    return DeliveryWrite(disposition, existing)
                open_count = self._conn.execute(
                    "SELECT COUNT(*) FROM delivery_requests WHERE target = ? "
                    "AND target_incarnation = ? AND stage IN "
                    "('queued', 'boundary_delivered', 'acknowledged')",
                    (intent.target, intent.target_incarnation),
                ).fetchone()[0]
                if int(open_count) >= MAX_OPEN_DELIVERIES_PER_RECIPIENT:
                    raise DeliveryRefusal(
                        "recipient_queue_full", "recipient delivery queue is full"
                    )
                sender_open_count = self._conn.execute(
                    "SELECT COUNT(*) FROM delivery_requests WHERE target = ? "
                    "AND target_incarnation = ? AND sender = ? AND stage IN "
                    "('queued', 'boundary_delivered', 'acknowledged')",
                    (intent.target, intent.target_incarnation, intent.sender),
                ).fetchone()[0]
                if int(sender_open_count) >= MAX_OPEN_DELIVERIES_PER_SENDER_RECIPIENT:
                    raise DeliveryRefusal(
                        "recipient_queue_full", "recipient delivery queue is full"
                    )
                stamp = time.time()
                self._event(
                    DELIVERY_ACCEPTED,
                    {"profile": 3, "operation_key": key, "digest": digest, "request": request},
                    stamp,
                )
                queued_seq = self._event(
                    DELIVERY_QUEUED,
                    {
                        "profile": 3,
                        "operation_key": key,
                        "ordinal": 1,
                        "stage": "queued",
                        "notification_id": notification_id,
                        "selected_mode": selected_mode,
                        "quality": quality,
                    },
                    stamp,
                )
                self._conn.execute(
                    "INSERT INTO delivery_requests "
                    "(operation_key, sender, idempotency_key, request_digest, request_json, "
                    "target, target_incarnation, deadline, selected_mode, quality, stage, "
                    "cancel_requested, boundary_delivered, explicitly_acknowledged, "
                    "ordinal, latest_event_seq) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', 0, 0, 0, 1, ?)",
                    (
                        key,
                        intent.sender,
                        intent.idempotency_key,
                        digest,
                        _encode(request),
                        intent.target,
                        intent.target_incarnation,
                        intent.deadline,
                        selected_mode,
                        quality,
                        queued_seq,
                    ),
                )
                self._notification(notification_id, key, intent.target, offer)
                self._conn.commit()
                committed = True
                stored = self._fetch("key", (key,))
                if stored is None:
                    raise RuntimeError("committed delivery aggregate is missing")
                return DeliveryWrite("inserted", stored)
            except BaseException:
                if not committed:
                    self._conn.rollback()
                raise
            finally:
                self._finish(committed)

    def notification(self, notification_id: str) -> dict[str, Any] | None:
        """Return the committed frame for an at-least-once socket retry."""
        with self._lock:
            row = self._conn.execute(
                "SELECT frame_json FROM delivery_notifications WHERE notification_id = ?",
                (notification_id,),
            ).fetchone()
        if row is None:
            return None
        frame = json.loads(row[0])
        if not isinstance(frame, dict):
            raise DeliveryRefusal("replay_incompatible", "stored delivery frame is malformed")
        return frame

    def advance(
        self,
        operation_key: str,
        *,
        stage: DeliveryStage,
        mutation_id: str,
        mutation_digest: str,
        actor: str,
        source: Literal["recipient", "hub"],
        evidence: Mapping[str, Any],
    ) -> DeliveryWrite:
        """Atomically advance one stage with recipient- or hub-bound evidence."""
        return self._mutate(
            operation_key,
            stage=stage,
            mutation_id=mutation_id,
            mutation_digest=mutation_digest,
            actor=actor,
            source=source,
            evidence=evidence,
        )

    def request_cancel(
        self,
        operation_key: str,
        *,
        mutation_id: str,
        mutation_digest: str,
        actor: str,
    ) -> DeliveryWrite:
        """Record sender cancellation intent without claiming executor confirmation."""
        return self._mutate(
            operation_key,
            stage=None,
            mutation_id=mutation_id,
            mutation_digest=mutation_digest,
            actor=actor,
            source="recipient",
            evidence={},
        )

    def _mutate(
        self,
        operation_key: str,
        *,
        stage: DeliveryStage | None,
        mutation_id: str,
        mutation_digest: str,
        actor: str,
        source: Literal["recipient", "hub"],
        evidence: Mapping[str, Any],
    ) -> DeliveryWrite:
        """Commit a checked transition, mutation identity, event, and outbox frame."""
        if (
            not mutation_id
            or len(mutation_id.encode()) > 128
            or any(ord(char) < 0x20 or ord(char) == 0x7F for char in mutation_id)
            or len(mutation_digest) != 64
            or any(char not in "0123456789abcdef" for char in mutation_digest)
        ):
            raise DeliveryRefusal("invalid_shape", "delivery mutation identity is malformed")
        if len(_encode(evidence).encode()) > 4096:
            raise DeliveryRefusal("invalid_shape", "delivery evidence exceeds its limit")
        with self._lock:
            committed = False
            try:
                self._begin()
                current = self._fetch("key", (operation_key,))
                if current is None:
                    raise DeliveryRefusal("unknown_request", "delivery request does not exist")
                prior = self._conn.execute(
                    "SELECT mutation_digest FROM delivery_mutations "
                    "WHERE operation_key = ? AND mutation_id = ?",
                    (operation_key, mutation_id),
                ).fetchone()
                if prior is not None:
                    self._conn.rollback()
                    disposition: Literal["replayed", "conflict"] = (
                        "replayed" if prior[0] == mutation_digest else "conflict"
                    )
                    return DeliveryWrite(disposition, current)
                target = current.request.get("target")
                origin_hub = current.request.get("origin_hub")
                if stage is None:
                    if actor != current.sender:
                        raise DeliveryRefusal(
                            "unauthorised_requester", "only the sender may request cancellation"
                        )
                    lifecycle = DeliveryLifecycle(
                        current.stage, current.cancel_requested
                    ).request_cancel()
                    if current.cancel_requested:
                        self._conn.rollback()
                        return DeliveryWrite("replayed", current)
                    kind = DELIVERY_CANCEL_REQUESTED
                    audience = target
                else:
                    if source == "recipient" and actor != target:
                        raise DeliveryRefusal(
                            "unauthorised_requester", "stage evidence is not from recipient"
                        )
                    if source == "hub" and actor != origin_hub:
                        raise DeliveryRefusal(
                            "unauthorised_requester", "stage evidence is not from owning hub"
                        )
                    if stage in (
                        "boundary_delivered",
                        "acknowledged",
                        "completed",
                        "failed",
                        "cancelled",
                    ):
                        if source != "recipient":
                            raise DeliveryRefusal(
                                "unauthorised_requester", "stage requires recipient evidence"
                            )
                    if stage == "expired" and source != "hub":
                        raise DeliveryRefusal("unauthorised_requester", "expiry is a hub decision")
                    if stage in ("completed", "failed") and (
                        evidence.get("request_id") != current.request.get("request_id")
                        or evidence.get("task_id") != current.request.get("task_id")
                    ):
                        raise DeliveryRefusal(
                            "invalid_shape", "outcome evidence lacks matching correlation"
                        )
                    lifecycle = DeliveryLifecycle(current.stage, current.cancel_requested).advance(
                        stage
                    )
                    kind = DELIVERY_TRANSITION
                    audience = current.sender
                ordinal = current.ordinal + 1
                boundary_delivered = current.boundary_delivered or stage == "boundary_delivered"
                explicitly_acknowledged = current.explicitly_acknowledged or stage == "acknowledged"
                notification_id = f"delivery:{operation_key}:{ordinal}"
                stamp = time.time()
                event_seq = self._event(
                    kind,
                    {
                        "profile": 3,
                        "operation_key": operation_key,
                        "ordinal": ordinal,
                        "prior_stage": current.stage,
                        "stage": lifecycle.stage,
                        "cancel_requested": lifecycle.cancel_requested,
                        "boundary_delivered": boundary_delivered,
                        "explicitly_acknowledged": explicitly_acknowledged,
                        "mutation_id": mutation_id,
                        "mutation_digest": mutation_digest,
                        "actor": actor,
                        "source": source if stage is not None else "sender",
                        "evidence": dict(evidence),
                        "notification_id": notification_id,
                    },
                    stamp,
                )
                updated = self._conn.execute(
                    "UPDATE delivery_requests SET stage = ?, cancel_requested = ?, "
                    "boundary_delivered = ?, explicitly_acknowledged = ?, "
                    "ordinal = ?, latest_event_seq = ? "
                    "WHERE operation_key = ? AND ordinal = ?",
                    (
                        lifecycle.stage,
                        int(lifecycle.cancel_requested),
                        int(boundary_delivered),
                        int(explicitly_acknowledged),
                        ordinal,
                        event_seq,
                        operation_key,
                        current.ordinal,
                    ),
                )
                if updated.rowcount != 1:
                    raise DeliveryRefusal("state_conflict", "delivery state changed concurrently")
                self._conn.execute(
                    "INSERT INTO delivery_mutations "
                    "(operation_key, mutation_id, mutation_digest, event_seq) "
                    "VALUES (?, ?, ?, ?)",
                    (operation_key, mutation_id, mutation_digest, event_seq),
                )
                frame = {
                    "type": "delivery_status",
                    "sender": "SynapseHub",
                    "audience": audience,
                    "operation_key": operation_key,
                    "request_id": current.request.get("request_id"),
                    "task_id": current.request.get("task_id"),
                    "target": target,
                    "target_incarnation": current.request.get("target_incarnation"),
                    "stage": lifecycle.stage,
                    "cancel_requested": lifecycle.cancel_requested,
                    "notification_id": notification_id,
                    "event_seq": event_seq,
                    "source": source if stage is not None else "sender",
                    "protocol_version": 3,
                }
                self._notification(notification_id, operation_key, str(audience), frame)
                if lifecycle.stage in TERMINAL_STAGES:
                    self._conn.execute(
                        "UPDATE delivery_notifications SET retired_at = COALESCE(retired_at, ?) "
                        "WHERE operation_key = ? AND audience = ? AND delivered_at IS NULL",
                        (stamp, operation_key, target),
                    )
                self._conn.commit()
                committed = True
                stored = self._fetch("key", (operation_key,))
                if stored is None:
                    raise RuntimeError("committed delivery aggregate is missing")
                return DeliveryWrite("inserted", stored)
            except BaseException:
                if not committed:
                    self._conn.rollback()
                raise
            finally:
                self._finish(committed)
