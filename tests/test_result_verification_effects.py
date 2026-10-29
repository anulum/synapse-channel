# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — a deployed revision or a migration is verified from the world
"""Deployment and migration verifiers read; they do not deploy or migrate.

A worker that exits 0 after writing the wrong revision, or the wrong schema,
is reported ``confirmed_done`` and ``not_verified``.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from synapse_channel.cli import main
from synapse_channel.result_verification import read_outcome
from synapse_channel.result_verification_effects import (
    schema_digest,
    verify_deployment,
    verify_migration,
)

ROOT = Path(__file__).resolve().parents[1]
_ZERO = "0" * 64


def _report(path: Path, revision: str, health: str) -> None:
    path.write_text(
        json.dumps({"revision": revision, "health": health}),
        encoding="utf-8",
    )


def _applied(path: Path, migration: str = "0001_init", *, extra: str = "") -> None:
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE schema_migrations (version TEXT NOT NULL)")
    connection.execute("CREATE TABLE items (id INTEGER PRIMARY KEY)")
    if extra:
        connection.execute(extra)
    connection.execute(
        "INSERT INTO schema_migrations (version) VALUES (?)",
        (migration,),
    )
    connection.commit()
    connection.close()


def _schema(path: Path) -> str:
    """Learn the shipped schema digest from a deliberate mismatch."""
    verdict = verify_migration(path, "0001_init", _ZERO, operation_id="learn")
    assert verdict.observation == "schema_differs"
    return verdict.evidence["observed_schema_sha256"]


def test_deployment_accepts_only_the_requested_healthy_revision(tmp_path: Path) -> None:
    """A different revision or any health other than ``ok`` is not verified."""
    report = tmp_path / "health.json"
    with pytest.raises(ValueError, match="revision"):
        verify_deployment(report, "has space", operation_id="op")
    absent = verify_deployment(report, "abc123", operation_id="op")
    assert (absent.effect, absent.result, absent.observation) == (
        "absent",
        "not_verified",
        "report_absent",
    )
    _report(report, "other", "ok")
    differs = verify_deployment(report, "abc123", operation_id="op")
    assert (differs.effect, differs.result, differs.observation) == (
        "observed",
        "not_verified",
        "revision_differs",
    )
    _report(report, "abc123", "starting")
    unhealthy = verify_deployment(report, "abc123", operation_id="op")
    assert (unhealthy.effect, unhealthy.result, unhealthy.observation) == (
        "observed",
        "not_verified",
        "report_names_revision_but_not_ok",
    )
    _report(report, "abc123", "ok")
    verified = verify_deployment(report, "abc123", operation_id="op")
    assert verified.verified
    assert verified.observation == "report_names_revision_and_ok"
    assert verified.evidence["report_sha256"] == hashlib.sha256(report.read_bytes()).hexdigest()
    assert verified.evidence["report_mtime_ns"] == str(report.stat().st_mtime_ns)


def test_deployment_does_not_treat_an_unreadable_report_as_a_result(tmp_path: Path) -> None:
    """A link, a directory, a huge file, or a non-object is not a health report."""
    target = tmp_path / "real.json"
    _report(target, "abc123", "ok")
    link = tmp_path / "link.json"
    link.symlink_to(target)
    linked = verify_deployment(link, "abc123", operation_id="op")
    assert (linked.effect, linked.result) == ("unknown", "not_checked")
    directory = tmp_path / "dir.json"
    directory.mkdir()
    assert verify_deployment(directory, "abc123", operation_id="op").observation == (
        "report_not_a_readable_regular_file"
    )
    huge = tmp_path / "huge.json"
    huge.write_bytes(b"{" + b" " * 65536)
    assert verify_deployment(huge, "abc123", operation_id="op").observation == "report_too_large"
    binary = tmp_path / "binary.json"
    binary.write_bytes(b"\xff")
    assert verify_deployment(binary, "abc123", operation_id="op").observation == (
        "report_not_recognised"
    )
    for text in ("{", "[]", '{"revision": "abc123"}', '{"revision": 1, "health": "ok"}'):
        broken = tmp_path / "broken.json"
        broken.write_text(text, encoding="utf-8")
        assert verify_deployment(broken, "abc123", operation_id="op").observation == (
            "report_not_recognised"
        )


def test_deployment_without_nofollow_does_not_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without O_NOFOLLOW the health report is not opened."""
    import synapse_channel.result_verification_effects as module

    report = tmp_path / "health.json"
    _report(report, "abc123", "ok")
    monkeypatch.setattr(module, "_NOFOLLOW", None)
    verdict = verify_deployment(report, "abc123", operation_id="op")
    assert (verdict.effect, verdict.result) == ("unknown", "not_checked")


