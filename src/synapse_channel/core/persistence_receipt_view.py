# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — read-only delivery receipt projection
"""Read one receipt without bootstrap, backfill, permission changes or replay.

Each lookup opens the existing file with SQLite's ``mode=ro`` and performs the
aggregate/event join in one read transaction. Live WAL commits remain visible;
``immutable`` mode is deliberately absent. Missing, busy, unknown-schema and
unverifiable facts are unavailable, never evidence of transport acceptance.
SQLite may create or reuse WAL/SHM coordination sidecars beside an existing
WAL database in a writable directory. No database content or mode is changed.
"""

from __future__ import annotations

import contextlib
import json
import math
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from synapse_channel.core.at_rest import load_key_file
from synapse_channel.core.errors import SynapseError
from synapse_channel.core.event_row_mac import RowMacKey, row_mac_key_path
from synapse_channel.core.persistence_sqlcipher import (
    SqlCipherKeyError,
    SqlCipherUnavailableError,
    apply_sqlcipher_key,
    import_sqlcipher_module,
)


class ReceiptProjectionError(SynapseError, RuntimeError):
    """A requested encrypted lookup could not establish its reader."""

    code = "receipt_projection"


@dataclass(frozen=True, slots=True)
class DeliveryReceiptView:
    """A receipt's derived state and the exact event payload establishing it.

    Attributes
    ----------
    state : str
        Requested, immediate_delivered, pending, deferred or expired.
    receipt : Mapping[str, object]
        Independent decoded payload of the aggregate's exact referenced event.
    """

    state: str
    receipt: Mapping[str, object]


def _driver(key: bytes | None, key_file: str | Path | None) -> tuple[Any, bytes | None]:
    """Select a DB-API driver without opening or provisioning any database."""
    if key is None and key_file is None:
        return sqlite3, None
    try:
        material = key if key is not None else load_key_file(Path(str(key_file)))
        if len(material) != 32:
            raise ValueError("invalid encryption key length")
        return import_sqlcipher_module(), material
    except (ValueError, OSError, SqlCipherUnavailableError) as exc:
        raise ReceiptProjectionError("encrypted receipt reader is unavailable") from exc


def _receipt_state(kind: object, payload: Mapping[str, object]) -> str | None:
    """Derive the state from an event, never from the mutable aggregate alone."""
    if kind == "delivery_receipt_requested":
        return "requested"
    delivered = payload.get("delivered")
    if not isinstance(delivered, bool):
        return None
    if kind == "delivery_receipt_immediate":
        return "immediate_delivered" if delivered else "pending"
    if kind == "delivery_receipt_deferred" and delivered:
        return "deferred"
    if kind == "delivery_receipt_expired" and not delivered:
        return "expired"
    return None


def _reject_json_constant(value: str) -> None:
    """Reject non-finite JSON tokens the receipt writer cannot have emitted."""
    raise ValueError("non-finite receipt value")


def _valid_binding(
    payload: Mapping[str, object],
    message_seq: int,
    message_id: object,
    sender: object,
    target: object,
) -> bool:
    """Require exact integer cursors and the aggregate's nonempty participants."""
    recorded_seq, recorded_id = payload.get("message_seq"), payload.get("message_id")
    return not (
        isinstance(recorded_seq, bool)
        or not isinstance(recorded_seq, int)
        or recorded_seq != message_seq
        or isinstance(recorded_id, bool)
        or not isinstance(recorded_id, int)
        or recorded_id != message_id
        or payload.get("sender") != sender
        or payload.get("target") != target
        or not sender
        or not target
    )


def _decode_projection(
    row: tuple[Any, ...], message_seq: int, row_mac: RowMacKey | None
) -> DeliveryReceiptView | None:
    """Validate the exact joined event and its binding before returning a view."""
    state, sender, target, message_id, event_seq, ts, kind, encoded, mac, has_macs = row
    if not isinstance(encoded, str) or not isinstance(ts, int | float) or not math.isfinite(ts):
        return None
    if row_mac is None:
        if has_macs:
            return None
    elif not row_mac.verify(event_seq, ts, kind, encoded, mac):
        return None
    try:
        payload = json.loads(encoded, parse_constant=_reject_json_constant)
    except (ValueError, RecursionError):
        return None
    if not isinstance(payload, dict):
        return None
    if not _valid_binding(payload, message_seq, message_id, sender, target):
        return None
    derived = _receipt_state(kind, payload)
    if derived is None or derived != state:
        return None
    return DeliveryReceiptView(derived, payload)


