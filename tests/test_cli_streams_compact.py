# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the compact CLI command

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import pytest

from synapse_channel import cli, cli_streams
from synapse_channel.core.persistence import EventStore

REPO_ROOT = Path(__file__).resolve().parents[1]


def _compact_ns(**overrides: Any) -> argparse.Namespace:
    base: dict[str, Any] = {
        "db": "events.db",
        "max_checkpoints_per_task": None,
        "finding_grace_seconds": None,
        "floor_seq": None,
        "all": False,
        "vacuum": False,
        "archive_report": None,
        "archive_report_limit": 200,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


def _seed_checkpoints(path: Path, task_id: str, count: int) -> None:
    store = EventStore(path)
    for index in range(count):
        store.append("checkpoint", {"task_id": task_id, "checkpoint": f"c{index}"}, ts=float(index))
    store.close()


def _repo_text(relative: str) -> str:
    return (REPO_ROOT / relative).read_text(encoding="utf-8")


def test_parser_compact() -> None:
    args = cli.build_parser().parse_args(
        [
            "compact",
            "hub.db",
            "--max-checkpoints-per-task",
            "3",
            "--all",
            "--vacuum",
            "--archive-report",
            "report.html",
            "--archive-report-limit",
            "50",
        ]
    )
    assert args.db == "hub.db"
    assert args.max_checkpoints_per_task == 3
    assert args.all is True
    assert args.vacuum is True
    assert args.archive_report == "report.html"
    assert args.archive_report_limit == 50
    assert args.func is cli_streams._cmd_compact


def test_parser_compact_floor_and_all_are_mutually_exclusive() -> None:
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["compact", "hub.db", "--floor-seq", "5", "--all"])


def test_public_docs_describe_compact_archive_report() -> None:
    combined = " ".join(
        "\n".join(
            [
                _repo_text("README.md"),
                _repo_text("docs/cli.md"),
            ]
        ).split()
    )

    assert "synapse compact ./synapse.db --all --max-checkpoints-per-task 3" in combined
    assert "--archive-report ./compact-report.html" in combined
    assert "pre-compaction event snapshot" in combined


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


def test_cmd_compact_writes_archive_report_from_pre_compaction_snapshot(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "events.db"
    _seed_checkpoints(db, "T1", 3)
    store = EventStore(db)
    store.append(
        "ledger_progress",
        {
            "task_id": "T1",
            "author": "ALPHA",
            "kind": "assessment",
            "text": "release receipt: evidence=pytest tests/test_cli_streams_compact.py -q",
            "posted_at": 10.0,
        },
        ts=10.0,
    )
    store.close()
    report = tmp_path / "archive" / "compact.html"

    rc = cli.main(
        [
            "compact",
            str(db),
            "--all",
            "--max-checkpoints-per-task",
            "1",
            "--archive-report",
            str(report),
            "--archive-report-limit",
            "20",
        ]
    )

    assert rc == 0
    output = capsys.readouterr().out
    assert "removed 2 checkpoint(s)" in output
    assert f"archive report: {report}" in output
    html = report.read_text(encoding="utf-8")
    assert "SYNAPSE archive report" in html
    assert "<dt>Total events before compaction</dt><dd>4</dd>" in html
    assert "removed 2 checkpoint(s), 0 finding(s)" in html
    assert "release receipt: evidence=pytest tests/test_cli_streams_compact.py -q" in html
    store = EventStore(db)
    assert store.count() == 2
    store.close()
