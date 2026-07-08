# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — coordination-causality CLI regressions

"""Federated causality tests for the causality CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from causality_helpers import _federated_pair, _seed
from synapse_channel import cli


def test_parser_accepts_repeated_peers_and_a_primary_hub_id() -> None:
    args = cli.build_parser().parse_args(
        [
            "causality",
            "causes",
            "hub.db",
            "peer-a:6",
            "--peer",
            "peer-a=a.db",
            "--peer",
            "peer-b=b.db",
            "--hub-id",
            "primary",
        ]
    )

    assert args.peer == ["peer-a=a.db", "peer-b=b.db"]
    assert args.hub_id == "primary"
    assert args.seq == "peer-a:6"


def test_cli_federated_causes_cross_the_hub_boundary(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db, peer = _federated_pair(tmp_path)

    exit_code = cli.main(["causality", "causes", str(db), "peer:2", "--peer", f"peer={peer}"])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "# Federated causality (causes): peer:2" in out
    assert "- Hubs: hub, peer" in out
    assert "[federation:dependency] hub:4" in out


def test_cli_federated_json_carries_relation_and_basis(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db, peer = _federated_pair(tmp_path)

    exit_code = cli.main(
        ["causality", "causes", str(db), "peer:2", "--peer", f"peer={peer}", "--json"]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["hubs"] == ["hub", "peer"]
    federated = [link for link in payload["direct"] if link["relation"] == "federation"]
    assert federated
    assert federated[0]["basis"] == "dependency"
    assert federated[0]["src"] == {"hub_id": "hub", "seq": 4}


def test_cli_federated_dot_renders_clusters_and_coloured_federation_edges(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db, peer = _federated_pair(tmp_path)

    exit_code = cli.main(
        ["causality", "causes", str(db), "peer:2", "--peer", f"peer={peer}", "--dot"]
    )

    assert exit_code == 0
    out = capsys.readouterr().out
    assert out.startswith("digraph federated_causality {")
    assert 'label="hub";' in out
    assert 'label="peer";' in out
    assert 'label="federation:dependency", color=blue];' in out


def test_cli_dot_requires_a_federated_query(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed(db)

    exit_code = cli.main(["causality", "causes", str(db), "4", "--dot"])

    assert exit_code == 2
    assert "it requires --peer" in capsys.readouterr().err


def test_cli_json_and_dot_are_mutually_exclusive(tmp_path: Path) -> None:
    db, peer = _federated_pair(tmp_path)

    with pytest.raises(SystemExit) as excinfo:
        cli.main(
            ["causality", "causes", str(db), "peer:2", "--peer", f"peer={peer}", "--json", "--dot"]
        )
    assert excinfo.value.code == 2


def test_cli_federated_plain_seq_resolves_to_the_primary_hub(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db, peer = _federated_pair(tmp_path)

    exit_code = cli.main(["causality", "effects", str(db), "4", "--peer", f"peer={peer}"])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "# Federated causality (effects): hub:4" in out
    assert "peer:2" in out


def test_cli_federated_hub_id_overrides_the_db_stem(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db, peer = _federated_pair(tmp_path)

    exit_code = cli.main(
        [
            "causality",
            "causes",
            str(db),
            "main:4",
            "--peer",
            f"peer={peer}",
            "--hub-id",
            "main",
        ]
    )

    assert exit_code == 0
    assert "# Federated causality (causes): main:4" in capsys.readouterr().out


def test_cli_hub_id_without_peer_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed(db)

    exit_code = cli.main(["causality", "causes", str(db), "4", "--hub-id", "main"])

    assert exit_code == 2
    assert "requires --peer" in capsys.readouterr().err


def test_cli_federated_malformed_peer_spec_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed(db)

    exit_code = cli.main(["causality", "causes", str(db), "4", "--peer", "no-equals-here"])

    assert exit_code == 2
    assert "expected HUB=PATH" in capsys.readouterr().err


def test_cli_federated_duplicate_hub_id_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db, peer = _federated_pair(tmp_path)

    exit_code = cli.main(["causality", "causes", str(db), "4", "--peer", f"hub={peer}"])

    assert exit_code == 2
    assert "duplicate hub id 'hub'" in capsys.readouterr().err


def test_cli_single_hub_non_integer_seq_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed(db)

    exit_code = cli.main(["causality", "causes", str(db), "abc"])

    assert exit_code == 2
    assert "invalid SEQ 'abc'" in capsys.readouterr().err


def test_cli_federated_malformed_reference_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db, peer = _federated_pair(tmp_path)

    exit_code = cli.main(["causality", "causes", str(db), "peer:abc", "--peer", f"peer={peer}"])

    assert exit_code == 2
    assert "expected SEQ or HUB:SEQ" in capsys.readouterr().err


def test_cli_federated_absent_reference_exits_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db, peer = _federated_pair(tmp_path)

    exit_code = cli.main(["causality", "causes", str(db), "peer:999", "--peer", f"peer={peer}"])

    assert exit_code == 1
    assert "No coordination event at peer:999" in capsys.readouterr().out


def test_cli_federated_missing_peer_store_exits_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed(db)

    exit_code = cli.main(
        ["causality", "causes", str(db), "4", "--peer", f"peer={tmp_path / 'absent.db'}"]
    )

    assert exit_code == 2
    assert "missing event store for hub 'peer'" in capsys.readouterr().err
