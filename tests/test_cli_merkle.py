# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — Merkle-commitment CLI regressions

from __future__ import annotations

import json
from pathlib import Path

import pytest

from synapse_channel import cli
from synapse_channel.core.journal import EventKind
from synapse_channel.core.merkle import proof_to_json, run_proof, run_root
from synapse_channel.core.persistence import EventStore

REPO_ROOT = Path(__file__).resolve().parents[1]


def _seed(path: Path, count: int = 7) -> None:
    store = EventStore(path)
    for seq in range(1, count + 1):
        store.append(EventKind.CLAIM, {"task_id": f"T{seq}", "owner": "alice"}, ts=float(seq))
    store.close()


def test_parser_wires_merkle_actions() -> None:
    args = cli.build_parser().parse_args(["merkle", "root", "hub.db", "--through", "5", "--json"])
    assert args.command == "merkle"
    assert args.merkle_command == "root"
    assert args.through == 5
    assert args.json is True


def test_merkle_requires_an_action() -> None:
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["merkle"])


# --- root --------------------------------------------------------------------


def test_cli_root_markdown(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "hub.db"
    _seed(db)
    assert cli.main(["merkle", "root", str(db)]) == 0
    assert "# Merkle root" in capsys.readouterr().out


def test_cli_root_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "hub.db"
    _seed(db)
    assert cli.main(["merkle", "root", str(db), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["tree_size"] == 7


def test_cli_root_expect_match(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "hub.db"
    _seed(db)
    root = run_root(db).root
    assert cli.main(["merkle", "root", str(db), "--expect", root]) == 0
    assert "root matches" in capsys.readouterr().err


def test_cli_root_expect_mismatch(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "hub.db"
    _seed(db)
    assert cli.main(["merkle", "root", str(db), "--expect", "deadbeef"]) == 1
    assert "root mismatch" in capsys.readouterr().err


def test_cli_root_missing_store(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["merkle", "root", str(tmp_path / "absent.db")]) == 2
    assert "missing event store" in capsys.readouterr().err


# --- prove -------------------------------------------------------------------


def test_cli_prove_markdown(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "hub.db"
    _seed(db)
    assert cli.main(["merkle", "prove", str(db), "3"]) == 0
    assert "# Inclusion proof: seq 3" in capsys.readouterr().out


def test_cli_prove_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "hub.db"
    _seed(db)
    assert cli.main(["merkle", "prove", str(db), "3", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["seq"] == 3
    assert payload["index"] == 2


def test_cli_prove_absent_seq(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "hub.db"
    _seed(db)
    assert cli.main(["merkle", "prove", str(db), "999"]) == 1
    assert "no event at seq 999" in capsys.readouterr().err


def test_cli_prove_missing_store(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["merkle", "prove", str(tmp_path / "absent.db"), "1"]) == 2
    assert "missing event store" in capsys.readouterr().err


# --- verify (offline, no store) ----------------------------------------------


def _write_proof(tmp_path: Path, db: Path, seq: int) -> Path:
    proof = run_proof(db, seq)
    assert proof is not None
    out = tmp_path / f"proof-{seq}.json"
    out.write_text(json.dumps(proof_to_json(proof)), encoding="utf-8")
    return out


def test_cli_verify_valid(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "hub.db"
    _seed(db)
    proof_file = _write_proof(tmp_path, db, 3)
    assert cli.main(["merkle", "verify", str(proof_file)]) == 0
    assert "proof valid" in capsys.readouterr().err


def test_cli_verify_with_expected_root(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "hub.db"
    _seed(db)
    root = run_root(db).root
    proof_file = _write_proof(tmp_path, db, 3)
    assert cli.main(["merkle", "verify", str(proof_file), "--expect", root]) == 0


def test_cli_verify_expected_root_mismatch(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed(db)
    proof_file = _write_proof(tmp_path, db, 3)
    assert cli.main(["merkle", "verify", str(proof_file), "--expect", "deadbeef"]) == 1
    assert "root mismatch" in capsys.readouterr().err


def test_cli_verify_tampered_proof(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "hub.db"
    _seed(db)
    proof = run_proof(db, 3)
    assert proof is not None
    data = proof_to_json(proof)
    data["leaf"] = "00" * 32
    proof_file = tmp_path / "bad.json"
    proof_file.write_text(json.dumps(data), encoding="utf-8")
    assert cli.main(["merkle", "verify", str(proof_file)]) == 1
    assert "does not reconstruct" in capsys.readouterr().err


def test_cli_verify_missing_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["merkle", "verify", str(tmp_path / "absent.json")]) == 2
    assert "missing proof file" in capsys.readouterr().err


def test_cli_verify_malformed_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json", encoding="utf-8")
    assert cli.main(["merkle", "verify", str(bad)]) == 2
    assert "unreadable proof" in capsys.readouterr().err


# --- documentation wiring ----------------------------------------------------


def test_docs_wire_merkle_command() -> None:
    combined = "\n".join(
        [
            (REPO_ROOT / "README.md").read_text(encoding="utf-8"),
            (REPO_ROOT / "docs" / "cli.md").read_text(encoding="utf-8"),
        ]
    )
    assert "synapse merkle" in combined
    assert "merkle prove" in combined
