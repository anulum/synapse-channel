# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — bounded adapter ownership and pending transaction journal
"""Retain one adapter pre-image and one prepared update in a private SQLite file."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path

from synapse_channel.adapter_custody_files import AdapterCustodyError, validate_auxiliary
from synapse_channel.core.secure_path import apply_owner_only_file
from synapse_channel.durable_dir import sync_directory

MAX_TEXT_BYTES = 1_048_576
MAX_DOCUMENT_BYTES = 32 * MAX_TEXT_BYTES


@dataclass(frozen=True)
class Ownership:
    """Exact owned bytes and retained original content.

    Attributes
    ----------
    tool, mode : str
        Catalogue identity and dedicated or shared ownership shape.
    original : str or None
        Original UTF-8 content, or None for an originally absent target.
    owned : str
        Full installed dedicated file or exact inserted shared-file segment.
    """

    tool: str
    mode: str
    original: str | None
    owned: str


@dataclass(frozen=True)
class PendingUpdate:
    """Before/after intent committed before touching the target.

    Attributes
    ----------
    before, after : str or None
        Exact target content before and after the intended effect; None means absent.
    ownership : Ownership or None
        Ownership to settle only when the target matches the observed after state.
    """

    before: str | None
    after: str | None
    ownership: Ownership | None


def _text(value: object, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or len(value.encode("utf-8")) > MAX_TEXT_BYTES:
        raise AdapterCustodyError("Invalid adapter custody text.")
    return value


def _ownership(value: object) -> Ownership | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"tool", "mode", "original", "owned"}:
        raise AdapterCustodyError("Invalid adapter ownership record.")
    tool, mode, owned = value["tool"], value["mode"], value["owned"]
    if (
        not isinstance(tool, str)
        or not tool
        or len(tool) > 128
        or not isinstance(mode, str)
        or mode not in {"file", "append"}
    ):
        raise AdapterCustodyError("Invalid adapter ownership identity.")
    if not isinstance(owned, str) or not owned:
        raise AdapterCustodyError("Invalid adapter ownership bytes.")
    _text(owned)
    return Ownership(tool, mode, _text(value["original"], optional=True), owned)


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise AdapterCustodyError("Duplicate adapter custody field.")
        result[key] = value
    return result


def _decode(raw: str, target: Path) -> tuple[Ownership | None, PendingUpdate | None]:
    try:
        value: object = json.loads(raw, object_pairs_hook=_unique)
    except (ValueError, RecursionError) as exc:
        raise AdapterCustodyError("Invalid adapter custody JSON.") from exc
    if not isinstance(value, dict) or set(value) != {"schema", "target", "owned", "pending"}:
        raise AdapterCustodyError("Invalid adapter custody document.")
    if value["schema"] != "adapter-file-custody-v1" or value["target"] != str(target):
        raise AdapterCustodyError("Adapter custody belongs to another target or schema.")
    owned = _ownership(value["owned"])
    pending = value["pending"]
    if pending is None:
        return owned, None
    if not isinstance(pending, dict) or set(pending) != {"before", "after", "ownership"}:
        raise AdapterCustodyError("Invalid prepared adapter transaction.")
    return owned, PendingUpdate(
        _text(pending["before"], optional=True),
        _text(pending["after"], optional=True),
        _ownership(pending["ownership"]),
    )


class CustodyStore:
    """Read and atomically commit one bounded private ownership document.

    Parameters
    ----------
    path : pathlib.Path
        Journal in an already validated private directory, held under its lock.
    target : pathlib.Path
        Exact lexical target bound into every journal document.
    readonly : bool, optional
        Open an existing journal without creating or changing its state.

    Notes
    -----
    This journal coordinates cooperating installers, not uncooperative writers
    or distributed filesystem clients. No earlier original is silently replaced.
    """

    def __init__(self, path: Path, target: Path, *, readonly: bool = False) -> None:
        self.path = path
        self.target = target
        fresh = False
        if not readonly:
            try:
                descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                pass
            else:
                os.close(descriptor)
                apply_owner_only_file(path)
                fresh = True
        validate_auxiliary(path)
        mode = "ro" if readonly else "rw"
        self.connection = sqlite3.connect(path.as_uri() + f"?mode={mode}", uri=True, timeout=0.25)
        try:
            self.connection.execute("PRAGMA trusted_schema=OFF")
            self.connection.execute("PRAGMA synchronous=FULL")
            self.connection.execute("PRAGMA temp_store=MEMORY")
            if fresh:
                self.connection.execute(
                    "CREATE TABLE custody (id INTEGER PRIMARY KEY CHECK(id=1), "
                    "document TEXT NOT NULL, sha256 TEXT NOT NULL)"
                )
                self.connection.execute("PRAGMA user_version=1")
                self.connection.commit()
                self.save(None, None)
                sync_directory(path.parent)
            elif self.connection.execute("PRAGMA user_version").fetchone() != (1,):
                raise AdapterCustodyError("Unsupported adapter custody journal.")
        except BaseException:
            self.connection.close()
            raise

    def __enter__(self) -> CustodyStore:
        """Enter the journal context under the caller's existing lock.

        Returns
        -------
        CustodyStore
            This open journal.
        """
        return self

    def __exit__(self, *_args: object) -> None:
        """Close the journal without discarding any durably prepared intent."""
        self.connection.close()

    def load(self) -> tuple[Ownership | None, PendingUpdate | None]:
        """Validate and read the current ownership and pending transaction.

        Returns
        -------
        tuple
            Current exact-byte ownership and optional prepared update.

        Raises
        ------
        AdapterCustodyError, sqlite3.Error
            The existing journal is missing, oversized, changed or malformed.
        """
        row = self.connection.execute(
            "SELECT substr(document,1,?), sha256, length(cast(document AS blob)) "
            "FROM custody WHERE id=1",
            (MAX_DOCUMENT_BYTES + 1,),
        ).fetchone()
        if (
            row is None
            or not isinstance(row[0], str)
            or not isinstance(row[2], int)
            or row[2] > MAX_DOCUMENT_BYTES
        ):
            raise AdapterCustodyError("Adapter custody journal is incomplete or too large.")
        raw = row[0]
        if hashlib.sha256(raw.encode("utf-8")).hexdigest() != row[1]:
            raise AdapterCustodyError("Adapter custody journal digest changed.")
        return _decode(raw, self.target)

    def save(self, owned: Ownership | None, pending: PendingUpdate | None) -> None:
        """Commit exact ownership and prepared state before a filesystem effect.

        Parameters
        ----------
        owned : Ownership or None
            Previously settled ownership; retained while an update is pending.
        pending : PendingUpdate or None
            Exact before/after intent, or None after reconciliation.
        """
        document = {
            "schema": "adapter-file-custody-v1",
            "target": str(self.target),
            "owned": None if owned is None else asdict(owned),
            "pending": None if pending is None else asdict(pending),
        }
        raw = json.dumps(document, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        if len(raw.encode("utf-8")) > MAX_DOCUMENT_BYTES:
            raise AdapterCustodyError("Adapter custody document exceeds its bound.")
        _decode(raw, self.target)
        with self.connection:
            self.connection.execute(
                "INSERT INTO custody(id,document,sha256) VALUES(1,?,?) "
                "ON CONFLICT(id) DO UPDATE SET document=excluded.document,sha256=excluded.sha256",
                (raw, hashlib.sha256(raw.encode("utf-8")).hexdigest()),
            )
