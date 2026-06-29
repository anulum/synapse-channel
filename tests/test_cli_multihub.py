# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — `synapse multihub observe` CLI regressions

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from synapse_channel.cli_multihub import _cmd_observe, add_parsers
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    add_parsers(parser.add_subparsers())
    return parser


def _args(*argv: str) -> argparse.Namespace:
    return _parser().parse_args(["multihub", *argv])


def _peer_db(tmp_path: Path, name: str = "peer-east.db") -> str:
    path = tmp_path / name
    store = EventStore(str(path))
    store.append(
        EventKind.LEDGER_TASK, {"task_id": "T1", "title": "build", "status": "open"}, ts=1.0
    )
    store.append(
        EventKind.LEDGER_TASK, {"task_id": "T2", "title": "test", "status": "done"}, ts=2.0
    )
    store.append(EventKind.CLAIM, {"task_id": "T1", "owner": "alpha"}, ts=3.0)
    store.append(EventKind.LEDGER_PROGRESS, {"task_id": "T1", "text": "started"}, ts=4.0)
    store.close()
    return str(path)


def test_observe_prints_board_claims_and_progress(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = _args("observe", "--peer-db", _peer_db(tmp_path))
    assert _cmd_observe(args) == 0
    out = capsys.readouterr().out
    assert "observing peer 'peer-east' — 2 tasks, 1 progress notes, 1 observed claims" in out
    assert "[open] T1 — build" in out and "[done] T2 — test" in out
    assert "observed claims (advisory — not granted):" in out
    assert "T1 -> alpha @ peer-east" in out


def test_observe_json_with_peer_id_override(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = _args("observe", "--peer-db", _peer_db(tmp_path), "--peer-id", "east", "--json")
    assert _cmd_observe(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["peer_id"] == "east"
    assert payload["board"]["T2"]["status"] == "done"
    assert payload["observed_claims"]["T1"]["hub_id"] == "east"  # tagged with the override id
    assert payload["observed_claims"]["T1"]["observed"] is True


def test_observe_on_an_empty_peer_omits_the_sections(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    empty = tmp_path / "empty.db"
    EventStore(str(empty)).close()
    args = _args("observe", "--peer-db", str(empty))
    assert _cmd_observe(args) == 0
    out = capsys.readouterr().out
    assert "0 tasks, 0 progress notes, 0 observed claims" in out
    # the section bodies are omitted (the count line still names them)
    assert "board:\n" not in out
    assert "observed claims (advisory" not in out


def test_observe_reports_a_missing_database(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = _args("observe", "--peer-db", str(tmp_path / "nope.db"))
    assert _cmd_observe(args) == 2
    assert "peer database not found" in capsys.readouterr().err


def test_observe_reports_a_corrupt_database(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    corrupt = tmp_path / "corrupt.db"
    corrupt.write_bytes(b"this is not a sqlite database")
    args = _args("observe", "--peer-db", str(corrupt))
    assert _cmd_observe(args) == 2
    assert "could not read peer event store" in capsys.readouterr().err


def test_observe_handles_a_store_that_fails_to_open(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import sqlite3

    db = tmp_path / "present.db"
    db.write_text("exists", encoding="utf-8")  # passes the is_file check

    def _failing_factory(_path: str) -> EventStore:
        raise sqlite3.OperationalError("database is locked")

    args = _args("observe", "--peer-db", str(db))
    assert _cmd_observe(args, store_factory=_failing_factory) == 2
    assert "could not read peer event store" in capsys.readouterr().err


def test_observe_handles_a_read_failure_after_opening(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import sqlite3

    db = tmp_path / "present.db"
    db.write_text("exists", encoding="utf-8")

    class _BadReadStore:
        def read_since(self, _after_seq: int) -> list[object]:
            raise sqlite3.DatabaseError("disk I/O error")

        def close(self) -> None:
            return None

    args = _args("observe", "--peer-db", str(db))
    # opens fine but the read fails — the store is still closed in the finally
    assert _cmd_observe(args, store_factory=lambda _p: _BadReadStore()) == 2  # type: ignore[arg-type,return-value]
    assert "could not read peer event store" in capsys.readouterr().err


def test_peer_id_defaults_to_the_db_stem() -> None:
    args = _args("observe", "--peer-db", "/some/path/peer-west.db")
    assert args.peer_id is None  # the command fills the default from the stem
