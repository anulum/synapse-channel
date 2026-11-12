# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — bounded wake generation catalogue
"""Reserve finite wake generations and retain compact retirement fences."""

from __future__ import annotations

import os
import re
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

from synapse_channel.core.secure_path import apply_owner_only_file, assert_owner_only_file_path
from synapse_channel.durable_dir import sync_directory
from synapse_channel.mcp.wake_limits import (
    MAX_RETIRED_GENERATIONS,
    MAX_ROOT_LEDGER_SLOTS,
    MAX_WAKE_DATABASE_BYTES,
    ROOT_CATALOGUE,
    ROOT_TRANSACTION_HEADROOM,
    RootLimits,
)
from synapse_channel.mcp.wake_pointer import (
    MAX_WAKE_INTEGER,
    WakeLedgerError,
    WakePolicyConflict,
    validate_policy_name,
)
from synapse_channel.mcp.wake_sqlite import configure_wake_sqlite

if TYPE_CHECKING:
    from synapse_channel.mcp.wake_root import RootInventory

RootPhase = Literal["creating", "active", "archiving", "retired"]
_APPLICATION_ID = 0x53575231
_PHASES = ("creating", "active", "archiving", "retired")
_SCHEMA = (
    "CREATE TABLE meta(id INTEGER PRIMARY KEY CHECK(id=1),revision INTEGER NOT NULL,"
    "max_ledgers INTEGER NOT NULL,max_bytes INTEGER NOT NULL,"
    "policy_name TEXT NOT NULL DEFAULT '')",
    "CREATE TABLE entries(scope_key TEXT PRIMARY KEY,phase TEXT NOT NULL,"
    "fingerprint TEXT NOT NULL DEFAULT '',archive_path TEXT NOT NULL DEFAULT '',"
    "manifest TEXT NOT NULL DEFAULT '',location_digest TEXT NOT NULL DEFAULT '',"
    "retired_at INTEGER NOT NULL DEFAULT 0)",
)


@dataclass(frozen=True)
class RootEntry:
    """Describe one reservation or compact archive fence without notification bodies."""

    key: str
    phase: RootPhase
    fingerprint: str = ""
    archive_path: str = ""
    manifest: str = ""
    location_digest: str = ""
    retired_at: int = 0

    def __post_init__(self) -> None:
        """Reject corrupted catalogue identities, phases and oversized archive intents."""
        if (
            not isinstance(self.key, str)
            or re.fullmatch(r"[0-9a-f]{64}", self.key) is None
            or self.phase not in _PHASES
            or not all(
                isinstance(x, str)
                for x in (self.fingerprint, self.archive_path, self.manifest, self.location_digest)
            )
            or len(self.archive_path) > 4096
            or len(self.manifest) > 8192
            or type(self.retired_at) is not int
            or not 0 <= self.retired_at <= MAX_WAKE_INTEGER
        ):
            raise WakeLedgerError("invalid wake root entry")
        for digest in (self.fingerprint, self.location_digest):
            if digest and re.fullmatch(r"[0-9a-f]{64}", digest) is None:
                raise WakeLedgerError("invalid wake archive fence")


def _verify(connection: sqlite3.Connection) -> None:
    """Refuse missing metadata and populations beyond the finite catalogue budget."""
    rows = connection.execute("SELECT * FROM meta").fetchall()
    if (
        len(rows) != 1
        or rows[0]["id"] != 1
        or type(rows[0]["revision"]) is not int
        or not 0 <= rows[0]["revision"] <= MAX_WAKE_INTEGER
    ):
        raise WakeLedgerError("invalid wake root metadata")
    RootLimits(rows[0]["max_ledgers"], rows[0]["max_bytes"])
    validate_policy_name(rows[0]["policy_name"])
    total = connection.execute("SELECT count(*) FROM entries").fetchone()[0]
    retired = connection.execute("SELECT count(*) FROM entries WHERE phase='retired'").fetchone()[0]
    retiring = connection.execute(
        "SELECT count(*) FROM entries WHERE phase='archiving'"
    ).fetchone()[0]
    bad = connection.execute(
        "SELECT 1 FROM entries WHERE phase NOT IN "
        "('creating','active','archiving','retired') OR length(scope_key)!=64 "
        "OR scope_key GLOB '*[^0-9a-f]*' LIMIT 1"
    ).fetchone()
    if (
        total > MAX_RETIRED_GENERATIONS + MAX_ROOT_LEDGER_SLOTS
        or total - retired > MAX_ROOT_LEDGER_SLOTS
        or retired + retiring > MAX_RETIRED_GENERATIONS
        or bad is not None
    ):
        raise WakeLedgerError("wake root catalogue capacity exceeded")


