# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — portable journal export on real stores, files and plain SQLite
"""Export real hub stores, verify the files, and read them back with plain sqlite3."""

from __future__ import annotations

import base64
import collections
import hashlib
import json
import os
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from synapse_channel import cli
from synapse_channel.core.aef_emission import AefReceiptLog
from synapse_channel.core.event_row_mac import (
    load_or_create_row_mac_key,
    load_row_mac_key,
    row_mac_key_path,
)
from synapse_channel.core.journal_export import (
    ExportError,
    export_store,
    export_to_sqlite,
    verify_export,
)
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.receipt_signing import (
    generate_receipt_signing_key,
    load_receipt_signing_key,
)

_NOW_MS = 1_783_941_000_000


def _store(tmp_path: Path) -> tuple[Path, Path]:
    """Write a real store: two legacy rows, then row-MAC'd rows, AEF receipts, one deletion."""
    db = tmp_path / "hub.db"
    store = EventStore(db)
    store.append("claim", {"task_id": "legacy-1", "owner": "a"})
    store.append("message", {"text": "before the key"})
    key = load_or_create_row_mac_key(
        row_mac_key_path(db), current_max_seq=store.max_seq(), log_has_macs=store.has_row_macs()
    )
    store.enable_row_mac(key)
    store.append("claim", {"task_id": "T1", "owner": "a"})
    gone = store.append("message", {"text": "to be deleted"})
    store.append("release", {"task_id": "T1", "owner": "a"})
    store.append("claim", {"task_id": "T2", "owner": "b"})
    store.delete([gone])
    store.close()
    signing = tmp_path / "aef.key"
    generate_receipt_signing_key(signing)
    with AefReceiptLog(db, hub_id="hub-a", signing_key=load_receipt_signing_key(signing)) as log:
        for number in (1, 2):
            log.append(
                receipt_type="lease",
                action="grant",
                actor_id="agent-1",
                subject={"task_id": f"T{number}", "epoch": 1, "lease_expires_at": _NOW_MS + 9},
                issued_at=_NOW_MS - 10,
                decision="allow",
                legacy_seq=number,
            )
    return db, row_mac_key_path(db)


def _lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text("utf-8").splitlines()]


def test_round_trip_matches_the_source_with_plain_sqlite(tmp_path: Path) -> None:
    db, key_path = _store(tmp_path)
    out = tmp_path / "export"
    manifest = export_store(db, out, row_key=load_row_mac_key(key_path))

    assert out.stat().st_mode & 0o777 == 0o700
    assert all((out / name).stat().st_mode & 0o777 == 0o600 for name in os.listdir(out))
    source = EventStore(db)
    try:
        stored = list(source.iter_events())
    finally:
        source.close()
    assert manifest["event_count"] == len(stored) == 5
    assert manifest["row_mac_counts"] == {"legacy": 2, "verified": 3}
    assert manifest["quarantined_count"] == 0 and manifest["aef_receipt_count"] == 2
    assert manifest["source"] == "hub.db"
    assert verify_export(out).manifest == manifest

    plain = tmp_path / "history.sqlite"
    export_to_sqlite(out, plain)
    assert plain.stat().st_mode & 0o777 == 0o600
    connection = sqlite3.connect(plain)
    try:
        seqs = [row[0] for row in connection.execute("SELECT seq FROM events ORDER BY seq")]
        kinds = dict(connection.execute("SELECT kind, count(*) FROM events GROUP BY kind"))
        aef = connection.execute("SELECT count(*) FROM aef_receipts").fetchone()[0]
        timeline = [
            json.loads(row[0])["task_id"]
            for row in connection.execute(
                "SELECT payload FROM events WHERE kind = 'claim' ORDER BY seq"
            )
        ]
    finally:
        connection.close()
    assert seqs == [event.seq for event in stored] and 4 not in seqs
    assert kinds == dict(collections.Counter(event.kind for event in stored))
    assert aef == 2
    assert timeline == ["legacy-1", "T1", "T2"]


