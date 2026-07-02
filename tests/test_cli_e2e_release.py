# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
"""End-to-end journeys for evidence-backed release, the receipt governance path.

``verify-release`` builds a release receipt offline by running verification
commands and recording their evidence; ``policy-check`` evaluates that receipt
against a policy; and ``release`` applies it to the hub, but only for the claim's
own owner. A git-claim over a feature branch provides a persistent claim to
release.
"""

from __future__ import annotations

import json
from pathlib import Path

from cli_e2e_helpers import git_repo, git_run, isolated_hub, run_cli

_RECEIPT_KEYS = {"task_id", "owner", "verification", "evidence", "epistemic_status"}


def _claimed_repo(tmp_path: Path, hub_uri: str) -> Path:
    """A feature-branch repo whose ``edit-y`` claim is held on the hub by USER."""
    repo = git_repo(tmp_path / "repo")
    git_run(repo, "checkout", "-q", "-b", "feature/y")
    run_cli("git-init", "--name", "trial", uri=hub_uri, cwd=repo)
    claimed = run_cli(
        "git-claim", "--task-id", "edit-y", "--paths", "src/y.py", uri=hub_uri, cwd=repo
    )
    assert claimed.ok(), claimed.output
    return repo


def test_verify_release_builds_an_offline_receipt(tmp_path: Path) -> None:
    """``verify-release`` runs the checks and writes an evidence-bearing receipt."""
    receipt = tmp_path / "receipt.json"
    result = run_cli(
        "verify-release", "edit-y", "--name", "USER", "--run", "true", "--output", str(receipt)
    )
    assert result.ok(), result.output
    assert receipt.exists()
    data = json.loads(receipt.read_text(encoding="utf-8"))
    assert _RECEIPT_KEYS <= set(data)
    assert data["task_id"] == "edit-y"


def test_release_refuses_a_non_owner(tmp_path: Path) -> None:
    """``release`` refuses to release a claim owned by someone else."""
    with isolated_hub(tmp_path) as hub:
        _claimed_repo(tmp_path, hub.uri)
        refused = run_cli("release", "edit-y", "--name", "intruder", uri=hub.uri)
        assert refused.returncode == 1
        assert "not" in refused.output.lower()
        # The claim is still held after the refused release.
        assert "edit-y" in run_cli("state", uri=hub.uri).stdout


def test_release_with_a_receipt_clears_the_owner_claim(tmp_path: Path) -> None:
    """The claim owner releases with a receipt and the claim clears."""
    with isolated_hub(tmp_path) as hub:
        _claimed_repo(tmp_path, hub.uri)
        receipt = tmp_path / "receipt.json"
        built = run_cli(
            "verify-release", "edit-y", "--name", "USER", "--run", "true", "--output", str(receipt)
        )
        assert built.ok(), built.output

        released = run_cli(
            "release", "edit-y", "--name", "USER", "--receipt", str(receipt), uri=hub.uri
        )
        assert released.ok(), released.output
        assert "edit-y" not in run_cli("state", uri=hub.uri).stdout


def test_policy_check_evaluates_a_receipt(tmp_path: Path) -> None:
    """``policy-check <task>`` evaluates a receipt against a policy, advisory by default."""
    receipt = tmp_path / "receipt.json"
    run_cli("verify-release", "edit-y", "--name", "USER", "--run", "true", "--output", str(receipt))
    policy = tmp_path / "policy.json"
    # policy-check rules are a mapping (keyed by rule id), unlike acl shadow's list.
    policy.write_text(json.dumps({"version": 1, "mode": "advisory", "rules": {}}), encoding="utf-8")

    result = run_cli(
        "policy-check", "edit-y", "--policy", str(policy), "--receipt-json", str(receipt)
    )
    assert result.ok(), result.output
    assert "advisory" in result.stdout


def test_merkle_committed_receipt_round_trips_through_policy_check(tmp_path: Path) -> None:
    """The tamper-evidence loop: commit the log at release, re-verify it later.

    A hub writes real coordination events; ``verify-release --merkle-db`` binds
    the receipt to that exact log prefix; ``policy-check --merkle-db`` recomputes
    the prefix and passes while the log has only grown append-only.
    """
    with isolated_hub(tmp_path) as hub:
        run_cli("task", "declare", "BUILD", "--title", "build step", uri=hub.uri)
        run_cli("lock", "BUILD", "--paths", "src/app.py", "--", "true", uri=hub.uri)

        receipt = tmp_path / "receipt.json"
        built = run_cli(
            "verify-release",
            "BUILD",
            "--name",
            "USER",
            "--run",
            "true",
            "--merkle-db",
            str(hub.db_path),
            "--output",
            str(receipt),
        )
        assert built.ok(), built.output
        data = json.loads(receipt.read_text(encoding="utf-8"))
        assert data["verification"]["merkle"]["tree_size"] > 0

        # the log keeps growing append-only after the receipt
        run_cli("task", "update", "BUILD", "--status", "done", uri=hub.uri)

    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({"version": 1, "mode": "advisory", "rules": {}}), encoding="utf-8")
    checked = run_cli(
        "policy-check",
        "BUILD",
        "--policy",
        str(policy),
        "--receipt-json",
        str(receipt),
        "--merkle-db",
        str(hub.db_path),
    )
    assert checked.ok(), checked.output
    assert "merkle_commitment" in checked.stdout
    assert "still matches the recorded root" in checked.stdout


def test_hub_signed_receipt_round_trips_through_policy_check(tmp_path: Path) -> None:
    """The provenance loop: keygen, sign the commitment at release, verify offline.

    ``merkle keygen`` creates the deployment's receipt-signing keypair;
    ``verify-release --signing-key`` signs the commitment over a real hub log;
    ``policy-check --trusted-signing-key`` verifies the attestation with only
    the receipt and the ``.pub`` file — no event store access — and a foreign
    key is refused as untrusted.
    """
    key_path = tmp_path / "hub-receipt.key"
    keygen = run_cli("merkle", "keygen", str(key_path))
    assert keygen.ok(), keygen.output
    assert "key_id:" in keygen.stdout

    with isolated_hub(tmp_path) as hub:
        run_cli("task", "declare", "BUILD", "--title", "build step", uri=hub.uri)
        receipt = tmp_path / "receipt.json"
        built = run_cli(
            "verify-release",
            "BUILD",
            "--name",
            "USER",
            "--run",
            "true",
            "--merkle-db",
            str(hub.db_path),
            "--signing-key",
            str(key_path),
            "--output",
            str(receipt),
        )
        assert built.ok(), built.output
        data = json.loads(receipt.read_text(encoding="utf-8"))
        assert data["verification"]["merkle_signature"]["algorithm"] == "ed25519"

    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({"version": 1, "mode": "advisory", "rules": {}}), encoding="utf-8")
    checked = run_cli(
        "policy-check",
        "BUILD",
        "--policy",
        str(policy),
        "--receipt-json",
        str(receipt),
        "--trusted-signing-key",
        f"{key_path}.pub",
    )
    assert checked.ok(), checked.output
    assert "merkle_signature: hub key" in checked.stdout

    foreign = tmp_path / "foreign.key"
    assert run_cli("merkle", "keygen", str(foreign)).ok()
    refused = run_cli(
        "policy-check",
        "BUILD",
        "--policy",
        str(policy),
        "--receipt-json",
        str(receipt),
        "--trusted-signing-key",
        f"{foreign}.pub",
    )
    assert refused.ok(), refused.output  # advisory mode reports without blocking
    assert "untrusted key" in refused.stdout
