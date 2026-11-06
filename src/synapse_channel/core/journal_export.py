# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — portable export of a hub store's journal and AEF receipts
"""Export a hub store to plain files, check the export, and copy it to plain SQLite.

An export is a new owner-only directory with three files:

* ``events.jsonl``: one stored journal row per line in ``seq`` order, with the
  stored text of ``kind`` and ``payload`` (or ``*_base64`` when SQLite holds bytes),
  ``payload_sha256``, the stored row ``mac``, ``row_mac`` and ``quarantined``;
* ``aef_receipts.jsonl``: the native AEF receipts as stored;
* ``MANIFEST.json``: counts, the digest of the exact ``seq`` set (rows deleted by
  event maintenance are legitimate gaps), the digest of every file, and what is
  not included.

Both tables are read in one read transaction on one connection, so the files are
one snapshot even while a hub writes. ``row_mac`` is ``verified`` or ``failed``
only when the exporter was given the row key, ``legacy`` for a row before the
key's first sequence, and ``unchecked`` otherwise: it is the exporter's statement,
not something the export proves. :func:`verify_export` checks consistency
between the files and the manifest, never authenticity. :func:`export_to_sqlite`
imports exactly the bytes it verified into a new plain SQLite file.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from synapse_channel import __version__
from synapse_channel.core.errors import SynapseError
from synapse_channel.core.event_row_mac import RowMacKey
from synapse_channel.core.event_row_recovery import decode_event_row
from synapse_channel.core.owner_only_output import write_new_owner_only_text
from synapse_channel.core.persistence_sqlcipher import connect_event_store

EXPORT_FORMAT = "synapse-export-v1"
"""Manifest format of this export layout."""

EVENTS_FILE = "events.jsonl"
AEF_FILE = "aef_receipts.jsonl"
MANIFEST_FILE = "MANIFEST.json"
_DATA_FILES = (EVENTS_FILE, AEF_FILE)

NOT_INCLUDED = (
    "row-authentication key and encryption keys",
    "external anti-rollback checkpoint store",
    "binary upload objects",
    "delivery, message-forward and operation state tables",
)
"""What an export deliberately leaves out; recorded in every manifest."""


class ExportError(SynapseError, ValueError):
    """An export cannot be written, or an export directory is not consistent."""

    code = "journal_export"


@dataclass(frozen=True)
class VerifiedExport:
    """The exact rows of an export that :func:`verify_export` found consistent."""

    manifest: dict[str, Any]
    events: list[dict[str, Any]]
    aef_lines: list[str]


def _text_field(name: str, value: object, record: dict[str, Any]) -> tuple[bytes, str | None]:
    """Record a stored field read as bytes; return its text when it is valid UTF-8.

    Valid UTF-8 is written as text under ``name``; other bytes under
    ``name_base64``, never with replacement characters. Returns the raw bytes
    and the text, or None when the bytes are not UTF-8.
    """
    # kind and payload are NOT NULL columns with TEXT affinity: SQLite stores a
    # number there as text, so with text_factory=bytes every value is bytes.
    assert isinstance(value, bytes)
    try:
        text = value.decode("utf-8")
    except UnicodeDecodeError:
        record[f"{name}_base64"] = base64.b64encode(value).decode("ascii")
        return value, None
    record[name] = text
    return value, text


def _event_record(row: tuple[object, ...], row_key: RowMacKey | None) -> dict[str, Any]:
    """Return one ``events.jsonl`` record for a stored row."""
    seq, ts, kind, payload, mac = row
    assert isinstance(seq, int)  # events.seq is the INTEGER PRIMARY KEY
    record: dict[str, Any] = {"seq": seq}
    if isinstance(ts, int | float) and not isinstance(ts, bool) and math.isfinite(ts):
        record["ts"] = ts
    else:
        record["ts"] = None
        record["ts_stored"] = repr(ts)
    _kind_raw, kind_text = _text_field("kind", kind, record)
    payload_raw, payload_text = _text_field("payload", payload, record)
    record["payload_sha256"] = hashlib.sha256(payload_raw).hexdigest()
    mac_text = mac.decode("ascii") if isinstance(mac, bytes) and mac.isascii() else None
    record["mac"] = mac_text
    if row_key is None:
        record["row_mac"] = "unchecked"
    elif seq <= row_key.since_seq:
        record["row_mac"] = "legacy"
    else:
        authentic = row_key.verify(seq, ts, kind_text, payload_text, mac_text)
        record["row_mac"] = "verified" if authentic else "failed"
    decoded = decode_event_row((seq, ts, kind_text, payload_text))
    record["quarantined"] = decoded.corruption is not None or record["row_mac"] == "failed"
    return record


def _read_snapshot(
    db: str | Path,
    key_file: str | Path | None,
    between_reads: Callable[[], None] | None,
) -> tuple[list[tuple[object, ...]], list[bytes]]:
    """Read events and AEF receipts in one read transaction on one connection."""
    connection, _encrypted = connect_event_store(db, key_file=key_file)
    # Text arrives as bytes, so invalid UTF-8 is kept and exported as base64
    # instead of failing the read or being replaced.
    connection.text_factory = bytes
    try:
        connection.execute("BEGIN")
        columns = {
            bytes(row[1]).decode() for row in connection.execute("PRAGMA table_info(events)")
        }
        mac_column = "mac" if "mac" in columns else "NULL"
        events = connection.execute(
            f"SELECT seq, ts, kind, payload, {mac_column} FROM events ORDER BY seq"  # nosec B608
        ).fetchall()
        if between_reads is not None:
            between_reads()
        tables = {
            bytes(row[0]).decode()
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        receipts: list[bytes] = []
        if "aef_receipts" in tables:
            receipts = [
                bytes(row[0])
                for row in connection.execute(
                    "SELECT canonical_receipt FROM aef_receipts ORDER BY seq"
                )
            ]
        connection.execute("COMMIT")
    finally:
        connection.close()
    return [tuple(row) for row in events], receipts


def _seq_set_digest(seqs: list[int]) -> str:
    return hashlib.sha256("\n".join(str(seq) for seq in sorted(seqs)).encode("ascii")).hexdigest()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def export_store(
    db: str | Path,
    out_dir: str | Path,
    *,
    key_file: str | Path | None = None,
    row_key: RowMacKey | None = None,
    between_reads: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Write a new export directory for ``db`` and return its manifest.

    ``between_reads`` runs between the two table reads, inside the read
    transaction; it exists so a test can write to the store at that moment.

    Raises
    ------
    ExportError
        When ``db`` is missing, ``out_dir`` exists, or a stored field cannot be
        represented faithfully.
    """
    source = Path(db)
    if not source.is_file():
        raise ExportError(f"no hub store at {source}")
    target = Path(out_dir)
    if target.exists() or target.is_symlink():
        raise ExportError(f"refusing to replace existing {target}")
    # Read first, so a store that cannot be opened leaves no directory behind.
    try:
        rows, receipts = _read_snapshot(source, key_file, between_reads)
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        raise ExportError(f"cannot read {source}: {exc}") from None
    # A directory created by someone else since the check above makes this
    # raise FileExistsError, which the CLI reports as a refusal.
    target.mkdir(mode=0o700)
    records = [_event_record(row, row_key) for row in rows]
    events_text = "".join(
        json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n" for record in records
    )
    aef_text = "".join(receipt.decode("utf-8") + "\n" for receipt in receipts)
    counts: dict[str, int] = {}
    for record in records:
        counts[record["row_mac"]] = counts.get(record["row_mac"], 0) + 1
    seqs = [record["seq"] for record in records]
    manifest: dict[str, Any] = {
        "format": EXPORT_FORMAT,
        "source": source.name,
        "tool_version": __version__,
        "event_count": len(records),
        "seq_set_sha256": _seq_set_digest(seqs),
        "first_seq": min(seqs) if seqs else None,
        "last_seq": max(seqs) if seqs else None,
        "row_mac_counts": dict(sorted(counts.items())),
        "quarantined_count": sum(1 for record in records if record["quarantined"]),
        "aef_receipt_count": len(receipts),
        "files": {
            EVENTS_FILE: _sha256(events_text.encode("utf-8")),
            AEF_FILE: _sha256(aef_text.encode("utf-8")),
        },
        "not_included": list(NOT_INCLUDED),
    }
    write_new_owner_only_text(target / EVENTS_FILE, events_text)
    write_new_owner_only_text(target / AEF_FILE, aef_text)
    write_new_owner_only_text(
        target / MANIFEST_FILE, json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    )
    return manifest


