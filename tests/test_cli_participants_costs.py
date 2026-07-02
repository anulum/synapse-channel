# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the session-cost subcommand of the participant CLI

"""Tests for ``synapse participant costs``.

The aggregation library has its own suite
(:mod:`tests.test_participant_session_metric_report`); these tests pin the CLI
contract — argument registration on the ``participant`` group, offline dispatch
against a real on-disk store seeded through the canonical note codec, both the
human and ``--json`` output shapes, the empty-store notice, and the exit-2
refusal for a missing store.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from synapse_channel.cli import build_parser
from synapse_channel.cli_participants_costs import _cmd_costs
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore
from synapse_channel.participants.session_metric_note import (
    SESSION_METRIC_NOTE_KIND,
    format_session_metric_note,
)
from synapse_channel.participants.session_telemetry import SessionMetrics


def _costs_args(*argv_tail: str) -> Any:
    return build_parser().parse_args(["participant", "costs", *argv_tail])


def _seed_store(db: Path) -> None:
    """Write two cumulative snapshots for one session plus a second session."""
    store = EventStore(db)
    try:
        store.append(
            EventKind.LEDGER_PROGRESS,
            {
                "kind": SESSION_METRIC_NOTE_KIND,
                "text": format_session_metric_note(
                    SessionMetrics(
                        turns=1,
                        errors=0,
                        abstentions=0,
                        input_tokens=100,
                        output_tokens=20,
                        cost_usd=0.05,
                        total_latency_seconds=1.0,
                        max_rate_limit_utilisation=None,
                        last_input_tokens=100,
                    )
                ),
                "author": "participant/claude",
                "task_id": "session-a",
            },
        )
        store.append(
            EventKind.LEDGER_PROGRESS,
            {
                "kind": SESSION_METRIC_NOTE_KIND,
                "text": format_session_metric_note(
                    SessionMetrics(
                        turns=3,
                        errors=1,
                        abstentions=0,
                        input_tokens=400,
                        output_tokens=90,
                        cost_usd=0.20,
                        total_latency_seconds=4.5,
                        max_rate_limit_utilisation=0.25,
                        last_input_tokens=180,
                    )
                ),
                "author": "participant/claude",
                "task_id": "session-a",
            },
        )
        store.append(
            EventKind.LEDGER_PROGRESS,
            {
                "kind": SESSION_METRIC_NOTE_KIND,
                "text": format_session_metric_note(
                    SessionMetrics(
                        turns=2,
                        errors=0,
                        abstentions=1,
                        input_tokens=50,
                        output_tokens=10,
                        cost_usd=0.01,
                        total_latency_seconds=0.8,
                        max_rate_limit_utilisation=None,
                        last_input_tokens=30,
                    )
                ),
                "author": "participant/ollama",
                "task_id": "session-b",
            },
        )
    finally:
        store.close()


# --- parser registration ------------------------------------------------------------


def test_costs_registered_on_the_participant_group() -> None:
    args = _costs_args("some.db")
    assert args.func is _cmd_costs
    assert args.db == "some.db"
    assert args.json is False


def test_costs_accepts_the_json_flag() -> None:
    assert _costs_args("some.db", "--json").json is True


# --- dispatch against a real store --------------------------------------------------


def test_missing_store_refuses_with_exit_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = _cmd_costs(_costs_args(str(tmp_path / "absent.db")))
    captured = capsys.readouterr()
    assert code == 2
    assert "missing event store" in captured.err
    assert captured.out == ""


def test_store_without_telemetry_reports_the_empty_notice(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    store = EventStore(db)
    try:
        store.append(EventKind.CHAT, {"text": "unrelated"})
    finally:
        store.close()
    code = _cmd_costs(_costs_args(str(db)))
    captured = capsys.readouterr()
    assert code == 0
    assert "No recorded session telemetry found." in captured.out


def test_human_report_keeps_latest_snapshot_and_totals(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed_store(db)
    code = _cmd_costs(_costs_args(str(db)))
    out = capsys.readouterr().out
    assert code == 0
    # The later cumulative snapshot for session-a supersedes the first one.
    assert "participant/claude/session-a: turns=3" in out
    assert "participant/ollama/session-b: turns=2" in out
    assert "totals: sessions=2 turns=5" in out
    assert "cost_usd=0.2100" in out
    assert "max_rate_limit=0.250" in out


def test_json_report_is_machine_readable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed_store(db)
    code = _cmd_costs(_costs_args(str(db), "--json"))
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["totals"]["sessions"] == 2
    assert payload["totals"]["cost_usd"] == pytest.approx(0.21)
    assert payload["totals"]["max_rate_limit_utilisation"] == pytest.approx(0.25)
    sessions = {
        (record["agent"], record["session_id"]): record for record in payload["sessions"]
    }
    assert sessions[("participant/claude", "session-a")]["turns"] == 3
    assert sessions[("participant/claude", "session-a")]["errors"] == 1
    assert sessions[("participant/ollama", "session-b")]["abstentions"] == 1
    assert str(payload["note"]).startswith("opt-in operational telemetry")
