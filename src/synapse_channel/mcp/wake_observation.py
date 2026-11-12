# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — atomic native activity observation and coalescing
"""Keep scanned source positions and their pending wake bits in one transaction."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from synapse_channel.mcp.wake_database import wake_database
from synapse_channel.mcp.wake_pending import WakePendingObservation
from synapse_channel.mcp.wake_pointer import (
    MAX_WAKE_INTEGER,
    WAKE_SOURCES,
    WakeLedgerError,
    WakePosition,
    WakeScope,
    WakeSource,
    validate_position,
)
from synapse_channel.mcp.wake_streams import WakeStreamObservation


@dataclass(frozen=True)
class _ActivityScan:
    """Bind one exact committed page to its scanned interval and source bits."""

    previous: int
    cursor: int
    bits: int

    def __post_init__(self) -> None:
        """Refuse coercion, regressions and changes without journal advancement."""
        if (
            any(
                type(value) is not int or not 0 <= value <= MAX_WAKE_INTEGER
                for value in (self.previous, self.cursor, self.bits)
            )
            or self.previous > self.cursor
            or self.bits > 7
            or (self.previous == self.cursor and self.bits != 0)
        ):
            raise WakeLedgerError("invalid wake activity scan")

    def encode(self) -> str:
        """Return the small canonical state, containing no source event bodies."""
        return json.dumps(
            {"previous": self.previous, "cursor": self.cursor, "bits": self.bits},
            sort_keys=True,
            separators=(",", ":"),
        )


def _activity_scan(connection: sqlite3.Connection) -> _ActivityScan | None:
    """Refuse corrupt stored progress instead of restarting it from zero."""
    row = connection.execute("SELECT position FROM sources WHERE source='activity'").fetchone()
    if row is None:
        return None
    try:
        raw = row[0]
        if type(raw) is not str or len(raw) > 256:
            raise ValueError("invalid stored activity scan")
        payload: object = json.loads(raw)
        if not isinstance(payload, dict) or set(payload) != {"previous", "cursor", "bits"}:
            raise ValueError("invalid stored activity scan")
        return _ActivityScan(payload["previous"], payload["cursor"], payload["bits"])
    except (TypeError, ValueError, WakeLedgerError):
        raise WakeLedgerError("invalid stored wake activity scan") from None


class WakeObservation(WakeStreamObservation, WakePendingObservation):
    """Persist observer progress without consuming native processing receipts.

    Parameters
    ----------
    path : Path
        Scope-bound private SQLite file shared with its outbox.
    scope : WakeScope
        Fixed operator subject, project, seat, configuration binding and hub.
    """

    def __init__(self, path: Path, scope: WakeScope) -> None:
        """Bind the observation state without opening any connection."""
        self.path = path
        self.scope = scope

    def position(self, source: WakeSource) -> WakePosition | None:
        """Read an observer's committed scan marker, independently of delivery."""
        if source not in WAKE_SOURCES:
            raise WakeLedgerError("invalid wake source")
        with wake_database(self.path, self.scope) as connection:
            row = connection.execute(
                "SELECT position FROM sources WHERE source=?", (source,)
            ).fetchone()
            if row is None:
                return None
            try:
                value: WakePosition = json.loads(row[0])
            except (TypeError, ValueError):
                raise WakeLedgerError("invalid stored wake source position") from None
            validate_position(source, value)
            return value

    def observe(
        self,
        source: WakeSource,
        previous: WakePosition | None,
        position: WakePosition,
        *,
        relevant: bool,
    ) -> bool:
        """Commit a scan marker and pending relevant change in one transaction.

        Empty inbox pages advance their scan marker with relevant=False.
        The original marker must still match; exact repeated commits are
        idempotent. Neither a scan nor a notification is a processing receipt.
        """
        validate_position(source, position)
        if previous is not None:
            validate_position(source, previous)
        if type(relevant) is not bool:
            raise WakeLedgerError("invalid wake relevance")
        encoded = json.dumps(position, separators=(",", ":"))
        with wake_database(self.path, self.scope) as connection:
            row = connection.execute(
                "SELECT position FROM sources WHERE source=?", (source,)
            ).fetchone()
            current = row[0] if row is not None else None
            if current == encoded:
                return False
            expected = json.dumps(previous, separators=(",", ":")) if previous is not None else None
            if current != expected:
                raise WakeLedgerError("wake scan position changed")
            if isinstance(position, int) and isinstance(previous, int) and position < previous:
                raise WakeLedgerError("wake journal position regressed")
            connection.execute(
                "INSERT INTO sources VALUES (?,?) ON CONFLICT(source) "
                "DO UPDATE SET position=excluded.position",
                (source, encoded),
            )
            if relevant:
                updated = connection.execute(
                    "UPDATE meta SET revision=revision+1,pending=pending|? WHERE revision<?",
                    (1 << WAKE_SOURCES.index(source), MAX_WAKE_INTEGER),
                )
                if updated.rowcount != 1:
                    raise WakeLedgerError("wake revision budget exhausted")
            return relevant

    def scan_cursor(self) -> int:
        """Return committed activity progress, or zero for a genuinely new scope."""
        with wake_database(self.path, self.scope) as connection:
            scan = _activity_scan(connection)
            return scan.cursor if scan is not None else 0

    def observe_page(self, previous: int, cursor: int, *, sources: tuple[WakeSource, ...]) -> bool:
        """Commit one page and all its source notifications atomically.

        Parameters
        ----------
        previous, cursor : int
            Original requested position and returned scanned position. Exact
            repeats are idempotent; stale, contradictory or regressing pages
            cannot consume a newer cursor.
        sources : tuple of {"messages", "tasks", "handoffs"}
            Unique kinds in the validated page. Empty scans still advance
            progress without producing a wake.

        Returns
        -------
        bool
            Whether this commit added relevant work. Notification acceptance
            remains independent from reading or processing the native events.
        """
        if (
            type(sources) is not tuple
            or len(sources) > len(WAKE_SOURCES)
            or any(type(source) is not str or source not in WAKE_SOURCES for source in sources)
            or len(set(sources)) != len(sources)
        ):
            raise WakeLedgerError("invalid wake activity sources")
        bits = sum(1 << WAKE_SOURCES.index(source) for source in sources)
        page = _ActivityScan(previous, cursor, bits)
        with wake_database(self.path, self.scope) as connection:
            current = _activity_scan(connection)
            committed = current.cursor if current is not None else 0
            if current == page or (page.previous == page.cursor == committed and not bits):
                return False
            if committed != previous:
                raise WakeLedgerError("wake activity scan position changed")
            connection.execute(
                "INSERT INTO sources VALUES ('activity',?) ON CONFLICT(source) "
                "DO UPDATE SET position=excluded.position",
                (page.encode(),),
            )
            if bits:
                updated = connection.execute(
                    "UPDATE meta SET revision=revision+1,pending=pending|? WHERE revision<?",
                    (bits, MAX_WAKE_INTEGER),
                )
                if updated.rowcount != 1:
                    raise WakeLedgerError("wake revision budget exhausted")
            return bool(bits)
