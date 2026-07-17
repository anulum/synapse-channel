# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the ingest CLI command

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pytest

from synapse_channel import cli, cli_streams
from synapse_channel.core.persistence import EventStore


def _ingest_ns(**overrides: Any) -> argparse.Namespace:
    base: dict[str, Any] = {
        "db": "events.db",
        "since": 0,
        "cursor": None,
        "kind": None,
        "memory": False,
        "limit": None,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


def _seed_store(path: Path) -> None:
    store = EventStore(path)
    store.append("claim", {"id": "T1"})
    store.append("finding", {"statement": "a"})
    store.append("chat", {"payload": "x"})
    store.close()


def test_parser_ingest() -> None:
    args = cli.build_parser().parse_args(["ingest", "hub.db", "--memory", "--since", "5"])
    assert args.db == "hub.db"
    assert args.memory is True
    assert args.since == 5
    assert args.func is cli_streams._cmd_ingest


def test_cmd_ingest_streams_events_as_ndjson(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "events.db"
    _seed_store(db)
    assert cli_streams._cmd_ingest(_ingest_ns(db=str(db))) == 0
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [row["kind"] for row in lines] == ["claim", "finding", "chat"]
    assert lines[1]["payload"]["statement"] == "a"


def test_cmd_ingest_memory_filter_drops_coordination_kinds(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "events.db"
    _seed_store(db)
    assert cli_streams._cmd_ingest(_ingest_ns(db=str(db), memory=True)) == 0
    kinds = [json.loads(line)["kind"] for line in capsys.readouterr().out.splitlines()]
    assert kinds == ["finding"]  # claim + chat are not memory kinds


def test_cmd_ingest_explicit_kind_filter(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "events.db"
    _seed_store(db)
    assert cli_streams._cmd_ingest(_ingest_ns(db=str(db), kind=["claim"])) == 0
    kinds = [json.loads(line)["kind"] for line in capsys.readouterr().out.splitlines()]
    assert kinds == ["claim"]


def test_cmd_ingest_resumes_from_cursor(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "events.db"
    cursor = tmp_path / "ingest.cursor"
    store = EventStore(db)
    store.append("finding", {"statement": "first"})
    store.close()
    assert cli_streams._cmd_ingest(_ingest_ns(db=str(db), cursor=str(cursor))) == 0
    assert "first" in capsys.readouterr().out

    store = EventStore(db)
    store.append("finding", {"statement": "second"})
    store.close()
    # The persisted seq cursor means the second run shows only the new event.
    assert cli_streams._cmd_ingest(_ingest_ns(db=str(db), cursor=str(cursor))) == 0
    out = capsys.readouterr().out
    assert "second" in out
    assert "first" not in out


def test_cmd_ingest_limit_caps_the_batch(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "events.db"
    _seed_store(db)
    assert cli_streams._cmd_ingest(_ingest_ns(db=str(db), limit=1)) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1


def test_cmd_ingest_surfaces_safe_corrupt_marker_and_advances_cursor(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "events.db"
    cursor = tmp_path / "ingest.cursor"
    store = EventStore(db)
    seq = store.append("claim", {"task_id": "T1"})
    secret = "do-not-print-raw-payload"
    store._conn.execute("UPDATE events SET payload = ? WHERE seq = ?", (secret, seq))
    store._conn.commit()
    store.close()

    assert cli_streams._cmd_ingest(_ingest_ns(db=str(db), cursor=str(cursor))) == 0
    first_output = capsys.readouterr().out
    marker = json.loads(first_output)
    assert marker["seq"] == seq
    assert marker["kind"] == "corrupt_event"
    assert marker["payload"]["reasons"] == ["invalid_json"]
    assert secret not in first_output

    assert cli_streams._cmd_ingest(_ingest_ns(db=str(db), cursor=str(cursor))) == 0
    assert capsys.readouterr().out == ""