def test_a_worker_that_exits_zero_with_the_wrong_revision_is_not_verified(
    tmp_path: Path,
) -> None:
    """Acceptance: ``confirmed_done`` beside a deployed revision that is not the request."""
    health = tmp_path / "health.json"
    outcome = tmp_path / "outcome.json"
    payload = json.dumps({"revision": "wrong", "health": "ok"})
    code = (
        f"import pathlib; pathlib.Path({str(health)!r}).write_text({payload!r}, encoding='utf-8')"
    )
    worker = subprocess.run(
        [
            sys.executable,
            "-m",
            "synapse_channel.cli",
            "worker-session",
            "--identity",
            "PROJECT/worker",
            "--outcome-file",
            str(outcome),
            "--",
            sys.executable,
            "-c",
            code,
        ],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        capture_output=True,
        check=False,
    )
    assert worker.returncode == 0
    operation_id, state = read_outcome(outcome)
    assert state == "confirmed_done"
    verdict = verify_deployment(health, "abc123", operation_id=operation_id, communication=state)
    assert verdict.communication == "confirmed_done"
    assert (verdict.effect, verdict.result, verdict.observation) == (
        "observed",
        "not_verified",
        "revision_differs",
    )


def test_migration_matches_identity_and_schema_and_does_not_write(tmp_path: Path) -> None:
    """The recorded migration and the schema digest must both match."""
    database = tmp_path / "app.db"
    with pytest.raises(ValueError, match="migration identity"):
        verify_migration(database, "has space", _ZERO, operation_id="op")
    with pytest.raises(ValueError, match="schema digest"):
        verify_migration(database, "0001_init", "abc", operation_id="op")
    with pytest.raises(ValueError, match="plain identifiers"):
        verify_migration(
            database,
            "0001_init",
            _ZERO,
            operation_id="op",
            migrations_table="1bad",
        )
    absent = verify_migration(database, "0001_init", _ZERO, operation_id="op")
    assert (absent.effect, absent.result, absent.observation) == (
        "absent",
        "not_verified",
        "database_absent",
    )
    _applied(database)
    before = database.read_bytes()
    digest = _schema(database)
    verified = verify_migration(database, "0001_init", digest, operation_id="op")
    assert verified.verified
    assert verified.observation == "migration_applied_and_schema_matches"
    assert database.read_bytes() == before
    empty = tmp_path / "empty.db"
    connection = sqlite3.connect(empty)
    connection.execute("CREATE TABLE schema_migrations (version TEXT)")
    connection.commit()
    connection.close()
    missing = verify_migration(empty, "0001_init", digest, operation_id="op")
    assert (missing.effect, missing.result, missing.observation) == (
        "absent",
        "not_verified",
        "migration_absent",
    )
    other = tmp_path / "other.db"
    _applied(other, extra="CREATE TABLE extra (id INTEGER)")
    differs = verify_migration(other, "0001_init", digest, operation_id="op")
    assert (differs.effect, differs.result, differs.observation) == (
        "observed",
        "not_verified",
        "schema_differs",
    )


def test_migration_refuses_an_unreadable_database(tmp_path: Path) -> None:
    """A link, a directory, a non-database, or a missing table is not a verified schema."""
    real = tmp_path / "real.db"
    _applied(real)
    link = tmp_path / "link.db"
    link.symlink_to(real)
    assert verify_migration(link, "0001_init", _ZERO, operation_id="op").result == "not_checked"
    directory = tmp_path / "dir.db"
    directory.mkdir()
    assert (
        verify_migration(directory, "0001_init", _ZERO, operation_id="op").observation
        == "database_not_a_readable_regular_file"
    )
    text = tmp_path / "notes.db"
    text.write_text("not a database", encoding="utf-8")
    assert verify_migration(text, "0001_init", _ZERO, operation_id="op").observation == (
        "database_not_readable"
    )
    bare = tmp_path / "bare.db"
    connection = sqlite3.connect(bare)
    connection.execute("CREATE TABLE items (id INTEGER)")
    connection.commit()
    connection.close()
    assert (
        verify_migration(bare, "0001_init", _ZERO, operation_id="op").observation
        == "migrations_table_absent"
    )
    wrong_column = tmp_path / "column.db"
    connection = sqlite3.connect(wrong_column)
    connection.execute("CREATE TABLE schema_migrations (name TEXT)")
    connection.commit()
    connection.close()
    column = verify_migration(wrong_column, "0001_init", _ZERO, operation_id="op")
    assert (column.effect, column.result, column.observation) == (
        "absent",
        "not_verified",
        "migration_column_absent",
    )