def _prepare(path: Path, *, allow_create: bool) -> bool:
    """Create a first catalogue only while the caller owns unfinished initialization."""
    fresh = False
    if allow_create:
        try:
            fd = os.open(
                path,
                os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
        except FileExistsError:
            pass
        else:
            os.close(fd)
            fresh = True
            apply_owner_only_file(path)
            sync_directory(path.parent)
    assert_owner_only_file_path(path, purpose="MCP wake root catalogue", require_single_link=True)
    size = path.stat().st_size
    if size > MAX_WAKE_DATABASE_BYTES:
        raise WakeLedgerError("wake root catalogue exceeds its byte budget")
    if size == 0 and not fresh:
        raise WakeLedgerError("wake root catalogue is incomplete")
    return fresh


@contextmanager
def root_catalogue(root: Path, *, allow_create: bool = False) -> Iterator[RootCatalogue]:
    """Use a bounded FULL/DELETE transaction while the caller holds the root OS lock."""
    path = root / ROOT_CATALOGUE
    fresh = _prepare(path, allow_create=allow_create)
    with closing(sqlite3.connect(path, timeout=0.1)) as db:
        db.row_factory = sqlite3.Row
        if not fresh and (
            db.execute("PRAGMA application_id").fetchone()[0] != _APPLICATION_ID
            or db.execute("PRAGMA user_version").fetchone()[0] not in (1, 2)
        ):
            raise WakeLedgerError("unsupported wake root catalogue")
        configure_wake_sqlite(db)
        with db:
            db.execute("BEGIN IMMEDIATE")
            if fresh:
                for statement in _SCHEMA:
                    db.execute(statement)
                defaults = RootLimits()
                db.execute(
                    "INSERT INTO meta(id,revision,max_ledgers,max_bytes) VALUES(1,0,?,?)",
                    (defaults.max_ledgers, defaults.max_bytes),
                )
                db.execute(f"PRAGMA application_id={_APPLICATION_ID}")
                db.execute("PRAGMA user_version=2")
            elif db.execute("PRAGMA user_version").fetchone()[0] == 1:
                db.execute("ALTER TABLE meta ADD COLUMN policy_name TEXT NOT NULL DEFAULT ''")
                db.execute("PRAGMA user_version=2")
            _verify(db)
            yield RootCatalogue(db, root)
            _verify(db)


class RootCatalogue:
    """Keep reservations and permanent finite retired-key fences under one root lock."""

    def __init__(self, connection: sqlite3.Connection, root: Path) -> None:
        """Use the caller-owned transaction without opening another connection."""
        self.connection = connection
        self.root = root

    def bind_policy(self, name: str) -> None:
        """Bind this root once; permit only the same physical policy thereafter."""
        validate_policy_name(name)
        if not name:
            raise WakeLedgerError("wake policy name is required")
        current: str = self.connection.execute("SELECT policy_name FROM meta").fetchone()[0]
        if not current:
            self.connection.execute("UPDATE meta SET policy_name=?", (name,))
        elif current != name:
            try:
                same = (self.root.parent / current).samefile(self.root.parent / name)
            except OSError:
                same = False
            if not same:
                raise WakePolicyConflict("wake root belongs to another policy")

    def limits(self) -> RootLimits:
        """Return the exact current validated operator limits."""
        row = self.connection.execute("SELECT max_ledgers,max_bytes FROM meta").fetchone()
        return RootLimits(row[0], row[1])

    def revision(self) -> int:
        """Read the bounded compare-and-set fence for operator configuration."""
        value: int = self.connection.execute("SELECT revision FROM meta").fetchone()[0]
        return value

    def _touch(self) -> None:
        """Increment the root fence without ever wrapping a durable counter."""
        if self.revision() == MAX_WAKE_INTEGER:
            raise WakeLedgerError("wake root revision exhausted")
        self.connection.execute("UPDATE meta SET revision=revision+1")

    def get(self, key: str) -> RootEntry | None:
        """Read one generation without projecting the rest of the registry."""
        row = self.connection.execute("SELECT * FROM entries WHERE scope_key=?", (key,)).fetchone()
        if row is None:
            return None
        return RootEntry(
            row["scope_key"],
            cast("RootPhase", row["phase"]),
            row["fingerprint"],
            row["archive_path"],
            row["manifest"],
            row["location_digest"],
            row["retired_at"],
        )

    def live_keys(self) -> frozenset[str]:
        """Return only the bounded reservations still occupying active-root capacity."""
        return frozenset(
            row[0]
            for row in self.connection.execute(
                "SELECT scope_key FROM entries WHERE phase!='retired'"
            )
        )

    def assert_capacity(
        self, inventory: RootInventory, key: str | None, limits: RootLimits
    ) -> None:
        """Reserve one main-file maximum and catalogue/journal maintenance headroom."""
        keys = inventory.keys | self.live_keys()
        if key is not None:
            keys |= {key}
        if len(keys) > limits.max_ledgers:
            raise WakeLedgerError("wake root generation capacity reached")
        other_bytes = inventory.logical_bytes - (
            inventory.sizes.get(key + ".sqlite3", 0) if key is not None else 0
        )
        other_bytes -= inventory.sizes.get(ROOT_CATALOGUE, 0)
        if other_bytes + MAX_WAKE_DATABASE_BYTES + ROOT_TRANSACTION_HEADROOM > limits.max_bytes:
            raise WakeLedgerError("wake root byte budget reached")

    def reserve(self, inventory: RootInventory, key: str) -> bool:
        """Fence admission before any new ledger bytes or callbacks can be published.

        Returns
        -------
        bool
            Whether a new or interrupted reservation still needs activation after
            its native commit. An active entry is unchanged under the root lock.
        """
        if re.fullmatch(r"[0-9a-f]{64}", key) is None:
            raise WakeLedgerError("invalid wake root generation")
        entry = self.get(key)
        if entry is not None and entry.phase in ("archiving", "retired"):
            raise WakeLedgerError("wake generation is archived or retiring")
        present = key + ".sqlite3" in inventory.sizes
        if (
            entry is not None
            and entry.phase == "active"
            and (not present or inventory.sizes[key + ".sqlite3"] == 0)
        ):
            raise WakeLedgerError("wake ledger disappeared")
        if key + ".sqlite3-journal" in inventory.sizes and not present:
            raise WakeLedgerError("wake ledger has an orphan journal")
        self.assert_capacity(inventory, key, self.limits())
        if entry is None:
            self.connection.execute(
                "INSERT INTO entries(scope_key,phase) VALUES(?,'creating')", (key,)
            )
            self._touch()
            return True
        return entry.phase == "creating"

    def activate(self, key: str) -> None:
        """Publish a first successful database commit without writing on ordinary reads."""
        entry = self.get(key)
        if entry is None or entry.phase not in ("creating", "active"):
            raise WakeLedgerError("wake reservation changed")
        if entry.phase == "creating":
            self.connection.execute("UPDATE entries SET phase='active' WHERE scope_key=?", (key,))
            self._touch()

    def configure(
        self, limits: RootLimits, expected_revision: int, inventory: RootInventory
    ) -> None:
        """Apply an exact operator CAS only when the entire existing root still fits."""
        if type(expected_revision) is not int or expected_revision != self.revision():
            raise WakeLedgerError("wake root configuration changed")
        self.assert_capacity(inventory, None, limits)
        self.connection.execute(
            "UPDATE meta SET max_ledgers=?,max_bytes=?", (limits.max_ledgers, limits.max_bytes)
        )
        self._touch()

    def phases(self) -> dict[str, int]:
        """Return bounded phase counts without loading archive intents or record bodies."""
        return dict(self.connection.execute("SELECT phase,count(*) FROM entries GROUP BY phase"))

    def begin_archive(self, intent: RootEntry) -> None:
        """Commit an exact resumable archive intent and reserve its permanent fence."""
        if (
            intent.phase != "archiving"
            or not intent.fingerprint
            or not intent.archive_path
            or not intent.manifest
            or not intent.location_digest
        ):
            raise WakeLedgerError("invalid wake archive phase")
        current = self.get(intent.key)
        if current is not None and current.phase in ("archiving", "retired"):
            if current == intent:
                return
            raise WakeLedgerError("wake archive intent changed")
        counts = self.phases()
        if counts.get("retired", 0) + counts.get("archiving", 0) == MAX_RETIRED_GENERATIONS:
            raise WakeLedgerError("wake retirement fence capacity reached")
        if current is None:
            self.connection.execute(
                "INSERT INTO entries(scope_key,phase) VALUES(?,'creating')", (intent.key,)
            )
        self.connection.execute(
            "UPDATE entries SET phase='archiving',fingerprint=?,archive_path=?,manifest=?,"
            "location_digest=? WHERE scope_key=?",
            (
                intent.fingerprint,
                intent.archive_path,
                intent.manifest,
                intent.location_digest,
                intent.key,
            ),
        )
        self._touch()

    def finish_archive(self, key: str, fingerprint: str, location_digest: str, now: int) -> None:
        """Retain a compact permanent fence only after verified archive publication."""
        if type(now) is not int or not 0 <= now <= MAX_WAKE_INTEGER:
            raise WakeLedgerError("invalid wake archive timestamp")
        current = self.get(key)
        if (
            current is None
            or current.phase != "archiving"
            or current.fingerprint != fingerprint
            or current.location_digest != location_digest
        ):
            raise WakeLedgerError("wake archive intent changed")
        self.connection.execute(
            "UPDATE entries SET phase='retired',manifest='',retired_at=? WHERE scope_key=?",
            (now, key),
        )
        self._touch()

    def cancel_archive(self, key: str, fingerprint: str) -> None:
        """Release an archive intent only after the caller verifies every original source."""
        current = self.get(key)
        if current is None or current.phase != "archiving" or current.fingerprint != fingerprint:
            raise WakeLedgerError("wake archive intent changed")
        self.connection.execute(
            "UPDATE entries SET phase='active',fingerprint='',archive_path='',manifest='',"
            "location_digest='' WHERE scope_key=?",
            (key,),
        )
        self._touch()
