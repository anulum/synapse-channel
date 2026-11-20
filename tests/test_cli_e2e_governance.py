# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
"""End-to-end journeys for the governance surface: integrity and policy.

Governance commands split three ways and this module drives one representative of
each shape as a user does: event-store readers (``merkle``, ``reproduce``,
``postmortem``, ``compact``) over a written log; offline policy evaluators
(``acl shadow``) over JSON fixtures; and local key/federation management
(``encrypt-key``, ``federation``). Hub-mutating governance (``release``,
``verify-release``, ``approval``, ``supervisor``) is covered by its own journey.
"""

from __future__ import annotations

import json
from pathlib import Path

from cli_e2e_helpers import isolated_hub, run_cli


def _populate(uri: str) -> None:
    """Declare a task and take then release a file-scoped lease."""
    run_cli("task", "declare", "BUILD", "--title", "build step", uri=uri)
    run_cli("lock", "BUILD", "--paths", "src/app.py", "--", "true", uri=uri)


def _write_json(path: Path, payload: object) -> Path:
    """Write ``payload`` as JSON to ``path`` and return the path."""
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


# --- event-store readers ----------------------------------------------------


def test_merkle_root_prove_verify_round_trip(tmp_path: Path) -> None:
    """A written log commits to a root, proves a leaf, and verifies offline."""
    with isolated_hub(tmp_path) as hub:
        _populate(hub.uri)
        root = run_cli("merkle", "root", str(hub.db_path))
        assert root.ok(), root.output
        assert "Root (sha256):" in root.stdout

        proof_path = tmp_path / "proof.json"
        proof = run_cli("merkle", "prove", str(hub.db_path), "2", "--json")
        assert proof.ok(), proof.output
        proof_path.write_text(proof.stdout, encoding="utf-8")

        verified = run_cli("merkle", "verify", str(proof_path))
        assert verified.ok(), verified.output
        # The human confirmation is written to stderr; the exit code is the contract.
        assert "proof valid" in verified.output


def test_reproduce_emits_a_stable_task_digest(tmp_path: Path) -> None:
    """``reproduce`` canonicalises a task slice into a stable digest."""
    with isolated_hub(tmp_path) as hub:
        _populate(hub.uri)
        first = run_cli("reproduce", str(hub.db_path), "BUILD")
        assert first.ok(), first.output
        assert "Digest (sha256):" in first.stdout

        # Same log, same digest — determinism is the whole point.
        second = run_cli("reproduce", str(hub.db_path), "BUILD")
        assert second.stdout == first.stdout


def test_postmortem_reconstructs_a_task_timeline(tmp_path: Path) -> None:
    """``postmortem <db> <task>`` prints a forensic timeline with the owner."""
    with isolated_hub(tmp_path) as hub:
        _populate(hub.uri)
        result = run_cli("postmortem", str(hub.db_path), "BUILD")
        assert result.ok(), result.output
        assert "Postmortem: BUILD" in result.stdout
        assert "USER" in result.stdout


def test_compact_requires_an_explicit_floor(tmp_path: Path) -> None:
    """``compact`` refuses to run without ``--floor-seq`` or ``--all`` (fail-safe)."""
    with isolated_hub(tmp_path) as hub:
        _populate(hub.uri)
        refused = run_cli("compact", str(hub.db_path))
        assert refused.returncode == 2
        assert "needs a floor" in refused.output

        # A floor alone is not enough — compaction also needs a retention knob, so
        # it can never silently discard history without an explicit policy.
        no_knob = run_cli("compact", str(hub.db_path), "--all")
        assert no_knob.returncode == 2
        assert "retention knob" in no_knob.output

        compacted = run_cli("compact", str(hub.db_path), "--all", "--max-checkpoints-per-task", "5")
        assert compacted.ok(), compacted.output


# --- offline policy evaluators ----------------------------------------------


def test_acl_shadow_reports_allow_and_deny(tmp_path: Path) -> None:
    """``acl shadow`` evaluates requests against a policy without ever blocking."""
    policy = _write_json(
        tmp_path / "acl.json",
        {
            "rules": [
                {
                    "permission": "claim",
                    "target_kind": "path",
                    "target_pattern": "src/*",
                    "namespace": "P",
                    "reason": "core may claim src",
                }
            ]
        },
    )
    requests = _write_json(
        tmp_path / "req.json",
        [
            {
                "subject": "P/a",
                "permission": "claim",
                "target_kind": "path",
                "target_value": "src/x.py",
            },
            {
                "subject": "P/a",
                "permission": "metrics",
                "target_kind": "metrics",
                "target_value": "live",
            },
        ],
    )
    result = run_cli(
        "acl", "shadow", "--policy", str(policy), "--requests", str(requests), "--project", "P"
    )
    # Shadow mode is advisory: it reports, it never blocks, so it exits zero.
    assert result.ok(), result.output


def test_acl_shadow_reports_a_missing_requests_file(tmp_path: Path) -> None:
    """``acl shadow`` fails clearly when the requests file is absent."""
    policy = _write_json(tmp_path / "acl.json", {"rules": []})
    result = run_cli(
        "acl", "shadow", "--policy", str(policy), "--requests", str(tmp_path / "absent.json")
    )
    assert result.returncode == 2
    assert "requests file does not exist" in result.output


# --- local key and federation management ------------------------------------


def test_federation_list_is_empty_by_default() -> None:
    """``federation list`` reports no imported peer domains on a fresh install."""
    result = run_cli("federation", "list")
    assert result.ok(), result.output
    assert "no peer domains imported" in result.stdout


def test_hub_refuses_a_scope_granting_federation_store_without_message_auth(
    tmp_path: Path,
) -> None:
    """An imported peering granting scope the hub cannot enforce refuses to start.

    The journey is the real operator path: ``federation import`` writes the store,
    then ``synapse hub --federation-store`` without ``--require-message-auth`` must
    exit fatally and name ``--federation-observe-only`` as the declared-intent
    escape hatch, instead of running with silently unenforceable peerings.
    """
    bundle = _write_json(
        tmp_path / "peer.json",
        {
            "domain_id": "domain-b",
            "namespaces": ["SYNAPSE-CHANNEL"],
            "certificate_pins": ["sha256:aa"],
            "signing_key_ids": ["domain-b:main"],
            "scope_grants": [{"verb": "message", "namespace": "SYNAPSE-CHANNEL"}],
        },
    )
    store = tmp_path / "federation.json"
    imported = run_cli(
        "federation", "import", str(bundle), "--confirmed-by", "ops", "--store", str(store)
    )
    assert imported.ok(), imported.output

    hub = run_cli("hub", "--federation-store", str(store))
    assert hub.returncode == 2
    assert "grants cross-domain scope" in hub.stderr
    assert "--federation-observe-only" in hub.stderr


def test_hub_refuses_observe_only_without_a_federation_store() -> None:
    """A declared observe-only intent with no store to observe is a config error."""
    hub = run_cli("hub", "--federation-observe-only")
    assert hub.returncode == 2
    assert "requires --federation-store" in hub.stderr


def test_encrypt_key_generate_then_check(tmp_path: Path) -> None:
    """``encrypt-key generate <path>`` writes a key that ``check`` then accepts."""
    key_path = tmp_path / "at-rest.key"
    generated = run_cli("encrypt-key", "generate", str(key_path))
    assert generated.ok(), generated.output
    assert key_path.exists()

    checked = run_cli("encrypt-key", "check", str(key_path))
    assert checked.ok(), checked.output
