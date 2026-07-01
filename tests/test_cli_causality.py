# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — coordination-causality CLI regressions

from __future__ import annotations

import json
from pathlib import Path

import pytest

from synapse_channel import cli
from synapse_channel.core.causality import DEFAULT_MAX_GRAPH_NODES
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore

REPO_ROOT = Path(__file__).resolve().parents[1]


def _seed(path: Path) -> None:
    """B done & released; A depends on B and is claimed after; C contends A's paths."""
    store = EventStore(path)
    store.append(EventKind.LEDGER_TASK, {"task_id": "B", "title": "B", "depends_on": []}, ts=1.0)
    store.append(
        EventKind.CLAIM,
        {
            "task_id": "B",
            "owner": "alice",
            "status": "claimed",
            "paths": ["src/x"],
            "worktree": "w",
        },
        ts=2.0,
    )
    store.append(
        EventKind.TASK_UPDATE,
        {"task_id": "B", "owner": "alice", "status": "done", "paths": ["src/x"], "worktree": "w"},
        ts=3.0,
    )
    store.append(EventKind.RELEASE, {"task_id": "B"}, ts=4.0)
    store.append(EventKind.LEDGER_TASK, {"task_id": "A", "title": "A", "depends_on": ["B"]}, ts=5.0)
    store.append(
        EventKind.CLAIM,
        {"task_id": "A", "owner": "bob", "status": "claimed", "paths": ["src/y"], "worktree": "w"},
        ts=6.0,
    )
    store.append(EventKind.RELEASE, {"task_id": "A"}, ts=7.0)
    store.append(
        EventKind.CLAIM,
        {
            "task_id": "C",
            "owner": "carol",
            "status": "claimed",
            "paths": ["src/y"],
            "worktree": "w",
        },
        ts=8.0,
    )
    store.close()


def test_parser_wires_causality_command() -> None:
    args = cli.build_parser().parse_args(["causality", "effects", "hub.db", "6", "--json"])

    assert args.command == "causality"
    assert args.direction == "effects"
    assert args.db == "hub.db"
    assert args.seq == 6
    assert args.json is True


def test_cli_causality_causes_markdown(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "hub.db"
    _seed(db)

    exit_code = cli.main(["causality", "causes", str(db), "6"])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "# Causality (causes): seq 6" in out
    assert "[dependency]" in out


def test_cli_causality_effects_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "hub.db"
    _seed(db)

    exit_code = cli.main(["causality", "effects", str(db), "4", "--json"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["direction"] == "effects"
    assert payload["present"] is True
    assert [node["seq"] for node in payload["transitive"]] == [6, 7, 8]


def test_cli_causality_counterfactual_markdown(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed(db)

    exit_code = cli.main(["causality", "counterfactual", str(db), "2"])

    assert exit_code == 0
    assert "Loses recorded support" in capsys.readouterr().out


def test_cli_causality_absent_sequence_exits_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed(db)

    exit_code = cli.main(["causality", "causes", str(db), "999"])

    assert exit_code == 1
    assert "No coordination event at seq 999" in capsys.readouterr().out


def test_cli_causality_missing_store_exits_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = cli.main(["causality", "causes", str(tmp_path / "absent.db"), "1"])

    assert exit_code == 2
    assert "missing event store" in capsys.readouterr().err


def test_cli_causality_rejects_unknown_direction(tmp_path: Path) -> None:
    # argparse choices guard the direction before the handler runs.
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["causality", "sideways", "hub.db", "1"])


def test_docs_wire_causality_command() -> None:
    combined = "\n".join(
        [
            (REPO_ROOT / "README.md").read_text(encoding="utf-8"),
            (REPO_ROOT / "docs" / "cli.md").read_text(encoding="utf-8"),
        ]
    )

    assert "synapse causality" in combined
    assert "counterfactual" in combined


def test_parser_defaults_the_node_ceiling() -> None:
    args = cli.build_parser().parse_args(["causality", "effects", "hub.db", "6"])
    assert args.max_nodes == DEFAULT_MAX_GRAPH_NODES


def test_cli_causality_over_the_node_ceiling_exits_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An over-ceiling log errors with the compact remedy instead of loading."""
    db = tmp_path / "hub.db"
    _seed(db)

    exit_code = cli.main(["causality", "effects", str(db), "6", "--max-nodes", "1"])

    assert exit_code == 2
    err = capsys.readouterr().err
    assert "would exceed 1 coordination events" in err
    assert "synapse compact" in err


def test_cli_causality_zero_lifts_the_node_ceiling(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed(db)

    assert cli.main(["causality", "effects", str(db), "6", "--max-nodes", "0"]) == 0
    assert "# Causality (effects): seq 6" in capsys.readouterr().out
