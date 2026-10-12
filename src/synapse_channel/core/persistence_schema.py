# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — existing event-store table schemas
"""Create existing table families without committing or backfilling their contents."""

from __future__ import annotations

from typing import Any


def create_row_mac_index(connection: Any) -> None:
    """Index authenticated rows after the owner has installed the MAC column.

    Parameters
    ----------
    connection : connection
        The owner's existing DB-API connection. This additive index makes
        read-only authentication-enrollment probes independent of journal size.
    """
    connection.execute(
        "CREATE INDEX IF NOT EXISTS event_row_mac_idx ON events(mac) WHERE mac IS NOT NULL"
    )


def create_event_schema(connection: Any) -> None:
    """Create the append-only event table without changing transaction ownership.

    Parameters
    ----------
    connection : connection
        The owner's existing SQLite or SQLCipher DB-API connection.
    """
    connection.execute(
        "CREATE TABLE IF NOT EXISTS events ("
        "seq INTEGER PRIMARY KEY AUTOINCREMENT, "
        "ts REAL NOT NULL, "
        "kind TEXT NOT NULL, "
        "payload TEXT NOT NULL)"
    )


def create_operation_schema(connection: Any) -> None:
    """Create the keyed operation and evidence-outbox tables in their original order.

    Parameters
    ----------
    connection : connection
        The owner's DB-API connection; no commit is performed here.
    """
    connection.execute(
        "CREATE TABLE IF NOT EXISTS operations ("
        "operation_key TEXT PRIMARY KEY, "
        "request_digest TEXT NOT NULL, "
        "response_json TEXT NOT NULL, "
        "response_sha256 TEXT NOT NULL, "
        "first_event_seq INTEGER NOT NULL, "
        "commit_seq INTEGER NOT NULL, "
        "committed_at REAL NOT NULL)"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS operation_outbox ("
        "operation_key TEXT PRIMARY KEY, "
        "intent_json TEXT NOT NULL, "
        "receipt_id TEXT, "
        "FOREIGN KEY(operation_key) REFERENCES operations(operation_key))"
    )


def create_receipt_schema(connection: Any) -> None:
    """Create receipt aggregates and their notification outbox before legacy backfill.

    Parameters
    ----------
    connection : connection
        The owner's DB-API connection; backfill and commit remain with the owner.
    """
    connection.execute(
        "CREATE TABLE IF NOT EXISTS delivery_receipts ("
        "message_seq INTEGER PRIMARY KEY, "
        "sender TEXT NOT NULL, "
        "target TEXT NOT NULL, "
        "message_id INTEGER NOT NULL, "
        "client_msg_id TEXT NOT NULL, "
        "state TEXT NOT NULL, "
        "delivered INTEGER, "
        "deferred INTEGER NOT NULL, "
        "acked_by TEXT NOT NULL, "
        "updated_event_seq INTEGER NOT NULL, "
        "FOREIGN KEY(message_seq) REFERENCES events(seq), "
        "FOREIGN KEY(updated_event_seq) REFERENCES events(seq))"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS delivery_receipt_outbox ("
        "notification_id TEXT PRIMARY KEY, "
        "message_seq INTEGER NOT NULL, "
        "phase TEXT NOT NULL, "
        "sender TEXT NOT NULL, "
        "frame_json TEXT NOT NULL, "
        "attempts INTEGER NOT NULL DEFAULT 0, "
        "delivered_at REAL, "
        "FOREIGN KEY(message_seq) REFERENCES delivery_receipts(message_seq))"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS delivery_receipt_outbox_pending_idx "
        "ON delivery_receipt_outbox(sender, delivered_at)"
    )


def create_aef_schema(connection: Any) -> None:
    """Create the existing AEF outbox only after its kinds have been enrolled.

    Parameters
    ----------
    connection : connection
        The owner's DB-API connection; enrollment and commit remain with the owner.
    """
    connection.execute(
        "CREATE TABLE IF NOT EXISTS aef_outbox ("
        "legacy_seq INTEGER PRIMARY KEY, "
        "receipt_id TEXT UNIQUE, "
        "FOREIGN KEY(legacy_seq) REFERENCES events(seq))"
    )
