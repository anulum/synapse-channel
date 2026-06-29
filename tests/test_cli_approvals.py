# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — human-in-the-loop approval gate CLI regressions

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

import pytest

from hub_e2e_helpers import close_agents, connect_agent, running_hub
from synapse_channel.cli_approvals import _emit_approval, add_parsers
from synapse_channel.core.approvals import (
    APPROVAL_NOTE_KIND,
    format_approval_note,
    run_approval_report,
)
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers()
    add_parsers(sub)
    return parser


def _seed(db: Path, *notes: tuple[str, str, str, str]) -> None:
    store = EventStore(db)
    for author, subject, state, reason in notes:
        store.append(
            EventKind.LEDGER_PROGRESS,
            {
                "author": author,
                "kind": APPROVAL_NOTE_KIND,
                "task_id": subject,
                "text": format_approval_note(subject=subject, state=state, reason=reason),
            },
            durable=True,
        )
    store.close()


# ---------- emit-side argument validation ----------


def test_request_rejects_bad_subject(capsys: pytest.CaptureFixture[str]) -> None:
    parser = _parser()
    args = parser.parse_args(["approval", "request", "--name", "dev", "--subject", "two words"])
    assert args.func(args) == 2
    assert "subject" in capsys.readouterr().err


def test_request_connect_failure_returns_1() -> None:
    parser = _parser()
    args = parser.parse_args(
        [
            "approval",
            "request",
            "--name",
            "dev",
            "--subject",
            "T1",
            "--uri",
            "ws://127.0.0.1:1",
            "--ready-timeout",
            "0.2",
        ]
    )
    assert args.func(args) == 1


def test_decide_requires_a_verdict() -> None:
    parser = _parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["approval", "decide", "--name", "ceo", "--subject", "T1"])


def test_decide_rejects_both_verdicts() -> None:
    parser = _parser()
    with pytest.raises(SystemExit):
        parser.parse_args(
            ["approval", "decide", "--name", "ceo", "--subject", "T1", "--approve", "--reject"]
        )


# ---------- status command ----------


def test_status_missing_db_returns_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    parser = _parser()
    args = parser.parse_args(["approval", "status", str(tmp_path / "nope.db")])
    assert args.func(args) == 2
    assert "missing event store" in capsys.readouterr().err


def test_status_human_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "hub.db"
    _seed(db, ("dev", "T1", "requested", ""), ("ceo", "T1", "approved", "ok"))
    parser = _parser()
    args = parser.parse_args(["approval", "status", str(db)])
    assert args.func(args) == 0
    out = capsys.readouterr().out
    assert "Approval gates" in out
    assert "T1: approved" in out


def test_status_json_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "hub.db"
    _seed(db, ("dev", "T1", "requested", ""))
    parser = _parser()
    args = parser.parse_args(["approval", "status", str(db), "--json"])
    assert args.func(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["statuses"][0]["current_state"] == "awaiting_approval"


def test_status_pending_filter(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "hub.db"
    _seed(db, ("dev", "T1", "requested", ""), ("ceo", "T2", "approved", ""))
    parser = _parser()
    args = parser.parse_args(["approval", "status", str(db), "--pending"])
    assert args.func(args) == 0
    out = capsys.readouterr().out
    assert "T1" in out
    assert "T2" not in out


def test_status_subject_filter_empty_match(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed(db, ("dev", "T1", "requested", ""))
    parser = _parser()
    args = parser.parse_args(["approval", "status", str(db), "--subject", "NOPE"])
    assert args.func(args) == 0
    assert "No matching approval subjects" in capsys.readouterr().out


# ---------- live e2e ----------


async def test_request_then_decide_e2e(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    store = EventStore(db)
    hub = SynapseHub(journal=store, hub_id="syn-test")
    parser = _parser()
    async with running_hub(hub) as (_hub, uri):
        request = parser.parse_args(
            ["approval", "request", "--name", "dev-rx", "--subject", "T1", "--uri", uri]
        )
        decide = parser.parse_args(
            [
                "approval",
                "decide",
                "--name",
                "ceo",
                "--subject",
                "T1",
                "--approve",
                "--reason",
                "ship it",
                "--uri",
                uri,
            ]
        )
        assert await asyncio.to_thread(request.func, request) == 0
        assert await asyncio.to_thread(decide.func, decide) == 0
        report = None
        for _ in range(150):
            try:
                report = run_approval_report(db)
            except ValueError:
                report = None
            if report is not None and report.statuses and not report.pending:
                break
            await asyncio.sleep(0.02)
    assert report is not None
    status = report.by_subject["T1"]
    assert status.current_state == "approved"
    assert status.requested_by == "dev"  # "-rx" stripped on send
    assert status.decided_by == "ceo"
    assert status.decision_reason == "ship it"


async def test_emit_approval_reports_name_conflict_instead_of_dropping(tmp_path: Path) -> None:
    # An approval emit whose name conflicts with a live identity must report it,
    # not write into a dying socket and lose the decision silently.
    async with running_hub(SynapseHub()) as (_hub, uri):
        holder = await connect_agent("dup", uri)
        try:
            rc = await _emit_approval(
                uri=uri,
                name="dup",
                subject="T1",
                state="requested",
                reason="",
                token=None,
                ready_timeout=2.0,
            )
        finally:
            await close_agents(holder)
    assert rc == 1
