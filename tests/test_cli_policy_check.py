# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the advisory policy-check CLI

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pytest

from synapse_channel import cli_policy_check
from synapse_channel.core.merkle import root_to_json, run_root
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.receipt_signing import (
    generate_receipt_signing_key,
    load_receipt_signing_key,
    sign_merkle_commitment,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers()
    cli_policy_check.add_parsers(sub)
    return parser


def _write(path: Path, payload: dict[str, Any]) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _policy(tmp_path: Path, rules: dict[str, Any], mode: str = "advisory") -> Path:
    return _write(tmp_path / "policy.json", {"version": 1, "mode": mode, "rules": rules})


def _receipt(tmp_path: Path, **fields: Any) -> Path:
    base = {"task_id": "T1", "owner": "alice", "evidence": [], "changed_files": []}
    base.update(fields)
    return _write(tmp_path / "receipt.json", base)


def _run(argv: list[str]) -> int:
    args = _parser().parse_args(argv)
    return cli_policy_check._cmd_policy_check(args)


def test_text_report_passes_and_returns_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    policy = _policy(tmp_path, {"required_tests": {"commands": ["pytest"]}})
    receipt = _receipt(tmp_path, evidence=["pytest -q passed"])
    code = _run(["policy-check", "T1", "--policy", str(policy), "--receipt-json", str(receipt)])
    out = capsys.readouterr().out
    assert code == 0
    assert "-> pass" in out
    assert "required_tests" in out


def test_json_report_is_structured(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    policy = _policy(tmp_path, {"no_merge_without_receipt": {"required": True}})
    receipt = _receipt(tmp_path)
    code = _run(
        [
            "policy-check",
            "TASK-7",
            "--policy",
            str(policy),
            "--receipt-json",
            str(receipt),
            "--json",
        ]
    )
    report = json.loads(capsys.readouterr().out)
    assert code == 0  # advisory: never blocks without --enforce
    assert report["subject"] == "TASK-7"
    assert report["overall"] == "fail"
    assert report["blocked"] is False
    assert report["decisions"][0]["rule"] == "no_merge_without_receipt"


def test_enforce_mode_blocks_and_returns_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    policy = _policy(tmp_path, {"no_merge_without_receipt": {"required": True}}, mode="enforcement")
    receipt = _receipt(tmp_path)
    code = _run(
        ["policy-check", "T1", "--policy", str(policy), "--receipt-json", str(receipt), "--enforce"]
    )
    out = capsys.readouterr().out
    assert code == 1
    assert "BLOCKED" in out


def test_enforce_without_failures_returns_zero(tmp_path: Path) -> None:
    policy = _policy(tmp_path, {"no_merge_without_receipt": {"required": True}}, mode="enforcement")
    receipt = _receipt(tmp_path, evidence=["pytest passed"])
    code = _run(
        ["policy-check", "T1", "--policy", str(policy), "--receipt-json", str(receipt), "--enforce"]
    )
    assert code == 0


def test_warn_next_action_is_shown(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    policy = _policy(tmp_path, {"evidence_freshness": {"max_age_seconds": 10}})
    receipt = _receipt(tmp_path, freshness_seconds=99.0)
    _run(["policy-check", "T1", "--policy", str(policy), "--receipt-json", str(receipt)])
    out = capsys.readouterr().out
    assert "next:" in out


def test_missing_policy_file_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    receipt = _receipt(tmp_path)
    code = _run(
        [
            "policy-check",
            "T1",
            "--policy",
            str(tmp_path / "nope.json"),
            "--receipt-json",
            str(receipt),
        ]
    )
    assert code == 2
    assert "policy-check error" in capsys.readouterr().out


def test_missing_receipt_file_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    policy = _policy(tmp_path, {})
    code = _run(
        ["policy-check", "T1", "--policy", str(policy), "--receipt-json", str(tmp_path / "no.json")]
    )
    assert code == 2
    assert "receipt file does not exist" in capsys.readouterr().out


def test_invalid_receipt_json_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    policy = _policy(tmp_path, {})
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    code = _run(["policy-check", "T1", "--policy", str(policy), "--receipt-json", str(bad)])
    assert code == 2
    assert "invalid receipt JSON" in capsys.readouterr().out


def test_non_object_receipt_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    policy = _policy(tmp_path, {})
    arr = tmp_path / "arr.json"
    arr.write_text("[1, 2, 3]", encoding="utf-8")
    code = _run(["policy-check", "T1", "--policy", str(policy), "--receipt-json", str(arr)])
    assert code == 2
    assert "must be an object" in capsys.readouterr().out


def _seeded_store(path: Path, count: int = 4) -> None:
    store = EventStore(path)
    for i in range(1, count + 1):
        store.append("claim", {"task_id": f"T{i}"}, ts=float(i))
    store.close()


def _committed_receipt(tmp_path: Path, db: Path) -> Path:
    return _receipt(
        tmp_path,
        evidence=["pytest -q passed"],
        verification={"merkle": root_to_json(run_root(db))},
    )


def test_merkle_db_adds_a_passing_commitment_decision(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    policy = _policy(tmp_path, {"required_tests": {"commands": ["pytest"]}})
    db = tmp_path / "hub.db"
    _seeded_store(db)
    receipt = _committed_receipt(tmp_path, db)
    code = _run(
        [
            "policy-check",
            "T1",
            "--policy",
            str(policy),
            "--receipt-json",
            str(receipt),
            "--merkle-db",
            str(db),
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "merkle_commitment: coordination log through seq 4 still matches" in out


def test_merkle_db_fails_the_decision_when_the_prefix_changed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    policy = _policy(tmp_path, {"required_tests": {"commands": ["pytest"]}})
    db = tmp_path / "hub.db"
    _seeded_store(db)
    receipt = _committed_receipt(tmp_path, db)
    tampered = tmp_path / "tampered.db"
    store = EventStore(tampered)
    for i in range(1, 5):
        store.append("claim", {"task_id": f"X{i}"}, ts=float(i))
    store.close()
    code = _run(
        [
            "policy-check",
            "T1",
            "--policy",
            str(policy),
            "--receipt-json",
            str(receipt),
            "--merkle-db",
            str(tampered),
        ]
    )
    out = capsys.readouterr().out
    assert code == 0  # advisory mode reports, it does not gate
    assert "✗ merkle_commitment: commitment mismatch" in out
    assert "treat the coordination log (or the receipt) as tampered" in out


def test_merkle_db_marks_a_receipt_without_a_commitment(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    policy = _policy(tmp_path, {"required_tests": {"commands": ["pytest"]}})
    db = tmp_path / "hub.db"
    _seeded_store(db)
    receipt = _receipt(tmp_path, evidence=["pytest -q passed"])
    code = _run(
        [
            "policy-check",
            "T1",
            "--policy",
            str(policy),
            "--receipt-json",
            str(receipt),
            "--merkle-db",
            str(db),
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "merkle_commitment: receipt carries no coordination-log commitment" in out


def test_merkle_db_enforce_blocks_on_a_failed_commitment(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An enforcement policy gates the release on a tampered commitment."""
    policy = _policy(tmp_path, {"required_tests": {"commands": ["pytest"]}}, mode="enforcement")
    db = tmp_path / "hub.db"
    _seeded_store(db)
    receipt = _committed_receipt(tmp_path, db)
    shorter = tmp_path / "shorter.db"
    _seeded_store(shorter, count=2)
    code = _run(
        [
            "policy-check",
            "T1",
            "--policy",
            str(policy),
            "--receipt-json",
            str(receipt),
            "--merkle-db",
            str(shorter),
            "--enforce",
        ]
    )
    out = capsys.readouterr().out
    assert code == 1
    assert "BLOCKED" in out


def test_merkle_db_missing_store_is_a_policy_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    policy = _policy(tmp_path, {"required_tests": {"commands": ["pytest"]}})
    db = tmp_path / "hub.db"
    _seeded_store(db)
    receipt = _committed_receipt(tmp_path, db)
    code = _run(
        [
            "policy-check",
            "T1",
            "--policy",
            str(policy),
            "--receipt-json",
            str(receipt),
            "--merkle-db",
            str(tmp_path / "absent.db"),
        ]
    )
    assert code == 2
    assert "missing event store" in capsys.readouterr().out


# --- merkle_signature decision ------------------------------------------------------


def _keypair(tmp_path: Path) -> tuple[Path, Path]:
    """Generate a receipt-signing keypair; return (private, public) paths."""
    key_path = tmp_path / "hub-receipt.key"
    generate_receipt_signing_key(key_path)
    return key_path, tmp_path / "hub-receipt.key.pub"


def _signed_receipt_file(tmp_path: Path, db: Path, key_path: Path) -> Path:
    merkle = root_to_json(run_root(db))
    key = load_receipt_signing_key(key_path)
    return _receipt(
        tmp_path,
        evidence=["pytest -q passed"],
        verification={
            "merkle": merkle,
            "merkle_signature": sign_merkle_commitment(merkle, key=key),
        },
    )


def test_trusted_signing_key_adds_a_passing_signature_decision(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    policy = _policy(tmp_path, {"required_tests": {"commands": ["pytest"]}})
    db = tmp_path / "hub.db"
    _seeded_store(db)
    key_path, pub_path = _keypair(tmp_path)
    receipt = _signed_receipt_file(tmp_path, db, key_path)
    code = _run(
        [
            "policy-check",
            "T1",
            "--policy",
            str(policy),
            "--receipt-json",
            str(receipt),
            "--merkle-db",
            str(db),
            "--trusted-signing-key",
            str(pub_path),
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "✓ merkle_commitment" in out
    assert "✓ merkle_signature: hub key" in out
    assert "evidence_verdict: VALID_LEGACY" in out


def test_json_report_distinguishes_a_verified_legacy_receipt(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    policy = _policy(tmp_path, {"required_tests": {"commands": ["pytest"]}})
    db = tmp_path / "hub.db"
    _seeded_store(db)
    key_path, pub_path = _keypair(tmp_path)
    receipt = _signed_receipt_file(tmp_path, db, key_path)
    code = _run(
        [
            "policy-check",
            "T1",
            "--policy",
            str(policy),
            "--receipt-json",
            str(receipt),
            "--trusted-signing-key",
            str(pub_path),
            "--json",
        ]
    )
    report = json.loads(capsys.readouterr().out)
    assert code == 0
    assert report["evidence_verdict"] == {
        "verdict": "VALID_LEGACY",
        "receipt_id": "",
        "key_id": load_receipt_signing_key(key_path).key_id,
        "reasons": [
            f"hub key {load_receipt_signing_key(key_path).key_id} "
            "attested this coordination-log commitment"
        ],
    }


def test_signature_decision_fails_on_a_tampered_commitment(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    policy = _policy(tmp_path, {"required_tests": {"commands": ["pytest"]}})
    db = tmp_path / "hub.db"
    _seeded_store(db)
    key_path, pub_path = _keypair(tmp_path)
    merkle = root_to_json(run_root(db))
    envelope = sign_merkle_commitment(merkle, key=load_receipt_signing_key(key_path))
    merkle["root"] = "0" * 64
    receipt = _receipt(
        tmp_path,
        evidence=["pytest -q passed"],
        verification={"merkle": merkle, "merkle_signature": envelope},
    )
    code = _run(
        [
            "policy-check",
            "T1",
            "--policy",
            str(policy),
            "--receipt-json",
            str(receipt),
            "--trusted-signing-key",
            str(pub_path),
        ]
    )
    out = capsys.readouterr().out
    assert code == 0  # advisory mode reports, never blocks
    assert "✗ merkle_signature: commitment signature does not verify" in out
    assert "do not trust this receipt's provenance" in out
    assert "evidence_verdict: INVALID_SIGNATURE" in out


def test_signature_decision_marks_an_unsigned_receipt(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    policy = _policy(tmp_path, {"required_tests": {"commands": ["pytest"]}})
    db = tmp_path / "hub.db"
    _seeded_store(db)
    _, pub_path = _keypair(tmp_path)
    receipt = _receipt(
        tmp_path,
        evidence=["pytest -q passed"],
        epistemic_status="supported",
        verification={"merkle": root_to_json(run_root(db))},
    )
    code = _run(
        [
            "policy-check",
            "T1",
            "--policy",
            str(policy),
            "--receipt-json",
            str(receipt),
            "--trusted-signing-key",
            str(pub_path),
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "merkle_signature: receipt carries no commitment signature" in out
    assert "evidence_verdict: MALFORMED" in out


def test_an_unreadable_trusted_key_is_a_configuration_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    policy = _policy(tmp_path, {"required_tests": {"commands": ["pytest"]}})
    receipt = _receipt(tmp_path, evidence=["pytest -q passed"])
    code = _run(
        [
            "policy-check",
            "T1",
            "--policy",
            str(policy),
            "--receipt-json",
            str(receipt),
            "--trusted-signing-key",
            str(tmp_path / "absent.pub"),
        ]
    )
    assert code == 2
    assert "cannot read receipt verification key" in capsys.readouterr().out


def test_signature_enforce_blocks_on_an_untrusted_signer(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An enforcement policy gates the release on unverifiable provenance."""
    policy = _policy(tmp_path, {"required_tests": {"commands": ["pytest"]}}, mode="enforcement")
    db = tmp_path / "hub.db"
    _seeded_store(db)
    key_path, _ = _keypair(tmp_path)
    other_dir = tmp_path / "other"
    other_dir.mkdir()
    _, foreign_pub = _keypair(other_dir)
    receipt = _signed_receipt_file(tmp_path, db, key_path)
    code = _run(
        [
            "policy-check",
            "T1",
            "--policy",
            str(policy),
            "--receipt-json",
            str(receipt),
            "--trusted-signing-key",
            str(foreign_pub),
            "--enforce",
        ]
    )
    out = capsys.readouterr().out
    assert code == 1
    assert "✗ merkle_signature: commitment signed by an untrusted key" in out
    assert "evidence_verdict: UNKNOWN_KEY" in out
    assert "BLOCKED" in out