def _read_regular(directory: Path, name: str) -> bytes:
    """Read one listed file, refusing a symlink or a non-regular entry."""
    path = directory / name
    if path.is_symlink() or not path.is_file():
        raise ExportError(f"{name} is missing, a symlink, or not a regular file")
    return path.read_bytes()


def _check_entries(directory: Path) -> None:
    """Refuse a directory that holds anything besides the three export files."""
    if directory.is_symlink() or not directory.is_dir():
        raise ExportError(f"{directory} is not an export directory")
    extra = sorted({entry.name for entry in directory.iterdir()} - {MANIFEST_FILE, *_DATA_FILES})
    if extra:
        raise ExportError(f"unlisted entries in the export: {', '.join(extra)}")


def _parsed_events(raw: bytes) -> list[dict[str, Any]]:
    """Parse ``events.jsonl`` and check each record's payload digest."""
    events: list[dict[str, Any]] = []
    for number, line in enumerate(raw.decode("utf-8").splitlines(), start=1):
        record = json.loads(line)
        if not isinstance(record, dict):
            raise ExportError(f"{EVENTS_FILE} line {number} is not an object")
        if "payload_base64" in record:
            payload = base64.b64decode(record["payload_base64"], validate=True)
        else:
            payload = str(record["payload"]).encode("utf-8")
        if _sha256(payload) != record.get("payload_sha256"):
            raise ExportError(f"{EVENTS_FILE} line {number}: payload does not match its digest")
        events.append(record)
    return events


