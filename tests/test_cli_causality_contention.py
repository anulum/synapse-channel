# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — coordination-causality CLI regressions

"""Contention command tests for the causality CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from causality_helpers import _federated_pair, _seed, _seed_contention
from synapse_channel import cli


def test_cli_contention_reports_the_yielder_and_exits_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed_contention(db)

    exit_code = cli.main(["causality", "contention", str(db)])

    out = capsys.readouterr().out
    assert exit_code == 1
    assert "# Contention: 1 overlapping live claim pair(s)" in out
    assert "## C (carol) should yield to A (bob)" in out
    assert "advisory only: no claim is preempted" in out


def test_cli_contention_quiet_log_exits_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed(db)  # C is the only live claim; nothing overlaps

    exit_code = cli.main(["causality", "contention", str(db)])

    assert exit_code == 0
    assert "No live claims overlap" in capsys.readouterr().out


def test_cli_contention_json_carries_both_standings(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed_contention(db)

    exit_code = cli.main(["causality", "contention", str(db), "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert payload[0]["holder"]["task_id"] == "A"
    assert payload[0]["yielder"]["owner"] == "carol"
    assert "later claim" in payload[0]["reason"]


def test_cli_contention_missing_store_exits_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = cli.main(["causality", "contention", str(tmp_path / "absent.db")])
    assert exit_code == 2
    assert "missing event store" in capsys.readouterr().err


def test_cli_contention_honours_the_node_ceiling(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed_contention(db)

    exit_code = cli.main(["causality", "contention", str(db), "--max-nodes", "1"])

    assert exit_code == 2
    assert "would exceed 1 coordination events" in capsys.readouterr().err


def test_cli_sequence_directions_still_require_a_seq(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed(db)

    exit_code = cli.main(["causality", "causes", str(db)])

    assert exit_code == 2
    assert "requires an event SEQ" in capsys.readouterr().err


def test_cli_contention_refuses_peers(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db, peer = _federated_pair(tmp_path)

    exit_code = cli.main(["causality", "contention", str(db), "--peer", f"peer={peer}"])

    assert exit_code == 2
    assert "--peer is not supported" in capsys.readouterr().err
