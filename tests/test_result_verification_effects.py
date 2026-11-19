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
from synapse_channel.core.generation_chain import (
    MAX_GENERATION,
    publish_generation,
    seal_generation,
)
from synapse_channel.core.secure_path import apply_owner_only_dir
from synapse_channel.result_verification import read_outcome
from synapse_channel.result_verification_effects import (
    START_RECORD_SCHEMA,
    ServiceStartRecord,
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


_ART_A = "a" * 64
_ART_B = "b" * 64
_ART_C = "c" * 64


def _make_chain(tmp_path: Path, name: str = "chain") -> Path:
    chain = tmp_path / name
    chain.mkdir(mode=0o700)
    apply_owner_only_dir(chain)
    return chain


def _write_start_record(
    path: Path,
    *,
    generation: int,
    artefact: str,
    pid: int = 4321,
    started_at: str = "2026-11-19T05:30:00Z",
) -> ServiceStartRecord:
    record = ServiceStartRecord(
        generation=generation,
        artefact=artefact,
        pid=pid,
        started_at=started_at,
    )
    staging = path.parent / f".{path.name}.tmp"
    with staging.open("wb") as handle:
        handle.write(record.canonical_bytes())
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(staging, path)
    return record


def test_service_start_record_validates_fields_and_canonical_bytes() -> None:
    """ServiceStartRecord enforces exact schema, positive ints, 64-hex artefact, and ASCII time."""
    record = ServiceStartRecord(
        generation=1,
        artefact=_ART_A,
        pid=99,
        started_at="2026-11-19T05:30:00Z",
    )
    assert record.schema == START_RECORD_SCHEMA
    expected = (
        f'{{"artefact":"{_ART_A}","generation":1,"pid":99,'
        f'"schema":"{START_RECORD_SCHEMA}","started_at":"2026-11-19T05:30:00Z"}}'
    ).encode("ascii")
    assert record.canonical_bytes() == expected
    with pytest.raises(ValueError, match="schema"):
        ServiceStartRecord(1, _ART_A, 99, "2026-11-19T05:30:00Z", schema="wrong.v1")
    for bad_gen in (0, -1, MAX_GENERATION + 1):
        with pytest.raises(ValueError, match="integer|bound"):
            ServiceStartRecord(bad_gen, _ART_A, 99, "2026-11-19T05:30:00Z")
    with pytest.raises(ValueError, match="artefact"):
        ServiceStartRecord(1, "ABCD", 99, "2026-11-19T05:30:00Z")
    with pytest.raises(ValueError, match="integer"):
        ServiceStartRecord(1, _ART_A, 0, "2026-11-19T05:30:00Z")
    for bad_time in ("", "   ", "2026-11-19T05:30:00Z\n", "2026\x7f", "2026\u00e9"):
        with pytest.raises(ValueError, match="started_at"):
            ServiceStartRecord(1, _ART_A, 99, bad_time)


def test_chained_deployment_rejects_mixed_missing_or_malformed_arguments(
    tmp_path: Path,
) -> None:
    """verify_deployment refuses mixed report+chain calls, partial args, or bad tokens."""
    chain = _make_chain(tmp_path)
    report = tmp_path / "health.json"
    start = tmp_path / "start.json"
    with pytest.raises(ValueError, match="not both"):
        verify_deployment(report, "rev-1", operation_id="op", chain=chain)
    with pytest.raises(ValueError, match="together"):
        verify_deployment(None, "rev-1", operation_id="op", chain=chain, start_record=start)
    for bad_base in (True, False, -1, MAX_GENERATION):
        with pytest.raises(ValueError, match="base_generation"):
            verify_deployment(
                revision="rev-1",
                operation_id="op",
                chain=chain,
                start_record=start,
                base_generation=bad_base,
                base_artefact_sha256=_ZERO,
                artefact_sha256=_ART_A,
                attempt_id="att-1",
            )
    with pytest.raises(ValueError, match="base_artefact_sha256"):
        verify_deployment(
            revision="rev-1",
            operation_id="op",
            chain=chain,
            start_record=start,
            base_generation=0,
            base_artefact_sha256="BAD",
            artefact_sha256=_ART_A,
            attempt_id="att-1",
        )
    with pytest.raises(ValueError, match="artefact_sha256"):
        verify_deployment(
            revision="rev-1",
            operation_id="op",
            chain=chain,
            start_record=start,
            base_generation=0,
            base_artefact_sha256=_ZERO,
            artefact_sha256="BAD",
            attempt_id="att-1",
        )
    with pytest.raises(ValueError, match="attempt_id"):
        verify_deployment(
            revision="rev-1",
            operation_id="op",
            chain=chain,
            start_record=start,
            base_generation=0,
            base_artefact_sha256=_ZERO,
            artefact_sha256=_ART_A,
            attempt_id="bad attempt",
        )


def test_chained_deployment_reports_unreadable_or_corrupt_chain(tmp_path: Path) -> None:
    """Missing, symlinked, loose, gapped, or non-canonical chains return chain_unreadable."""
    start = tmp_path / "start.json"
    missing = verify_deployment(
        revision="rev-1",
        operation_id="op",
        chain=tmp_path / "absent",
        start_record=start,
        base_generation=0,
        base_artefact_sha256=_ZERO,
        artefact_sha256=_ART_A,
        attempt_id="att-1",
    )
    assert (missing.effect, missing.result, missing.observation) == (
        "unknown",
        "not_checked",
        "chain_unreadable",
    )
    real = _make_chain(tmp_path, "real_chain")
    linked = tmp_path / "linked_chain"
    linked.symlink_to(real)
    via_link = verify_deployment(
        revision="rev-1",
        operation_id="op",
        chain=linked,
        start_record=start,
        base_generation=0,
        base_artefact_sha256=_ZERO,
        artefact_sha256=_ART_A,
        attempt_id="att-1",
    )
    assert (via_link.effect, via_link.result, via_link.observation) == (
        "unknown",
        "not_checked",
        "chain_unreadable",
    )
    if os.name == "posix":
        loose = tmp_path / "loose_chain"
        loose.mkdir(mode=0o755)
        os.chmod(loose, 0o755)
        loose_verdict = verify_deployment(
            revision="rev-1",
            operation_id="op",
            chain=loose,
            start_record=start,
            base_generation=0,
            base_artefact_sha256=_ZERO,
            artefact_sha256=_ART_A,
            attempt_id="att-1",
        )
        assert loose_verdict.observation == "chain_unreadable"
    gapped = _make_chain(tmp_path, "gapped_chain")
    (gapped / "gen-00000000000000000002.json").write_bytes(b"{}")
    gapped_verdict = verify_deployment(
        revision="rev-1",
        operation_id="op",
        chain=gapped,
        start_record=start,
        base_generation=0,
        base_artefact_sha256=_ZERO,
        artefact_sha256=_ART_A,
        attempt_id="att-1",
    )
    assert (gapped_verdict.effect, gapped_verdict.result, gapped_verdict.observation) == (
        "unknown",
        "not_checked",
        "chain_unreadable",
    )
    corrupt = _make_chain(tmp_path, "corrupt_chain")
    (corrupt / "gen-00000000000000000001.json").write_bytes(b"not-json\n")
    corrupt_verdict = verify_deployment(
        revision="rev-1",
        operation_id="op",
        chain=corrupt,
        start_record=start,
        base_generation=0,
        base_artefact_sha256=_ZERO,
        artefact_sha256=_ART_A,
        attempt_id="att-1",
    )
    assert (corrupt_verdict.effect, corrupt_verdict.result, corrupt_verdict.observation) == (
        "unknown",
        "not_checked",
        "chain_unreadable",
    )


def test_chained_deployment_checks_declared_prestate_at_zero_and_nonzero_base(
    tmp_path: Path,
) -> None:
    """A mismatched base_artefact_sha256 (including non-zero at base 0) yields base_differs."""
    chain = _make_chain(tmp_path)
    start = tmp_path / "start.json"
    publish_generation(chain, base=0, operation="op", attempt="att-1", artefact=_ART_A)
    _write_start_record(start, generation=1, artefact=_ART_A)
    nonzero_at_zero = verify_deployment(
        revision="rev-1",
        operation_id="op",
        chain=chain,
        start_record=start,
        base_generation=0,
        base_artefact_sha256=_ART_B,
        artefact_sha256=_ART_A,
        attempt_id="att-1",
    )
    assert (nonzero_at_zero.effect, nonzero_at_zero.result, nonzero_at_zero.observation) == (
        "observed",
        "not_verified",
        "base_differs",
    )
    wrong_base_slot = verify_deployment(
        revision="rev-2",
        operation_id="op-2",
        chain=chain,
        start_record=start,
        base_generation=1,
        base_artefact_sha256=_ART_B,
        artefact_sha256=_ART_C,
        attempt_id="att-2",
    )
    assert (wrong_base_slot.effect, wrong_base_slot.result, wrong_base_slot.observation) == (
        "observed",
        "not_verified",
        "base_differs",
    )
    assert wrong_base_slot.evidence["observed_base_artefact_sha256"] == _ART_A
    empty = _make_chain(tmp_path, "empty_chain")
    missing_base_slot = verify_deployment(
        revision="rev-2",
        operation_id="op-2",
        chain=empty,
        start_record=start,
        base_generation=1,
        base_artefact_sha256=_ART_A,
        artefact_sha256=_ART_B,
        attempt_id="att-2",
    )
    assert (missing_base_slot.effect, missing_base_slot.result, missing_base_slot.observation) == (
        "observed",
        "not_verified",
        "base_differs",
    )


def test_chained_deployment_distinguishes_absent_sealed_foreign_and_wrong_artefact_slots(
    tmp_path: Path,
) -> None:
    """Unpublished, sealed, foreign-attempt, and wrong-artefact slots are never verified."""
    chain = _make_chain(tmp_path)
    start = tmp_path / "start.json"
    _write_start_record(start, generation=1, artefact=_ART_A)
    unpublished = verify_deployment(
        revision="rev-1",
        operation_id="op",
        chain=chain,
        start_record=start,
        base_generation=0,
        base_artefact_sha256=_ZERO,
        artefact_sha256=_ART_A,
        attempt_id="att-1",
    )
    assert (unpublished.effect, unpublished.result, unpublished.observation) == (
        "absent",
        "not_verified",
        "not_published",
    )
    sealed_chain = _make_chain(tmp_path, "sealed_chain")
    sealed_out = seal_generation(sealed_chain, base=0, operation="op", attempt="stopper-1")
    sealed = verify_deployment(
        revision="rev-1",
        operation_id="op",
        chain=sealed_chain,
        start_record=start,
        base_generation=0,
        base_artefact_sha256=_ZERO,
        artefact_sha256=_ART_A,
        attempt_id="att-1",
    )
    assert (sealed.effect, sealed.result, sealed.observation) == (
        "absent",
        "not_verified",
        "sealed_before_publication",
    )
    assert (
        sealed.evidence["slot_sha256"]
        == hashlib.sha256(sealed_out.record.canonical_bytes()).hexdigest()
    )
    publish_generation(chain, base=0, operation="op", attempt="att-other", artefact=_ART_A)
    foreign = verify_deployment(
        revision="rev-1",
        operation_id="op",
        chain=chain,
        start_record=start,
        base_generation=0,
        base_artefact_sha256=_ZERO,
        artefact_sha256=_ART_A,
        attempt_id="att-1",
    )
    assert (foreign.effect, foreign.result, foreign.observation) == (
        "absent",
        "not_verified",
        "slot_taken_by_another_attempt",
    )
    wrong_art = verify_deployment(
        revision="rev-1",
        operation_id="op",
        chain=chain,
        start_record=start,
        base_generation=0,
        base_artefact_sha256=_ZERO,
        artefact_sha256=_ART_B,
        attempt_id="att-other",
    )
    assert (wrong_art.effect, wrong_art.result, wrong_art.observation) == (
        "observed",
        "not_verified",
        "artefact_differs",
    )


def test_chained_deployment_suspends_on_non_convergence_and_verifies_on_convergence(
    tmp_path: Path,
) -> None:
    """Published slots suspend until start_record converges, then verify or flag drift."""
    chain = _make_chain(tmp_path)
    start = tmp_path / "start.json"
    first = publish_generation(chain, base=0, operation="op-1", attempt="att-1", artefact=_ART_A)
    absent_start = verify_deployment(
        revision="rev-1",
        operation_id="op-1",
        chain=chain,
        start_record=start,
        base_generation=0,
        base_artefact_sha256=_ZERO,
        artefact_sha256=_ART_A,
        attempt_id="att-1",
    )
    assert (absent_start.effect, absent_start.result, absent_start.observation) == (
        "observed",
        "not_checked",
        "published_not_converged",
    )
    assert absent_start.result != "not_verified"
    written_one = _write_start_record(start, generation=1, artefact=_ART_A, pid=777)
    converged_zero = verify_deployment(
        revision="rev-1",
        operation_id="op-1",
        chain=chain,
        start_record=start,
        base_generation=0,
        base_artefact_sha256=_ZERO,
        artefact_sha256=_ART_A,
        attempt_id="att-1",
    )
    assert converged_zero.verified
    assert (converged_zero.effect, converged_zero.result, converged_zero.observation) == (
        "observed",
        "verified",
        "published_and_converged",
    )
    assert converged_zero.evidence["chain_head"] == "1"
    assert (
        converged_zero.evidence["slot_sha256"]
        == hashlib.sha256(first.record.canonical_bytes()).hexdigest()
    )
    assert (
        converged_zero.evidence["start_record_sha256"]
        == hashlib.sha256(written_one.canonical_bytes()).hexdigest()
    )
    assert converged_zero.evidence["start_record_mtime_ns"] == str(start.stat().st_mtime_ns)
    assert converged_zero.evidence["observed_start_pid"] == "777"

    publish_generation(chain, base=1, operation="op-2", attempt="att-2", artefact=_ART_B)
    lagging = verify_deployment(
        revision="rev-2",
        operation_id="op-2",
        chain=chain,
        start_record=start,
        base_generation=1,
        base_artefact_sha256=_ART_A,
        artefact_sha256=_ART_B,
        attempt_id="att-2",
    )
    assert (lagging.effect, lagging.result, lagging.observation) == (
        "observed",
        "not_checked",
        "published_not_converged",
    )
    _write_start_record(start, generation=2, artefact=_ART_C, pid=888)
    bad_converged = verify_deployment(
        revision="rev-2",
        operation_id="op-2",
        chain=chain,
        start_record=start,
        base_generation=1,
        base_artefact_sha256=_ART_A,
        artefact_sha256=_ART_B,
        attempt_id="att-2",
    )
    assert (bad_converged.effect, bad_converged.result, bad_converged.observation) == (
        "observed",
        "not_verified",
        "converged_artefact_differs",
    )
    _write_start_record(start, generation=2, artefact=_ART_B, pid=889)
    converged_one = verify_deployment(
        revision="rev-2",
        operation_id="op-2",
        chain=chain,
        start_record=start,
        base_generation=1,
        base_artefact_sha256=_ART_A,
        artefact_sha256=_ART_B,
        attempt_id="att-2",
    )
    assert converged_one.verified
    assert converged_one.observation == "published_and_converged"
    superseded = verify_deployment(
        revision="rev-1",
        operation_id="op-1",
        chain=chain,
        start_record=start,
        base_generation=0,
        base_artefact_sha256=_ZERO,
        artefact_sha256=_ART_A,
        attempt_id="att-1",
    )
    assert (superseded.effect, superseded.result, superseded.observation) == (
        "observed",
        "not_checked",
        "superseded_before_convergence_seen",
    )


def test_chained_deployment_maps_every_malformed_start_record_to_not_recognised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Present but unreadable or non-canonical start records return observed/not_checked."""
    import synapse_channel.result_verification_effects as module

    chain = _make_chain(tmp_path)
    publish_generation(chain, base=0, operation="op", attempt="att-1", artefact=_ART_A)
    real_start = tmp_path / "real_start.json"
    valid = _write_start_record(real_start, generation=1, artefact=_ART_A)

    def check(path: Path) -> None:
        verdict = verify_deployment(
            revision="rev-1",
            operation_id="op",
            chain=chain,
            start_record=path,
            base_generation=0,
            base_artefact_sha256=_ZERO,
            artefact_sha256=_ART_A,
            attempt_id="att-1",
        )
        assert (verdict.effect, verdict.result, verdict.observation) == (
            "observed",
            "not_checked",
            "start_record_not_recognised",
        )

    link = tmp_path / "link_start.json"
    link.symlink_to(real_start)
    check(link)
    directory = tmp_path / "dir_start.json"
    directory.mkdir()
    check(directory)
    oversize = tmp_path / "huge_start.json"
    oversize.write_bytes(b"{" + b" " * 4096)
    check(oversize)

    bad = tmp_path / "bad_start.json"
    payloads = [
        b"\xff",
        b"{",
        b"[]",
        valid.canonical_bytes() + b"\n",
        json.dumps(valid.to_dict(), sort_keys=True).encode("ascii"),
        (
            f'{{"schema":"{START_RECORD_SCHEMA}","generation":1,'
            f'"artefact":"{_ART_A}","pid":1,"started_at":"2026-11-19T05:30:00Z"}}'
        ).encode("ascii"),
        (
            f'{{"artefact":"{_ART_A}","artefact":"{_ART_A}","generation":1,'
            f'"pid":1,"schema":"{START_RECORD_SCHEMA}","started_at":"2026-11-19T05:30:00Z"}}'
        ).encode("ascii"),
        (
            f'{{"artefact":"{_ART_A}","extra":1,"generation":1,'
            f'"pid":1,"schema":"{START_RECORD_SCHEMA}","started_at":"2026-11-19T05:30:00Z"}}'
        ).encode("ascii"),
        f'{{"artefact":"{_ART_A}","generation":1,"pid":1,"schema":"{START_RECORD_SCHEMA}"}}'.encode(
            "ascii"
        ),
        (
            f'{{"artefact":"{_ART_A}","generation":"1",'
            f'"pid":1,"schema":"{START_RECORD_SCHEMA}","started_at":"2026-11-19T05:30:00Z"}}'
        ).encode("ascii"),
        (
            f'{{"artefact":"{_ART_A}","generation":true,'
            f'"pid":1,"schema":"{START_RECORD_SCHEMA}","started_at":"2026-11-19T05:30:00Z"}}'
        ).encode("ascii"),
        (
            f'{{"artefact":"{_ART_A}","generation":0,'
            f'"pid":1,"schema":"{START_RECORD_SCHEMA}","started_at":"2026-11-19T05:30:00Z"}}'
        ).encode("ascii"),
        (
            f'{{"artefact":1,"generation":1,'
            f'"pid":1,"schema":"{START_RECORD_SCHEMA}","started_at":"2026-11-19T05:30:00Z"}}'
        ).encode("ascii"),
        (
            f'{{"artefact":"{_ART_A}","generation":1,'
            f'"pid":1,"schema":"wrong.v1","started_at":"2026-11-19T05:30:00Z"}}'
        ).encode("ascii"),
        (
            f'{{"artefact":"{_ART_A}","generation":1,'
            f'"pid":1,"schema":"{START_RECORD_SCHEMA}","started_at":"2026\\n"}}'
        ).encode("ascii"),
    ]
    for raw in payloads:
        bad.write_bytes(raw)
        check(bad)

    monkeypatch.setattr(module, "_NOFOLLOW", None)
    check(real_start)


def test_cli_chained_deployment_exit_codes_and_worker_session_boundary(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """CLI verify-result deployment handles chained flags, exit codes 0/1/2/3, and outcomes."""
    chain = _make_chain(tmp_path)
    start = tmp_path / "start.json"
    outcome = tmp_path / "outcome.json"
    code = (
        "import os; from pathlib import Path; "
        "from synapse_channel.core.generation_chain import publish_generation; "
        f"publish_generation(Path({str(chain)!r}), base=0, "
        "operation=os.environ['SYN_OPERATION_ID'], "
        f"attempt='att-cli', artefact={_ART_A!r})"
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
    common = [
        "verify-result",
        "deployment",
        "--revision",
        "rev-1",
        "--chain",
        str(chain),
        "--start-record",
        str(start),
        "--base-generation",
        "0",
        "--base-artefact-sha256",
        _ZERO,
        "--artefact-sha256",
        _ART_A,
        "--attempt-id",
        "att-cli",
        "--outcome",
        str(outcome),
    ]
    assert main(common) == 3
    pending = json.loads(capsys.readouterr().out)
    assert pending["communication"] == "confirmed_done"
    assert pending["observation"] == "published_not_converged"

    _write_start_record(start, generation=1, artefact=_ART_A, pid=5150)
    sub = subprocess.run(
        [sys.executable, "-m", "synapse_channel.cli", *common],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        capture_output=True,
        text=True,
        check=False,
    )
    assert sub.returncode == 0, sub.stderr
    converged = json.loads(sub.stdout)
    assert converged["result"] == "verified"
    assert converged["observation"] == "published_and_converged"

    assert (
        main(
            [
                "verify-result",
                "deployment",
                "--revision",
                "rev-1",
                "--chain",
                str(chain),
                "--start-record",
                str(start),
                "--base-generation",
                "0",
                "--base-artefact-sha256",
                _ART_B,
                "--artefact-sha256",
                _ART_A,
                "--attempt-id",
                "att-cli",
                "--operation-id",
                "op-cli",
            ]
        )
        == 1
    )
    capsys.readouterr()
    for bad_gen in ("01", "-1", "abc", "0\n"):
        assert (
            main(
                [
                    "verify-result",
                    "deployment",
                    "--revision",
                    "rev-1",
                    "--chain",
                    str(chain),
                    "--start-record",
                    str(start),
                    "--base-generation",
                    bad_gen,
                    "--base-artefact-sha256",
                    _ZERO,
                    "--artefact-sha256",
                    _ART_A,
                    "--attempt-id",
                    "att-cli",
                    "--operation-id",
                    "op-cli",
                ]
            )
            == 2
        )
        capsys.readouterr()