def test_migration_without_nofollow_does_not_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without O_NOFOLLOW the database is not opened."""
    import synapse_channel.result_verification_effects as module

    database = tmp_path / "app.db"
    _applied(database)
    monkeypatch.setattr(module, "_NOFOLLOW", None)
    verdict = verify_migration(database, "0001_init", _ZERO, operation_id="op")
    assert (verdict.effect, verdict.result) == ("unknown", "not_checked")


def test_a_worker_that_exits_zero_with_the_wrong_schema_is_not_verified(
    tmp_path: Path,
) -> None:
    """Acceptance: ``confirmed_done`` beside a schema that is not the requested one."""
    intended = tmp_path / "intended.db"
    _applied(intended)
    digest = _schema(intended)
    produced = tmp_path / "produced.db"
    outcome = tmp_path / "outcome.json"
    code = (
        "import sqlite3; "
        f"connection = sqlite3.connect({str(produced)!r}); "
        "connection.execute('CREATE TABLE schema_migrations (version TEXT)'); "
        "connection.execute(\"INSERT INTO schema_migrations VALUES ('0001_init')\"); "
        "connection.execute('CREATE TABLE other (id INTEGER)'); "
        "connection.commit()"
    )
    worker = subprocess.run(
        [
            sys.executable,
            "-m",
            "synapse_channel.cli",
            "worker-session",
            "--identity",
            "PROJECT/worker",
            "--outcome-file",
            str(outcome),
            "--",
            sys.executable,
            "-c",
            code,
        ],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        capture_output=True,
        check=False,
    )
    assert worker.returncode == 0, worker.stderr
    operation_id, state = read_outcome(outcome)
    verdict = verify_migration(
        produced,
        "0001_init",
        digest,
        operation_id=operation_id,
        communication=state,
    )
    assert verdict.communication == "confirmed_done"
    assert (verdict.effect, verdict.result, verdict.observation) == (
        "observed",
        "not_verified",
        "schema_differs",
    )


def test_a_failed_stat_closes_the_report_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed fstat still closes the descriptor and does not verify the report."""
    report = tmp_path / "health.json"
    _report(report, "abc123", "ok")
    closed: list[int] = []
    real_close = os.close

    def spy(descriptor: int) -> None:
        closed.append(descriptor)
        real_close(descriptor)

    real_fstat = os.fstat

    def fail_stat(descriptor: int) -> os.stat_result:
        monkeypatch.setattr(os, "fstat", real_fstat)
        del descriptor
        raise OSError("stat failed")

    monkeypatch.setattr(os, "close", spy)
    monkeypatch.setattr(os, "fstat", fail_stat)
    verdict = verify_deployment(report, "abc123", operation_id="op")
    assert (verdict.effect, verdict.result) == ("unknown", "not_checked")
    assert closed


