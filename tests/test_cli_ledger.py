# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for `synapse ledger` and the v0 reader
"""``synapse ledger show`` and ``read-v0``, and the mapping of the interim v0 files."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pytest

from synapse_channel import cli_ledger
from synapse_channel.session_ledger import SCHEMA, V0_SCHEMA, append_event, read_v0

V0: list[dict[str, Any]] = [
    {
        "event": "observed",
        "key": "synapse-a",
        "row_id": "B:1:10",
        "identity": "P/a",
        "vendor": "claude",
        "pid": 1,
        "pid_starttime": 10,
        "native_session": "n-a",
        "resume_argv": ["claude", "--resume", "n-a"],
    },
    {
        "event": "started",
        "key": "bare:agy:2",
        "row_id": "B:2:20",
        "identity": "P/b",
        "vendor": "agy",
        "pid": 2,
        "pid_starttime": 20,
        "native_session_id": "n-b",
        "resume_argv": ["agy", "--dangerously-skip-permissions", "--conversation", "n-b"],
    },
    {
        "event": "changed",
        "key": "synapse-a",
        "row_id": "B:1:10",
        "fields": ["label"],
        "native_session_id": "n-a2",
    },
    {
        "event": "changed",
        "key": "synapse-a",
        "row_id": "B:3:30",
        "fields": ["agent_row"],
        "identity": "P/a",
        "vendor": "codex",
        "pid": 3,
        "pid_starttime": 30,
    },
    {
        "event": "ended_unobserved",
        "key": "bare:agy:2",
        "row_id": "B:2:20",
        "end_kind": "boot_cut",
        "ended_at": "t2",
        "last_seen": "t1",
    },
    {"event": "screens", "key": "synapse-a"},  # no row and an unknown kind: nothing is read
]


def _v0_file(tmp_path: Path) -> Path:
    path = tmp_path / "events-2026-11.jsonl"
    lines = [
        json.dumps({"schema": V0_SCHEMA, "boot_id": "B", "ts": "t", "seq": n, **e})
        for n, e in enumerate(V0, 1)
    ]
    path.write_text("\n".join(["not json", '{"schema": "other"}', *lines]) + "\n", encoding="utf-8")
    return path


def test_the_v0_reader_maps_every_event_kind(tmp_path: Path) -> None:
    events = list(read_v0(_v0_file(tmp_path)))
    kinds = [(event["event"], event["row_id"]) for event in events]
    assert kinds == [
        ("start", "B:1:10"),
        ("start", "B:2:20"),
        ("observe", "B:1:10"),
        ("ended_unobserved", "B:1:10"),  # the agent in that pane was replaced
        ("start", "B:3:30"),
        ("ended_unobserved", "B:2:20"),
    ]
    assert all(
        event["schema"] == SCHEMA and event["source_schema"] == V0_SCHEMA for event in events
    )
    first, bare = events[0], events[1]
    assert first["launcher"] == "observed" and first["tmux"] == {"session": "synapse-a"}
    assert first["native_session_id"] == "n-a" and bare["tmux"] is None
    assert not any("resume_argv" in event for event in events)  # rebuilt, never carried over
    assert events[2]["native_session_id"] == "n-a2"
    assert (events[5]["end_kind"], events[5]["last_seen"]) == ("boot_cut", "t1")


def _args(**fields: object) -> argparse.Namespace:
    return argparse.Namespace(**{"all": False, "boot": None, "json": False, **fields})


def test_show_lists_open_rows_and_all_rows_on_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = tmp_path / "ledger"
    monkeypatch.setattr(cli_ledger, "ledger_dir", lambda: ledger)
    for row, identity in (("B:1:10", "P/a"), ("C:2:20", "P/b")):
        append_event(
            {
                "event": "start",
                "row_id": row,
                "identity": identity,
                "vendor": "claude",
                "launcher": "seat-hold",
            },
            directory=ledger,
            boot=row[0],
        )
    append_event(
        {"event": "observe", "row_id": "B:1:10", "native_session_id": "n-1"},
        directory=ledger,
        boot="B",
    )
    append_event(
        {"event": "end", "row_id": "C:2:20", "end_kind": "exit", "exit_status": 0},
        directory=ledger,
        boot="C",
    )

    assert cli_ledger._cmd_show(_args()) == 0
    assert capsys.readouterr().out == "P/a  claude  seat-hold  open  n-1  B:1:10\n"
    assert cli_ledger._cmd_show(_args(all=True, boot="C")) == 0
    assert capsys.readouterr().out == "P/b  claude  seat-hold  exit  -  C:2:20\n"
    assert cli_ledger._cmd_show(_args(json=True)) == 0
    (row,) = json.loads(capsys.readouterr().out)
    assert row["row_id"] == "B:1:10" and row["observed"]["native_session_id"] == "n-1"


def test_show_on_a_host_with_no_ledger_yet_lists_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli_ledger, "ledger_dir", lambda: tmp_path / "absent")
    assert cli_ledger._cmd_show(_args(all=True)) == 0
    assert capsys.readouterr().out == ""


def test_show_refuses_a_ledger_it_may_not_use(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    assert cli_ledger._cmd_show(_args()) == cli_ledger.USAGE_EXIT_CODE == 2
    assert "shared or versioned tree" in capsys.readouterr().out


def test_read_v0_prints_v1_events_and_refuses_a_missing_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli_ledger._cmd_read_v0(argparse.Namespace(path=_v0_file(tmp_path))) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 6 and json.loads(lines[0])["event"] == "start"
    missing = tmp_path / "absent.jsonl"
    assert cli_ledger._cmd_read_v0(argparse.Namespace(path=missing)) == 2
    assert capsys.readouterr().out.startswith(f"synapse ledger: cannot read {missing}")


def test_the_parser_registers_show_and_read_v0() -> None:
    parser = argparse.ArgumentParser()
    cli_ledger.add_parsers(parser.add_subparsers())
    show = parser.parse_args(["ledger", "show", "--all", "--boot", "B", "--json"])
    assert show.func is cli_ledger._cmd_show and (show.all, show.boot, show.json) == (
        True,
        "B",
        True,
    )
    v0 = parser.parse_args(["ledger", "read-v0", "/x/events.jsonl"])
    assert v0.func is cli_ledger._cmd_read_v0 and v0.path == Path("/x/events.jsonl")