def _known_schema(connection: Any) -> bool:
    """Require the writer's keyed tables, allowing additive future columns."""
    families = (
        ("events", {"seq", "ts", "kind", "payload"}, "seq"),
        (
            "delivery_receipts",
            {
                "message_seq",
                "sender",
                "target",
                "message_id",
                "client_msg_id",
                "state",
                "delivered",
                "deferred",
                "acked_by",
                "updated_event_seq",
            },
            "message_seq",
        ),
    )
    for table, columns, primary in families:
        schema = {
            row[1]: (str(row[2]).upper(), row[5])
            for row in connection.execute(f"PRAGMA table_info({table})")
        }
        if not columns <= schema.keys() or schema[primary] != ("INTEGER", 1):
            return False
    return True


def _project(
    connection: Any, path: Path, message_seq: int, row_mac: RowMacKey | None
) -> DeliveryReceiptView | None:
    """Read the supported additive schema inside one consistent read transaction."""
    connection.execute("PRAGMA query_only=ON")
    connection.execute("BEGIN")
    if not _known_schema(connection):
        return None
    columns = {row[1] for row in connection.execute("PRAGMA table_info(events)")}
    mac_column = "e.mac" if "mac" in columns else "NULL"
    mac_presence = (
        "EXISTS(SELECT 1 FROM events INDEXED BY event_row_mac_idx WHERE mac IS NOT NULL LIMIT 1)"
        if "mac" in columns
        else "0"
    )
    row = connection.execute(
        "SELECT r.state, r.sender, r.target, r.message_id, e.seq, e.ts, e.kind, "
        f"e.payload, {mac_column}, {mac_presence} "
        "FROM delivery_receipts AS r JOIN events AS e ON e.seq = r.updated_event_seq "
        "WHERE r.message_seq = ?",
        (message_seq,),
    ).fetchone()
    if row is None or (row_mac is None and row_mac_key_path(path).exists()):
        return None
    return _decode_projection(row, message_seq, row_mac)


def read_delivery_receipt(
    path: str | Path,
    message_seq: int,
    *,
    key_file: str | Path | None = None,
    key: bytes | None = None,
    row_mac: RowMacKey | None = None,
) -> DeliveryReceiptView | None:
    """Read the exact current receipt from an existing, read-only event store.

    Parameters
    ----------
    path : str or pathlib.Path
        Existing SQLite or SQLCipher file. A missing file and its parent remain
        absent; this function never provisions a database or a key.
    message_seq : int
        Positive durable chat sequence, not the hub's in-memory message id.
    key_file : str or pathlib.Path or None, optional
        Existing SQLCipher key file. Raw ``key`` takes precedence when supplied.
    key : bytes or None, optional
        Raw 32-byte SQLCipher key. Omit both encryption inputs for SQLite.
    row_mac : RowMacKey or None, optional
        Trusted enrolled row key. Authenticated stores without this key are
        unavailable. With it, the referenced event must verify, including a
        legacy-prefix event: unverified legacy data is not vouched for.

    Returns
    -------
    DeliveryReceiptView or None
        State and exact payload from one consistent snapshot. None means
        absent, busy, unknown schema, corrupt or unverified; it proves neither
        acceptance nor refusal. Each subsequent call sees new WAL commits.

    Raises
    ------
    ValueError
        If the message sequence is not a positive integer.
    ReceiptProjectionError
        If an explicitly requested encrypted reader cannot be established.

    Notes
    -----
    Authentication covers the referenced event, not a full-journal audit.
    The aggregate's state and message binding must agree with that event.
    MAC-column stores require the writer's additive ``event_row_mac_idx``
    index; older schemas remain unavailable until upgraded by their owner.
    Enrollment detection uses that index rather than scanning the journal.
    A receipt proves transport delivery only, never model consumption.
    """
    if (
        isinstance(message_seq, bool)
        or not isinstance(message_seq, int)
        or not 1 <= message_seq <= 2**63 - 1
    ):
        raise ValueError("receipt message sequence must be a positive integer in SQLite's range")
    target = Path(path)
    if not target.is_file():
        return None
    driver, material = _driver(key, key_file)
    try:
        with contextlib.closing(
            driver.connect(target.absolute().as_uri() + "?mode=ro", uri=True, timeout=0)
        ) as connection:
            if material is not None:
                apply_sqlcipher_key(connection, material)
            return _project(connection, target, message_seq, row_mac)
    except SqlCipherKeyError as exc:
        raise ReceiptProjectionError("encrypted receipt reader could not verify its key") from exc
    except driver.DatabaseError:
        return None