def test_replacing_the_database_with_a_link_is_not_checked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A swap onto another file between the check and connect is not a schema."""
    original = tmp_path / "original.db"
    _applied(original)
    other = tmp_path / "other.db"
    _applied(other, extra="CREATE TABLE extra (id INTEGER)")
    live = tmp_path / "live.db"
    live.write_bytes(original.read_bytes())
    real_resolve = Path.resolve

    def swap(self: Path, strict: bool = False) -> Path:
        if self == live:
            live.unlink()
            live.symlink_to(other.name)
            return real_resolve(other, strict=strict)
        return real_resolve(self, strict=strict)

    monkeypatch.setattr(Path, "resolve", swap)
    verdict = verify_migration(live, "0001_init", _ZERO, operation_id="op")
    assert (verdict.effect, verdict.result, verdict.observation) == (
        "unknown",
        "not_checked",
        "database_changed_during_open",
    )


def test_whitespace_changes_the_schema_digest(tmp_path: Path) -> None:
    """The same columns with different SQL whitespace are a different digest."""
    tight = tmp_path / "tight.db"
    loose = tmp_path / "loose.db"
    for path, statement in (
        (tight, "CREATE TABLE schema_migrations (version TEXT)"),
        (loose, "CREATE TABLE schema_migrations ( version TEXT )"),
    ):
        connection = sqlite3.connect(path)
        connection.execute(statement)
        connection.commit()
        connection.close()
    assert schema_digest(tight) != schema_digest(loose)


def test_wal_database_in_a_writable_directory_is_read(tmp_path: Path) -> None:
    """A WAL database the reader can write next to is still a readable schema."""
    database = tmp_path / "app.db"
    connection = sqlite3.connect(database)
    assert connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    connection.execute("CREATE TABLE schema_migrations (version TEXT NOT NULL)")
    connection.execute("INSERT INTO schema_migrations (version) VALUES ('0001_init')")
    connection.commit()
    connection.close()
    digest = schema_digest(database)
    verdict = verify_migration(database, "0001_init", digest, operation_id="op")
    assert verdict.verified
    assert verdict.observation == "migration_applied_and_schema_matches"


def test_migration_without_a_digest_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A migration check needs a digest, or the print flag, and otherwise refuses."""
    database = tmp_path / "app.db"
    _applied(database)
    assert (
        main(
            [
                "verify-result",
                "migration",
                "--database",
                str(database),
                "--operation-id",
                "op",
            ]
        )
        == 2
    )
    assert "schema-sha256" in capsys.readouterr().err


def test_print_schema_sha256_exits_without_a_verdict(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The print flag writes the digest and does not print a result record."""
    database = tmp_path / "app.db"
    _applied(database)
    assert (
        main(
            [
                "verify-result",
                "migration",
                "--database",
                str(database),
                "--print-schema-sha256",
            ]
        )
        == 0
    )
    printed = capsys.readouterr().out.strip()
    assert printed == schema_digest(database)
    assert "schema" not in printed or len(printed) == 64


def test_the_cli_verifies_deployment_and_migration(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``synapse verify-result deployment|migration`` exits by the result, not the reply."""
    health = tmp_path / "health.json"
    _report(health, "abc123", "ok")
    database = tmp_path / "app.db"
    _applied(database)
    digest = _schema(database)
    outcome = tmp_path / "outcome.json"
    outcome.write_text(
        json.dumps(
            {
                "schema": "synapse.execution-outcome.v1",
                "operation_id": "w-1",
                "state": "confirmed_done",
            }
        ),
        encoding="utf-8",
    )
    assert (
        main(
            [
                "verify-result",
                "deployment",
                "--revision",
                "abc123",
                "--report",
                str(health),
                "--outcome",
                str(outcome),
            ]
        )
        == 0
    )
    record = json.loads(capsys.readouterr().out)
    assert record["verifier"] == "deployment"
    assert record["result"] == "verified"
    assert record["communication"] == "confirmed_done"
    _report(health, "nope", "ok")
    assert (
        main(
            [
                "verify-result",
                "deployment",
                "--revision",
                "abc123",
                "--report",
                str(health),
                "--operation-id",
                "op",
            ]
        )
        == 1
    )
    capsys.readouterr()
    assert (
        main(
            [
                "verify-result",
                "migration",
                "--database",
                str(database),
                "--migration",
                "0001_init",
                "--schema-sha256",
                digest,
                "--operation-id",
                "op",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert (
        main(
            [
                "verify-result",
                "migration",
                "--database",
                str(tmp_path / "missing.db"),
                "--migration",
                "0001_init",
                "--schema-sha256",
                digest,
                "--operation-id",
                "op",
            ]
        )
        == 1
    )
    capsys.readouterr()
    assert (
        main(
            [
                "verify-result",
                "deployment",
                "--revision",
                "bad revision",
                "--report",
                str(health),
                "--operation-id",
                "op",
            ]
        )
        == 2
    )
    assert "plain token" in capsys.readouterr().err