def test_a_tampered_row_is_exported_as_failed_and_quarantined(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db, key_path = _store(tmp_path)
    connection = sqlite3.connect(db)
    connection.execute("UPDATE events SET payload = ? WHERE seq = 3", ('{"task_id": "forged"}',))
    connection.commit()
    connection.close()
    out = tmp_path / "export"

    code = cli.main(
        ["export", "create", str(db), "--out", str(out), "--row-mac-key", str(key_path)]
    )

    assert code == 0
    assert "1 rows failed the row MAC and 1 are quarantined" in capsys.readouterr().err
    row = next(record for record in _lines(out / "events.jsonl") if record["seq"] == 3)
    assert (row["row_mac"], row["quarantined"], row["payload"]) == (
        "failed",
        True,
        '{"task_id": "forged"}',
    )
    unkeyed = tmp_path / "unkeyed"
    assert cli.main(["export", "create", str(db), "--out", str(unkeyed)]) == 0
    assert {record["row_mac"] for record in _lines(unkeyed / "events.jsonl")} == {"unchecked"}


def test_one_snapshot_even_when_a_writer_commits_between_the_reads(tmp_path: Path) -> None:
    db, _key = _store(tmp_path)

    def write_between() -> None:
        writer = sqlite3.connect(db)
        writer.execute(
            "INSERT INTO events (ts, kind, payload) VALUES (1.0, 'message', '{\"text\": \"late\"}')"
        )
        writer.execute("DELETE FROM aef_receipts WHERE seq = 2")
        writer.commit()
        writer.close()

    manifest = export_store(db, tmp_path / "export", between_reads=write_between)

    assert (manifest["event_count"], manifest["aef_receipt_count"]) == (5, 2)
    after = export_store(db, tmp_path / "after")
    assert (after["event_count"], after["aef_receipt_count"]) == (6, 1)


def test_bytes_and_odd_timestamps_are_kept_exactly(tmp_path: Path) -> None:
    db = tmp_path / "odd.db"
    EventStore(db).close()
    connection = sqlite3.connect(db)
    connection.execute(
        "INSERT INTO events (ts, kind, payload) VALUES (?, ?, ?)", (1.5, "message", b"\xff\xfe raw")
    )
    connection.execute(
        "INSERT INTO events (ts, kind, payload) VALUES (?, ?, ?)", ("soon", b"\x80", '{"a": 1}')
    )
    connection.execute("INSERT INTO events (ts, kind, payload) VALUES (?, ?, ?)", (2.0, 7, 42))
    connection.commit()
    connection.close()
    out = tmp_path / "export"

    export_store(db, out)

    first, second, third = _lines(out / "events.jsonl")
    assert base64.b64decode(first["payload_base64"]) == b"\xff\xfe raw" and "payload" not in first
    assert first["payload_sha256"] == hashlib.sha256(b"\xff\xfe raw").hexdigest()
    assert first["quarantined"] is True
    assert (second["ts"], second["ts_stored"]) == (None, "b'soon'")
    assert base64.b64decode(second["kind_base64"]) == b"\x80"
    # Text affinity: SQLite keeps the numbers 7 and 42 as the texts "7" and "42".
    assert (third["kind"], third["payload"], third["quarantined"]) == ("7", "42", True)
    assert third["payload_sha256"] == hashlib.sha256(b"42").hexdigest()
    assert len(verify_export(out).events) == 3
    export_to_sqlite(out, tmp_path / "odd.sqlite")
    copy = sqlite3.connect(tmp_path / "odd.sqlite")
    try:
        row = copy.execute("SELECT payload_base64 FROM events WHERE seq = 1").fetchone()
        assert base64.b64decode(row[0]) == b"\xff\xfe raw"
    finally:
        copy.close()


def _rewrite(out: Path, name: str, text: str, *, fix_digest: bool) -> None:
    (out / name).write_text(text, "utf-8")
    if fix_digest:
        manifest = json.loads((out / "MANIFEST.json").read_text("utf-8"))
        manifest["files"][name] = hashlib.sha256(text.encode("utf-8")).hexdigest()
        (out / "MANIFEST.json").write_text(json.dumps(manifest), "utf-8")


@pytest.mark.parametrize(
    ("damage", "message"),
    [
        ("changed-line", "does not match its manifest digest"),
        ("changed-payload", "payload does not match its digest"),
        ("removed-line", "event count does not match"),
        ("swapped-seq", "seq set does not match"),
        ("extra-entry", "unlisted entries in the export: notes.txt"),
        ("missing-file", "aef_receipts.jsonl is missing"),
        ("symlinked-file", "aef_receipts.jsonl is missing, a symlink"),
        ("wrong-format", "is not a synapse-export-v1 manifest"),
        ("wrong-files", "must list exactly"),
        ("not-json", "the export cannot be read"),
        ("non-object-line", "line 1 is not an object"),
        ("string-seq", "holds a non-integer seq"),
        ("aef-count", "AEF receipt count does not match"),
        ("aef-not-object", "aef_receipts.jsonl line 1 is not an object"),
        ("duplicate-seq", "repeats a seq"),
        ("manifest-row-mac", "row_mac counts does not match"),
        ("manifest-quarantined", "quarantined count does not match"),
        ("manifest-first-seq", "first seq does not match"),
        ("manifest-last-seq", "last seq does not match"),
    ],
)
def test_verify_refuses_every_inconsistency(tmp_path: Path, damage: str, message: str) -> None:
    db, _key = _store(tmp_path)
    out = tmp_path / "export"
    export_store(db, out)
    events = (out / "events.jsonl").read_text("utf-8")
    lines = events.splitlines(keepends=True)
    if damage == "changed-line":
        (out / "events.jsonl").write_text(events.replace("T1", "T9", 1), "utf-8")
    elif damage == "changed-payload":
        _rewrite(out, "events.jsonl", events.replace("T1", "T9", 1), fix_digest=True)
    elif damage == "removed-line":
        _rewrite(out, "events.jsonl", "".join(lines[1:]), fix_digest=True)
    elif damage == "swapped-seq":
        # seq 2 becomes the deleted seq 4: same count, first and last, other set.
        record = json.loads(lines[1])
        record["seq"] = 4
        swapped = [lines[0], json.dumps(record) + "\n", *lines[2:]]
        _rewrite(out, "events.jsonl", "".join(swapped), fix_digest=True)
    elif damage == "extra-entry":
        (out / "notes.txt").write_text("hi", "utf-8")
    elif damage == "missing-file":
        (out / "aef_receipts.jsonl").unlink()
    elif damage == "symlinked-file":
        target = tmp_path / "elsewhere.jsonl"
        (out / "aef_receipts.jsonl").rename(target)
        (out / "aef_receipts.jsonl").symlink_to(target)
    elif damage in ("wrong-format", "wrong-files"):
        manifest = json.loads((out / "MANIFEST.json").read_text("utf-8"))
        if damage == "wrong-format":
            manifest["format"] = "other"
        else:
            manifest["files"].pop("aef_receipts.jsonl")
        (out / "MANIFEST.json").write_text(json.dumps(manifest), "utf-8")
    elif damage == "not-json":
        _rewrite(out, "events.jsonl", "{not json\n", fix_digest=True)
    elif damage == "non-object-line":
        _rewrite(out, "events.jsonl", "[1]\n", fix_digest=True)
    elif damage == "string-seq":
        record = json.loads(lines[0])
        record["seq"] = "1"
        _rewrite(
            out, "events.jsonl", json.dumps(record) + "\n" + "".join(lines[1:]), fix_digest=True
        )
    elif damage == "aef-count":
        _rewrite(out, "aef_receipts.jsonl", "{}\n", fix_digest=True)
    elif damage == "aef-not-object":
        aef = (out / "aef_receipts.jsonl").read_text("utf-8").splitlines(keepends=True)
        _rewrite(out, "aef_receipts.jsonl", "[1, 2]\n" + "".join(aef[1:]), fix_digest=True)
    elif damage == "duplicate-seq":
        _rewrite(out, "events.jsonl", events + lines[0], fix_digest=True)
    elif damage.startswith("manifest-"):
        manifest = json.loads((out / "MANIFEST.json").read_text("utf-8"))
        field = {
            "manifest-row-mac": "row_mac_counts",
            "manifest-quarantined": "quarantined_count",
            "manifest-first-seq": "first_seq",
            "manifest-last-seq": "last_seq",
        }[damage]
        manifest[field] = {"verified": 99} if field == "row_mac_counts" else 99
        (out / "MANIFEST.json").write_text(json.dumps(manifest), "utf-8")

    with pytest.raises(ExportError, match=message):
        verify_export(out)


def test_refusals_for_inputs_and_existing_outputs(tmp_path: Path) -> None:
    db, _key = _store(tmp_path)
    out = tmp_path / "export"
    out.mkdir()
    with pytest.raises(ExportError, match="refusing to replace existing"):
        export_store(db, out)
    with pytest.raises(ExportError, match="no hub store"):
        export_store(tmp_path / "none.db", tmp_path / "x")
    with pytest.raises(ExportError, match="not an export directory"):
        verify_export(tmp_path / "missing-dir")
    good = tmp_path / "good"
    export_store(db, good)
    taken = tmp_path / "taken.sqlite"
    taken.write_text("", "utf-8")
    with pytest.raises(ExportError, match="refusing to replace existing"):
        export_to_sqlite(good, taken)
    legacy = tmp_path / "legacy.db"
    EventStore(legacy).close()
    connection = sqlite3.connect(legacy)
    connection.execute("INSERT INTO events (ts, kind, payload) VALUES (1.0, 'm', '{}')")
    connection.commit()
    connection.close()
    assert export_store(legacy, tmp_path / "legacy")["aef_receipt_count"] == 0


def test_cli_verify_and_to_sqlite_report_and_exit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db, _key = _store(tmp_path)
    out = tmp_path / "export"
    assert cli.main(["export", "create", str(db), "--out", str(out)]) == 0
    assert "exported 5 events and 2 AEF receipts" in capsys.readouterr().out
    assert cli.main(["export", "verify", str(out)]) == 0
    assert capsys.readouterr().out.startswith("EXPORT_CONSISTENT: 5 events, 2 AEF receipts")
    assert cli.main(["export", "verify", str(out), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["verdict"] == "EXPORT_CONSISTENT"
    assert cli.main(["export", "to-sqlite", str(out), str(tmp_path / "h.sqlite")]) == 0
    assert "plain SQLite, not a hub store" in capsys.readouterr().out
    assert cli.main(["export", "to-sqlite", str(out), str(tmp_path / "h.sqlite")]) == 2
    capsys.readouterr()
    (out / "notes.txt").write_text("x", "utf-8")
    assert cli.main(["export", "verify", str(out)]) == 1
    assert capsys.readouterr().out.startswith("EXPORT_INCONSISTENT: unlisted entries")
    assert cli.main(["export", "to-sqlite", str(out), str(tmp_path / "i.sqlite")]) == 1
    capsys.readouterr()
    assert cli.main(["export", "create", str(db), "--out", str(out)]) == 2
    assert "refusing to replace existing" in capsys.readouterr().err
    code = cli.main(
        [
            "export",
            "create",
            str(db),
            "--out",
            str(tmp_path / "k"),
            "--row-mac-key",
            str(tmp_path / "nokey"),
        ]
    )
    assert code == 2 and "row-authentication key" in capsys.readouterr().err


def test_failed_reads_and_writes_leave_nothing_behind(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A store that cannot be read leaves no directory; a failed copy leaves no file."""
    db, _key = _store(tmp_path)
    out = tmp_path / "export"
    with pytest.raises(ExportError, match="cannot read"):
        export_store(db, out, key_file=tmp_path / "missing.key")
    assert not out.exists()
    code = cli.main(
        [
            "export",
            "create",
            str(db),
            "--out",
            str(out),
            "--db-key-file",
            str(tmp_path / "missing.key"),
        ]
    )
    assert code == 2 and "cannot read" in capsys.readouterr().err and not out.exists()

    export_store(db, out)
    lines = (out / "events.jsonl").read_text("utf-8").splitlines(keepends=True)
    record = json.loads(lines[0])
    record["mac"] = {"not": "a value SQLite can store"}
    _rewrite(out, "events.jsonl", json.dumps(record) + "\n" + "".join(lines[1:]), fix_digest=True)
    plain = tmp_path / "copy.sqlite"
    with pytest.raises(sqlite3.Error):
        export_to_sqlite(out, plain)
    assert not plain.exists()
