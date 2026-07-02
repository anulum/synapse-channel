# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — trust-graph CLI command regressions

from __future__ import annotations

import json
from pathlib import Path

import pytest

from synapse_channel.cli import build_parser, main
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.state import TaskClaim
from test_trust_graph import _seed_store


def _run(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    code = main(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_human_report_over_a_seeded_store(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "events.db"
    _seed_store(db)
    code, out, err = _run(["trust-graph", str(db), "--as-of", "100.0"], capsys)
    assert code == 0
    assert err == ""
    assert out.startswith("Trust graph: evidence with event-log provenance, not scores")
    assert "conflict_pair" in out


def test_json_report_parses_and_carries_the_boundary(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "events.db"
    _seed_store(db)
    code, out, _ = _run(["trust-graph", str(db), "--as-of", "100.0", "--json"], capsys)
    assert code == 0
    payload = json.loads(out)
    assert payload["note"] == "evidence graph, not scores"
    assert payload["edges"]
    assert payload["nodes"]


def test_dot_report_is_a_digraph(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "events.db"
    _seed_store(db)
    code, out, _ = _run(["trust-graph", str(db), "--as-of", "100.0", "--dot"], capsys)
    assert code == 0
    assert out.startswith("digraph trust_graph {")
    assert "shape=ellipse" in out
    assert "dir=none, style=dashed" in out


def test_agent_task_and_since_filters_are_wired(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "events.db"
    _seed_store(db)
    code, out, _ = _run(
        ["trust-graph", str(db), "--as-of", "100.0", "--agent", "gamma", "--json"], capsys
    )
    assert code == 0
    agent_edges = json.loads(out)["edges"]
    assert {edge["kind"] for edge in agent_edges} == {"broken_handoff_candidate", "stale_claim"}
    code, out, _ = _run(
        ["trust-graph", str(db), "--as-of", "100.0", "--task", "OVERLAP-A", "--json"], capsys
    )
    assert code == 0
    task_edges = json.loads(out)["edges"]
    assert {edge["kind"] for edge in task_edges} == {"conflict_pair"}
    code, out, _ = _run(
        ["trust-graph", str(db), "--as-of", "100.0", "--since", "1e9", "--json"], capsys
    )
    assert code == 0
    assert json.loads(out)["edges"] == []


def test_missing_store_fails_with_exit_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, err = _run(["trust-graph", str(tmp_path / "absent.db")], capsys)
    assert code == 2
    assert out == ""
    assert "missing event store" in err


def test_json_and_dot_are_mutually_exclusive(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    _seed_store(db)
    with pytest.raises(SystemExit) as excinfo:
        main(["trust-graph", str(db), "--json", "--dot"])
    assert excinfo.value.code == 2


def test_parser_flags_and_defaults() -> None:
    parser = build_parser(command="trust-graph")
    args = parser.parse_args(["trust-graph", "events.db"])
    assert args.db == "events.db"
    assert args.agent is None
    assert args.task is None
    assert args.since is None
    assert args.as_of is None
    assert args.json is False
    assert args.dot is False


def test_stale_only_store_reports_without_receipts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A log holding only coordination events (no ledger tasks or receipts)
    # still projects: the graph must not require the capability layer.
    db = tmp_path / "events.db"
    store = EventStore(db)
    store.append(
        EventKind.CLAIM,
        TaskClaim(
            task_id="ONLY",
            owner="omega",
            note="work",
            claimed_at=1.0,
            lease_expires_at=2.0,
            status="claimed",
            data_ref="",
            worktree="repo",
            paths=("a.py",),
            epoch=1,
            checkpoint="",
        ).as_dict(),
        ts=1.0,
        durable=True,
    )
    store.close()
    code, out, _ = _run(["trust-graph", str(db), "--as-of", "100.0", "--json"], capsys)
    assert code == 0
    edges = json.loads(out)["edges"]
    assert {edge["kind"] for edge in edges} == {"stale_claim"}
