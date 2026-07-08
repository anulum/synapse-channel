# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — event-query CLI regressions

from __future__ import annotations

import json
from pathlib import Path

import pytest

from synapse_channel import cli
from synapse_channel.core.delivery_receipts import immediate_receipt_payload
from synapse_channel.core.journal import EventKind, record_claim
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.state import TaskClaim

REPO_ROOT = Path(__file__).resolve().parents[1]


def _claim() -> TaskClaim:
    return TaskClaim(
        task_id="DOCS",
        owner="writer",
        note="docs",
        claimed_at=10.0,
        lease_expires_at=100.0,
        status="claimed",
        data_ref="",
        worktree="repo",
        paths=("docs/cli.md",),
        epoch=1,
    )


def test_parser_wires_event_query_command() -> None:
    args = cli.build_parser().parse_args(["event-query", "hub.db", "task DOCS timeline"])

    assert args.command == "event-query"
    assert args.db == "hub.db"
    assert args.query == "task DOCS timeline"


def test_cli_event_query_json_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "events.db"
    store = EventStore(db)
    record_claim(store, _claim())
    store.close()

    exit_code = cli.main(["event-query", str(db), "task DOCS timeline", "--json"])

    assert exit_code == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["kind"] == "task_timeline"
    assert payload["records"][0]["task_id"] == "DOCS"


def test_cli_event_query_accepts_datalog_alias(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "events.db"
    store = EventStore(db)
    record_claim(store, _claim())
    store.close()

    exit_code = cli.main(["event-query", str(db), 'timeline("DOCS").', "--json"])

    assert exit_code == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["kind"] == "task_timeline"
    assert payload["records"][0]["task_id"] == "DOCS"


def test_cli_event_query_human_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "events.db"
    store = EventStore(db)
    record_claim(store, _claim())
    store.close()

    exit_code = cli.main(["event-query", str(db), "task DOCS timeline"])

    assert exit_code == 0
    assert "task DOCS timeline: 1 event(s)" in capsys.readouterr().out


def test_cli_event_query_channel_filter_redacts_payload(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db = tmp_path / "events.db"
    store = EventStore(db)
    store.append(
        EventKind.CHAT,
        {
            "sender": "alice",
            "target": "all",
            "payload": "private body",
            "channel": "ops",
            "msg_id": 1,
        },
        ts=2.0,
    )
    store.close()

    exit_code = cli.main(["event-query", str(db), "channel ops between seq 1 9", "--json"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["kind"] == "channel_events"
    assert payload["records"][0]["channel"] == "ops"
    assert "private body" not in str(payload)


def test_cli_event_query_delivery_receipts_json(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db = tmp_path / "events.db"
    store = EventStore(db)
    store.append(
        EventKind.DELIVERY_RECEIPT_IMMEDIATE,
        immediate_receipt_payload(
            sender="ALICE",
            target="BOB",
            message_id=1,
            message_seq=10,
            delivered=False,
            recipients=(),
        ),
        ts=10.0,
        durable=True,
    )
    store.close()

    exit_code = cli.main(["event-query", str(db), "receipts ALICE", "--json"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["kind"] == "delivery_receipts"
    assert payload["receipts"][0]["sender"] == "ALICE"


def test_cli_event_query_reports_query_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = cli.main(["event-query", str(tmp_path / "missing.db"), "task T timeline"])

    assert exit_code == 2
    assert "missing event store" in capsys.readouterr().err


def test_docs_wire_event_query_command() -> None:
    combined = "\n".join(
        [
            (REPO_ROOT / "README.md").read_text(encoding="utf-8"),
            (REPO_ROOT / "docs" / "cli.md").read_text(encoding="utf-8"),
            (REPO_ROOT / "docs" / "coordination-model.md").read_text(encoding="utf-8"),
            (REPO_ROOT / "docs" / "glossary.md").read_text(encoding="utf-8"),
        ]
    )

    assert "synapse event-query ./synapse.db" in combined
    assert "temporal event-log query" in combined
