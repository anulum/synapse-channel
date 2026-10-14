# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — existing-journal snapshot for record-keeping exports
"""Read complete selected rows without provisioning, recovery or schema changes.

SQLite may create WAL coordination sidecars. Database content and file modes
are never changed. All rows in a result share one read transaction.
"""

from __future__ import annotations

import contextlib
import json
import math
import sqlite3
from collections.abc import Generator, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

from synapse_channel.core.at_rest import load_key_file
from synapse_channel.core.event_row_mac import RowMacKey, row_mac_key_path
from synapse_channel.core.persistence import StoredEvent
from synapse_channel.core.persistence_sqlcipher import (
    apply_sqlcipher_key,
    import_sqlcipher_module,
)

DEFAULT_MAX_EVENTS = 100_000
DEFAULT_MAX_PAYLOAD_BYTES = 64 * 1024 * 1024
SQLITE_MAX_SEQUENCE = 2**63 - 1
_SELECT_AUTHENTICATED = (
    "SELECT seq, ts, kind, CASE WHEN length(CAST(payload AS BLOB)) <= ? "
    "THEN payload ELSE NULL END, mac, length(CAST(payload AS BLOB)) "
    "FROM events WHERE (? IS NULL OR seq <= ?) ORDER BY seq"
)
_SELECT_LEGACY = (
    "SELECT seq, ts, kind, CASE WHEN length(CAST(payload AS BLOB)) <= ? "
    "THEN payload ELSE NULL END, NULL, length(CAST(payload AS BLOB)) "
    "FROM events WHERE (? IS NULL OR seq <= ?) ORDER BY seq"
)


if TYPE_CHECKING:

    class _Cursor(Protocol):
        def __iter__(self) -> Iterator[tuple[object, ...]]: ...

        def fetchone(self) -> tuple[object, ...] | None: ...

    class _Connection(Protocol):
        def execute(self, sql: str, parameters: tuple[object, ...] = ()) -> _Cursor: ...

        def close(self) -> None: ...

    class _Driver(Protocol):
        DatabaseError: type[Exception]

        def connect(self, database: str, *, uri: bool, timeout: float) -> _Connection: ...


@dataclass(frozen=True, slots=True)
class RecordSourceEvent:
    """A decoded retained event and the observed authentication of its row.

    Attributes
    ----------
    event : StoredEvent
        Exact sequence, timestamp, kind and decoded payload from the snapshot.
    authenticated : bool
        The supplied trusted row key verified this row. False includes an
        explicitly identified legacy prefix and a journal without row MACs.
    """

    event: StoredEvent
    authenticated: bool


def _bounded_integer(value: int, label: str, *, minimum: int) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= SQLITE_MAX_SEQUENCE
    ):
        raise ValueError(f"{label} must be an integer within the supported range")