def _read_export(directory: Path) -> VerifiedExport:
    """Read the manifest and data files once, checking digests and shapes."""
    try:
        manifest = json.loads(_read_regular(directory, MANIFEST_FILE))
        if not isinstance(manifest, dict) or manifest.get("format") != EXPORT_FORMAT:
            raise ExportError(f"{MANIFEST_FILE} is not a {EXPORT_FORMAT} manifest")
        files = manifest.get("files")
        if not isinstance(files, dict) or set(files) != set(_DATA_FILES):
            raise ExportError(f"{MANIFEST_FILE} must list exactly {', '.join(_DATA_FILES)}")
        data = {name: _read_regular(directory, name) for name in _DATA_FILES}
        for name, raw in data.items():
            if _sha256(raw) != files[name]:
                raise ExportError(f"{name} does not match its manifest digest")
        events = _parsed_events(data[EVENTS_FILE])
        aef_lines = data[AEF_FILE].decode("utf-8").splitlines()
        for number, line in enumerate(aef_lines, start=1):
            if not isinstance(json.loads(line), dict):
                raise ExportError(f"{AEF_FILE} line {number} is not an object")
    except (ValueError, KeyError, TypeError, UnicodeDecodeError) as exc:
        if isinstance(exc, ExportError):
            raise
        raise ExportError(f"the export cannot be read: {exc}") from None
    return VerifiedExport(manifest=manifest, events=events, aef_lines=aef_lines)


def _unique_seqs(events: list[dict[str, Any]]) -> list[int]:
    """Return every event's integer seq, refusing a non-integer or a repeat."""
    seqs: list[int] = []
    for record in events:
        seq = record.get("seq")
        if isinstance(seq, bool) or not isinstance(seq, int):
            raise ExportError(f"{EVENTS_FILE} holds a non-integer seq")
        seqs.append(seq)
    if len(set(seqs)) != len(seqs):
        raise ExportError(f"{EVENTS_FILE} repeats a seq")
    return seqs


