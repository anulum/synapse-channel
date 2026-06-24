# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the file/event-store CLI commands (relay/ingest/compact)

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pytest

from synapse_channel import cli, cli_streams
from synapse_channel.core.persistence import EventStore
from synapse_channel.relay import append_jsonl, encode_lite

# --- relay -------------------------------------------------------------------


def _relay_ns(**overrides: Any) -> argparse.Namespace:
    base: dict[str, Any] = {
        "relay_log": "feed.ndjson",
        "since": 0,
        "cursor": None,
        "for_name": None,
        "project": None,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


def _lite_line(log: Path, payload: str, msg_id: int) -> None:
    append_jsonl(
        log,
        encode_lite(
            {
                "sender": "A",
                "target": "all",
                "type": "chat",
                "payload": payload,
                "timestamp": 2.0,
                "msg_id": msg_id,
            }
        ),
    )


def test_parser_relay() -> None:
    args = cli.build_parser().parse_args(["relay", "feed.ndjson", "--since", "10"])
    assert args.relay_log == "feed.ndjson"
    assert args.since == 10
    assert args.cursor is None
    assert args.func is cli_streams._cmd_relay


def test_format_relay_line_renders_envelope() -> None:
    line = cli_streams._format_relay_line(
        {"timestamp": 1.5, "sender": "A", "target": "B", "type": "chat", "payload": "hi"}
    )
    assert line == "[1.500] A -> B (chat): hi"


def test_cmd_relay_prints_decoded_events(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    log = tmp_path / "feed.ndjson"
    _lite_line(log, "hello", 1)
    assert cli_streams._cmd_relay(_relay_ns(relay_log=str(log))) == 0
    assert "A -> all (chat): hello" in capsys.readouterr().out


def test_cmd_relay_resumes_from_cursor(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    log = tmp_path / "feed.ndjson"
    cursor = tmp_path / "feed.cursor"
    _lite_line(log, "one", 1)
    assert cli_streams._cmd_relay(_relay_ns(relay_log=str(log), cursor=str(cursor))) == 0
    assert "one" in capsys.readouterr().out

    _lite_line(log, "two", 2)
    # The persisted cursor means the second run shows only the newly appended line.
    assert cli_streams._cmd_relay(_relay_ns(relay_log=str(log), cursor=str(cursor))) == 0
    second = capsys.readouterr().out
    assert "two" in second
    assert "one" not in second


def test_cmd_relay_uses_since_offset(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    log = tmp_path / "feed.ndjson"
    _lite_line(log, "skip", 1)
    offset = log.stat().st_size
    _lite_line(log, "keep", 2)
    assert cli_streams._cmd_relay(_relay_ns(relay_log=str(log), since=offset)) == 0
    out = capsys.readouterr().out
    assert "keep" in out
    assert "skip" not in out


def test_cmd_relay_filters_by_recipient(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    log = tmp_path / "feed.ndjson"
    rows = [
        ("all", "chat", "everyone", 1),
        ("B,C", "chat", "you two", 2),
        ("C", "chat", "just C", 3),
        ("all", "presence_update", "noise", 4),
    ]
    for target, mtype, payload, mid in rows:
        append_jsonl(
            log,
            encode_lite(
                {
                    "sender": "A",
                    "target": target,
                    "type": mtype,
                    "payload": payload,
                    "timestamp": 2.0,
                    "msg_id": mid,
                }
            ),
        )
    assert cli_streams._cmd_relay(_relay_ns(relay_log=str(log), for_name="B")) == 0
    out = capsys.readouterr().out
    assert "everyone" in out  # broadcast reaches everyone
    assert "you two" in out  # B is one of several named recipients
    assert "just C" not in out  # addressed only to C
    assert "noise" not in out  # non-chat presence is dropped in the inbox view


def test_cmd_relay_filters_by_project(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    log = tmp_path / "feed.ndjson"
    rows = [
        ("all", "everyone", 1),
        ("quantum/claude-1", "to instance", 2),
        ("quantum/*", "to team", 3),
        ("other/codex-1", "elsewhere", 4),
    ]
    for target, payload, mid in rows:
        append_jsonl(
            log,
            encode_lite(
                {
                    "sender": "A",
                    "target": target,
                    "type": "chat",
                    "payload": payload,
                    "timestamp": 2.0,
                    "msg_id": mid,
                }
            ),
        )
    assert cli_streams._cmd_relay(_relay_ns(relay_log=str(log), project="quantum")) == 0
    out = capsys.readouterr().out
    assert "everyone" in out
    assert "to instance" in out
    assert "to team" in out
    assert "elsewhere" not in out


# --- ingest ------------------------------------------------------------------


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


# --- compact -----------------------------------------------------------------


def _compact_ns(**overrides: Any) -> argparse.Namespace:
    base: dict[str, Any] = {
        "db": "events.db",
        "max_checkpoints_per_task": None,
        "finding_grace_seconds": None,
        "floor_seq": None,
        "all": False,
        "vacuum": False,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


def _seed_checkpoints(path: Path, task_id: str, count: int) -> None:
    store = EventStore(path)
    for index in range(count):
        store.append("checkpoint", {"task_id": task_id, "checkpoint": f"c{index}"}, ts=float(index))
    store.close()


def test_parser_compact() -> None:
    args = cli.build_parser().parse_args(
        ["compact", "hub.db", "--max-checkpoints-per-task", "3", "--all", "--vacuum"]
    )
    assert args.db == "hub.db"
    assert args.max_checkpoints_per_task == 3
    assert args.all is True
    assert args.vacuum is True
    assert args.func is cli_streams._cmd_compact


def test_parser_compact_floor_and_all_are_mutually_exclusive() -> None:
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["compact", "hub.db", "--floor-seq", "5", "--all"])


def test_cmd_compact_requires_a_floor(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "events.db"
    _seed_checkpoints(db, "T1", 3)
    rc = cli_streams._cmd_compact(_compact_ns(db=str(db), max_checkpoints_per_task=1))
    assert rc == 2
    assert "needs a floor" in capsys.readouterr().err


def test_cmd_compact_requires_a_retention_knob(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "events.db"
    _seed_checkpoints(db, "T1", 3)
    rc = cli_streams._cmd_compact(_compact_ns(db=str(db), all=True))
    assert rc == 2
    assert "retention knob" in capsys.readouterr().err


def test_cmd_compact_rejects_an_invalid_policy(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "events.db"
    _seed_checkpoints(db, "T1", 3)
    rc = cli_streams._cmd_compact(_compact_ns(db=str(db), all=True, max_checkpoints_per_task=0))
    assert rc == 2
    assert "invalid retention policy" in capsys.readouterr().err


def test_cmd_compact_removes_superseded_checkpoints_with_all(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "events.db"
    _seed_checkpoints(db, "T1", 4)
    rc = cli_streams._cmd_compact(_compact_ns(db=str(db), all=True, max_checkpoints_per_task=1))
    assert rc == 0
    assert "removed 3 checkpoint(s), 0 finding(s)" in capsys.readouterr().out
    store = EventStore(db)
    survivors = [e.payload["checkpoint"] for e in store.read_all()]
    store.close()
    assert survivors == ["c3"]  # only the newest checkpoint per task remains


def test_cmd_compact_honours_an_explicit_floor(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "events.db"
    _seed_checkpoints(db, "T1", 3)
    store = EventStore(db)
    floor = store.read_all()[1].seq  # only the first two checkpoints are settled
    store.close()
    rc = cli_streams._cmd_compact(
        _compact_ns(db=str(db), floor_seq=floor, max_checkpoints_per_task=1)
    )
    assert rc == 0
    assert "removed 1 checkpoint(s)" in capsys.readouterr().out


def test_cmd_compact_with_vacuum_reports_and_reclaims(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "events.db"
    _seed_checkpoints(db, "T1", 4)
    rc = cli_streams._cmd_compact(
        _compact_ns(db=str(db), all=True, max_checkpoints_per_task=1, vacuum=True)
    )
    assert rc == 0
    assert "(vacuumed)" in capsys.readouterr().out
    store = EventStore(db)
    assert store.count() == 1
    store.close()