def _finite_float(value: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("event payload contains a non-finite number")
    return result


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("event payload contains duplicate fields")
        result[name] = value
    return result


def _constant(value: str) -> None:
    del value
    raise ValueError("event payload contains a non-finite number")


def _schema(connection: _Connection) -> bool:
    columns = {
        str(row[1]): (str(row[2]).upper(), row[5])
        for row in connection.execute("PRAGMA table_info(events)")
    }
    primary_keys = {name for name, column in columns.items() if column[1]}
    if (
        not {"seq", "ts", "kind", "payload"} <= columns.keys()
        or columns["seq"] != ("INTEGER", 1)
        or primary_keys != {"seq"}
    ):
        raise ValueError("unsupported event journal schema")
    return "mac" in columns


def _event_metadata(row: tuple[object, ...]) -> tuple[int, float, str, str]:
    seq, timestamp, kind, encoded, _, _ = row
    if isinstance(seq, bool) or not isinstance(seq, int) or not 1 <= seq <= SQLITE_MAX_SEQUENCE:
        raise ValueError("invalid event sequence")
    if (
        isinstance(timestamp, bool)
        or not isinstance(timestamp, int | float)
        or not math.isfinite(timestamp)
    ):
        raise ValueError("invalid event timestamp")
    if not isinstance(kind, str) or not kind or not kind.isprintable() or kind == "corrupt_event":
        raise ValueError("invalid event kind")
    if not isinstance(encoded, str):
        raise ValueError("event payload is unavailable or exceeds the byte budget")
    return seq, float(timestamp), kind, encoded


def _decode(row: tuple[object, ...], key: RowMacKey | None) -> RecordSourceEvent:
    seq, timestamp, kind, encoded = _event_metadata(row)
    mac = row[4]
    payload = json.loads(
        encoded,
        object_pairs_hook=_unique_object,
        parse_float=_finite_float,
        parse_constant=_constant,
    )
    if not isinstance(payload, dict):
        raise ValueError("event payload must be an object")
    authenticated = False
    if key is not None:
        if mac is not None or seq > key.since_seq:
            if not key.verify(seq, timestamp, kind, encoded, mac):
                raise ValueError("event row authentication failed")
            authenticated = True
    return RecordSourceEvent(
        StoredEvent(seq, float(timestamp), kind, cast(dict[str, object], payload)),
        authenticated,
    )


def _selected_rows(
    connection: _Connection,
    path: Path,
    through_seq: int | None,
    row_mac: RowMacKey | None,
    max_payload_bytes: int,
) -> _Cursor:
    connection.execute("PRAGMA query_only=ON")
    connection.execute("BEGIN")
    has_mac = _schema(connection)
    if row_mac is None and (
        row_mac_key_path(path).exists()
        or row_mac_key_path(path).is_symlink()
        or (
            has_mac
            and connection.execute("SELECT 1 FROM events WHERE mac IS NOT NULL LIMIT 1").fetchone()
        )
    ):
        raise ValueError("authenticated event journal requires its existing row key")
    query = _SELECT_AUTHENTICATED if has_mac else _SELECT_LEGACY
    return connection.execute(
        query,
        (max_payload_bytes, through_seq, through_seq),
    )


def iter_record_source(
    path: str | Path,
    *,
    through_seq: int | None = None,
    key_file: str | Path | None = None,
    row_mac: RowMacKey | None = None,
    max_events: int = DEFAULT_MAX_EVENTS,
    max_payload_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES,
) -> Generator[RecordSourceEvent, None, None]:
    """Yield every selected row of one existing journal snapshot.

    Parameters
    ----------
    path : str or pathlib.Path
        Existing SQLite or SQLCipher database. Missing inputs stay absent.
    through_seq : int or None, optional
        Inclusive nonnegative sequence ceiling; zero selects an empty prefix.
    key_file : str or pathlib.Path or None, optional
        Existing owner-only SQLCipher key. No key is provisioned.
    row_mac : RowMacKey or None, optional
        Trusted enrolled key required for authenticated stores. A prefix at or
        below since_seq without a MAC is returned as explicitly unverified.
    max_events, max_payload_bytes : int, optional
        Positive event and encoded-payload budgets. Exceeding either refuses
        the whole export; rows are never silently dropped. Larger complete
        exports require explicit caller-selected budgets.

    Yields
    ------
    RecordSourceEvent
        A validated event and its row-authentication observation.

    Raises
    ------
    ValueError, OSError, RuntimeError
        Invalid inputs, unsupported storage, unavailable keys, invalid rows
        or exhausted budgets. The caller must close the iterator on early exit.
    """
    _bounded_integer(max_events, "max_events", minimum=1)
    _bounded_integer(max_payload_bytes, "max_payload_bytes", minimum=1)
    if through_seq is not None:
        _bounded_integer(through_seq, "through_seq", minimum=0)
    target = Path(path)
    if not target.is_file():
        raise ValueError("existing event journal is required")
    material = None if key_file is None else load_key_file(Path(key_file))
    driver = cast("_Driver", sqlite3 if material is None else import_sqlcipher_module())
    try:
        with contextlib.closing(
            driver.connect(target.absolute().as_uri() + "?mode=ro", uri=True, timeout=0)
        ) as connection:
            if material is not None:
                apply_sqlcipher_key(connection, material)
            rows = _selected_rows(connection, target, through_seq, row_mac, max_payload_bytes)
            size = 0
            for count, row in enumerate(rows, start=1):
                if count > max_events:
                    raise ValueError("record-keeping event budget exceeded")
                length = row[5]
                if not isinstance(length, int):
                    raise ValueError("invalid event payload size")
                size += length
                if size > max_payload_bytes:
                    raise ValueError("record-keeping payload byte budget exceeded")
                yield _decode(row, row_mac)
    except driver.DatabaseError as exc:
        raise ValueError("event journal cannot be read") from exc
