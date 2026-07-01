# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for verified release receipts
"""Verified release receipt core tests."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

from synapse_channel.core.merkle import run_root
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.release_verification import (
    build_verified_release_receipt,
    check_receipt_merkle_commitment,
    collect_git_state,
)


def test_build_verified_release_receipt_runs_commands_and_hashes_artifacts(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "artifact.txt"
    artifact.write_text("artifact-body\n", encoding="utf-8")
    receipt = build_verified_release_receipt(
        task_id="VERIFY",
        owner="SYNAPSE-CHANNEL/codex-main",
        commands=[
            [
                sys.executable,
                "-c",
                "import sys; print('ok'); print('warn', file=sys.stderr)",
            ]
        ],
        artifacts=[artifact],
        changed_files=["src/synapse_channel/core/release_verification.py"],
        git_head="abc123",
        git_tree="def456",
        timestamp=123.5,
        signature="review-signature",
    )

    stdout_digest = hashlib.sha256(b"ok\n").hexdigest()
    stderr_digest = hashlib.sha256(b"warn\n").hexdigest()
    artifact_digest = hashlib.sha256(b"artifact-body\n").hexdigest()
    verification = receipt["verification"]

    assert receipt["task_id"] == "VERIFY"
    assert receipt["owner"] == "SYNAPSE-CHANNEL/codex-main"
    assert receipt["known_failures"] == []
    assert receipt["changed_files"] == ["src/synapse_channel/core/release_verification.py"]
    assert receipt["freshness_seconds"] == 0.0
    assert receipt["epistemic_status"] == "supported"
    assert receipt["confidence"] == "observed"
    assert verification["git_head"] == "abc123"
    assert verification["git_tree"] == "def456"
    assert verification["timestamp"] == 123.5
    assert verification["signature"] == "review-signature"
    assert verification["commands"] == [
        {
            "argv": [
                sys.executable,
                "-c",
                "import sys; print('ok'); print('warn', file=sys.stderr)",
            ],
            "exit_code": 0,
            "stdout_sha256": stdout_digest,
            "stderr_sha256": stderr_digest,
        }
    ]
    assert verification["artifacts"] == [
        {
            "path": str(artifact),
            "sha256": artifact_digest,
            "size_bytes": len(b"artifact-body\n"),
        }
    ]
    assert any(stdout_digest in line for line in receipt["evidence"])
    assert receipt["artifacts"] == [f"{artifact} sha256={artifact_digest} size=14"]


def test_build_verified_release_receipt_records_failed_command_as_known_failure(
    tmp_path: Path,
) -> None:
    receipt = build_verified_release_receipt(
        task_id="VERIFY",
        owner="SYNAPSE-CHANNEL/codex-main",
        commands=[[sys.executable, "-c", "import sys; print('bad'); sys.exit(7)"]],
        artifacts=[tmp_path / "missing.txt"],
        changed_files=[],
        git_head="abc123",
        git_tree="def456",
        timestamp=123.5,
    )

    assert receipt["epistemic_status"] == "degraded"
    assert receipt["known_failures"] == [
        f"verification command failed: {sys.executable} -c "
        "import sys; print('bad'); sys.exit(7) "
        "exit=7",
        f"artifact missing: {tmp_path / 'missing.txt'}",
    ]
    assert receipt["verification"]["commands"][0]["exit_code"] == 7


def test_collect_git_state_reports_head_tree_and_changed_files(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(
        ["git", "config", "user.email", "test@example.invalid"], cwd=tmp_path, check=True
    )
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=tmp_path, check=True)
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("before\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=tmp_path, check=True, stdout=subprocess.DEVNULL
    )
    tracked.write_text("after\n", encoding="utf-8")
    (tmp_path / "new.txt").write_text("new\n", encoding="utf-8")

    state = collect_git_state(tmp_path)

    assert len(state.head) == 40
    assert len(state.tree) == 40
    assert state.changed_files == ["new.txt", "tracked.txt"]


def test_verified_release_receipt_is_json_serialisable(tmp_path: Path) -> None:
    receipt = build_verified_release_receipt(
        task_id="VERIFY",
        owner="SYNAPSE-CHANNEL/codex-main",
        commands=[],
        artifacts=[],
        changed_files=[],
        git_head="abc123",
        git_tree="def456",
        timestamp=123.5,
    )

    assert json.loads(json.dumps(receipt, sort_keys=True))["verification"]["git_tree"] == "def456"


def _seeded_store(path: Path, count: int = 5) -> None:

    store = EventStore(path)
    for i in range(1, count + 1):
        store.append("claim", {"task_id": f"T{i}"}, ts=float(i))
    store.close()


def _receipt_with_commitment(db: Path) -> dict[str, object]:

    return dict(
        build_verified_release_receipt(
            task_id="T1",
            owner="alice",
            commands=[],
            artifacts=[],
            changed_files=[],
            git_head="",
            git_tree="",
            merkle=run_root(db),
        )
    )


def test_receipt_embeds_the_merkle_commitment(tmp_path: Path) -> None:
    """The commitment rides as machine detail and as a human evidence line."""

    db = tmp_path / "hub.db"
    _seeded_store(db)
    expected = run_root(db)
    receipt = _receipt_with_commitment(db)
    verification = receipt["verification"]
    assert isinstance(verification, dict)
    assert verification["merkle"] == {
        "root": expected.root,
        "tree_size": 5,
        "first_seq": 1,
        "last_seq": 5,
        "through_seq": 0,
    }
    evidence = receipt["evidence"]
    assert isinstance(evidence, list)
    assert any(f"merkle root: {expected.root} over 5" in line for line in evidence)


def test_receipt_without_merkle_carries_no_commitment(tmp_path: Path) -> None:
    receipt = build_verified_release_receipt(
        task_id="T1",
        owner="alice",
        commands=[],
        artifacts=[],
        changed_files=[],
        git_head="",
        git_tree="",
    )
    assert "merkle" not in receipt["verification"]


def test_commitment_check_passes_on_an_untouched_log(tmp_path: Path) -> None:

    db = tmp_path / "hub.db"
    _seeded_store(db)
    receipt = _receipt_with_commitment(db)
    check = check_receipt_merkle_commitment(receipt, db)
    assert check.status == "pass"
    assert check.recorded_root == check.recomputed_root


def test_commitment_check_passes_after_append_only_growth(tmp_path: Path) -> None:
    """Appending events after the committed prefix must not break the receipt."""
    from synapse_channel.core.release_verification import check_receipt_merkle_commitment

    db = tmp_path / "hub.db"
    _seeded_store(db)
    receipt = _receipt_with_commitment(db)
    store = EventStore(db)
    store.append("chat", {"n": 99}, ts=99.0)
    store.close()
    assert check_receipt_merkle_commitment(receipt, db).status == "pass"


def test_commitment_check_fails_when_the_committed_prefix_changed(tmp_path: Path) -> None:
    """A different log with the same shape reproduces a different root."""

    db = tmp_path / "hub.db"
    _seeded_store(db)
    receipt = _receipt_with_commitment(db)
    tampered = tmp_path / "tampered.db"
    store_events = [("claim", {"task_id": f"X{i}"}) for i in range(1, 6)]

    store = EventStore(tampered)
    for kind, payload in store_events:
        store.append(kind, payload, ts=1.0)
    store.close()
    check = check_receipt_merkle_commitment(receipt, tampered)
    assert check.status == "fail"
    assert "changed since the receipt" in check.reason


def test_commitment_check_fails_on_a_shorter_prefix(tmp_path: Path) -> None:

    db = tmp_path / "hub.db"
    _seeded_store(db)
    receipt = _receipt_with_commitment(db)
    shorter = tmp_path / "shorter.db"
    _seeded_store(shorter, count=3)
    check = check_receipt_merkle_commitment(receipt, shorter)
    assert check.status == "fail"
    assert "now holds 3" in check.reason


def test_commitment_check_is_not_applicable_without_a_commitment(tmp_path: Path) -> None:

    db = tmp_path / "hub.db"
    _seeded_store(db)
    check = check_receipt_merkle_commitment({"verification": {}}, db)
    assert check.status == "not_applicable"
    assert check_receipt_merkle_commitment({}, db).status == "not_applicable"


def test_commitment_check_fails_on_a_malformed_commitment(tmp_path: Path) -> None:

    db = tmp_path / "hub.db"
    _seeded_store(db)
    malformed = {"verification": {"merkle": {"root": "", "last_seq": "x"}}}
    check = check_receipt_merkle_commitment(malformed, db)
    assert check.status == "fail"
    assert "malformed" in check.reason