def _check_against_manifest(read: VerifiedExport) -> None:
    """Recompute every manifest figure from the rows and compare."""
    events, manifest = read.events, read.manifest
    seqs = _unique_seqs(events)
    row_mac_counts: dict[str, int] = {}
    for record in events:
        status = str(record.get("row_mac"))
        row_mac_counts[status] = row_mac_counts.get(status, 0) + 1
    checks = (
        (len(events), manifest.get("event_count"), "event count"),
        (dict(sorted(row_mac_counts.items())), manifest.get("row_mac_counts"), "row_mac counts"),
        (
            sum(1 for record in events if record.get("quarantined") is True),
            manifest.get("quarantined_count"),
            "quarantined count",
        ),
        (min(seqs) if seqs else None, manifest.get("first_seq"), "first seq"),
        (max(seqs) if seqs else None, manifest.get("last_seq"), "last seq"),
        (_seq_set_digest(seqs), manifest.get("seq_set_sha256"), "seq set"),
        (len(read.aef_lines), manifest.get("aef_receipt_count"), "AEF receipt count"),
    )
    for actual, recorded, label in checks:
        if actual != recorded:
            raise ExportError(f"the {label} does not match the manifest")


def verify_export(out_dir: str | Path) -> VerifiedExport:
    """Check an export's files against its manifest and return what was checked.

    The result is consistency between the files and the manifest; it says
    nothing about authenticity, which only the exporter's ``row_mac`` statement
    and the original row key can speak to.

    Raises
    ------
    ExportError
        On the first inconsistency, naming it.
    """
    directory = Path(out_dir)
    _check_entries(directory)
    read = _read_export(directory)
    _check_against_manifest(read)
    return read


_SQLITE_SCHEMA = (
    "CREATE TABLE events (seq INTEGER PRIMARY KEY, ts REAL, ts_stored TEXT, kind TEXT, "
    "kind_base64 TEXT, payload TEXT, payload_base64 TEXT, payload_sha256 TEXT NOT NULL, "
    "mac TEXT, row_mac TEXT NOT NULL, quarantined INTEGER NOT NULL)",
    "CREATE TABLE aef_receipts (position INTEGER PRIMARY KEY, log_id TEXT, seq INTEGER, "
    "receipt_id TEXT, receipt TEXT NOT NULL)",
)


def export_to_sqlite(out_dir: str | Path, sqlite_path: str | Path) -> VerifiedExport:
    """Verify an export, then copy exactly the verified rows into a new plain SQLite file.

    The file is not a hub store: no hub opens it. It exists so any SQLite tool
    can read the history without Synapse.

    Raises
    ------
    ExportError
        When the export is inconsistent or ``sqlite_path`` exists.
    """
    verified = verify_export(out_dir)
    target = Path(sqlite_path)
    try:
        os.close(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
    except FileExistsError:
        raise ExportError(f"refusing to replace existing {target}") from None
    try:
        _write_sqlite(target, verified)
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return verified


def _write_sqlite(target: Path, verified: VerifiedExport) -> None:
    """Write the verified rows into the new plain SQLite file at ``target``."""
    connection = sqlite3.connect(target)
    try:
        for statement in _SQLITE_SCHEMA:
            connection.execute(statement)
        connection.executemany(
            "INSERT INTO events VALUES (:seq, :ts, :ts_stored, :kind, :kind_base64, :payload, "
            ":payload_base64, :payload_sha256, :mac, :row_mac, :quarantined)",
            [
                {
                    "seq": record["seq"],
                    "ts": record.get("ts"),
                    "ts_stored": record.get("ts_stored"),
                    "kind": record.get("kind"),
                    "kind_base64": record.get("kind_base64"),
                    "payload": record.get("payload"),
                    "payload_base64": record.get("payload_base64"),
                    "payload_sha256": record["payload_sha256"],
                    "mac": record.get("mac"),
                    "row_mac": record["row_mac"],
                    "quarantined": int(bool(record["quarantined"])),
                }
                for record in verified.events
            ],
        )
        rows = []
        for position, line in enumerate(verified.aef_lines, start=1):
            receipt = json.loads(line)
            rows.append(
                (
                    position,
                    receipt.get("log_id"),
                    receipt.get("seq"),
                    receipt.get("receipt_id"),
                    line,
                )
            )
        connection.executemany("INSERT INTO aef_receipts VALUES (?, ?, ?, ?, ?)", rows)
        connection.commit()
    finally:
        connection.close()
