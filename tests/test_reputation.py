# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — advisory ranking over real journal evidence
"""Exercise the public projection, snapshot, keys, provenance and filters."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from synapse_channel.core.event_row_mac import load_or_create_row_mac_key, row_mac_key_path
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.persistence_sqlcipher import sqlcipher_available
from synapse_channel.core.reputation import (
    build_reputation,
    render_reputation,
    reputation_to_json,
    run_reputation,
)
from synapse_channel.core.state import TaskClaim
from synapse_channel.core.trust_graph import TrustGraph, TrustGraphEdge, TrustGraphNode
from test_trust_graph import _seed_store


def test_journal_ranking_counts_all_evidence_without_mutation(tmp_path: Path) -> None:
    path = tmp_path / "journal.db"
    _seed_store(path)
    before = path.read_bytes()
    report = run_reputation(path, as_of=100)
    by_agent = {a.agent: a for a in report.agents}
    assert by_agent["alpha"].positive == 1
    assert by_agent["alpha"].negatives == (("conflict_pair", 1), ("declared_failed_check", 1))
    assert by_agent["beta"].negatives == (("conflict_pair", 1), ("stale_claim", 1))
    assert by_agent["gamma"].negatives == (("broken_handoff_candidate", 1), ("stale_claim", 1))
    assert by_agent["alpha"].evidence_seqs == (2, 3, 6)
    assert by_agent["beta"].evidence_seqs == (4, 6)
    assert by_agent["beta"].negative_total == 2
    assert [a.agent for a in report.agents] == ["alpha", "beta", "gamma"]
    payload = reputation_to_json(report)
    assert payload["schema"] == "agent-reputation.v1"
    assert payload["authorises_execution"] is False
    assert report.generated_from_seq == 7
    assert "evidence seqs=[2, 3, 6]" in render_reputation(report)
    assert path.read_bytes() == before
    assert not row_mac_key_path(path).exists()


def test_filter_ceiling_and_empty_snapshot(tmp_path: Path) -> None:
    path = tmp_path / "journal.db"
    _seed_store(path)
    report = run_reputation(path, as_of=100, agent="alpha", since=3)
    assert {a.agent for a in report.agents} == {"alpha", "beta"}
    assert all(2 not in a.evidence_seqs for a in report.agents)
    positive = run_reputation(path, through_seq=2)
    assert positive.agents[0].net == 1
    assert "[none]" in render_reputation(positive)
    empty = run_reputation(path, through_seq=0)
    assert empty.agents == () and empty.generated_from_seq == 0
    assert render_reputation(empty).endswith("No agent evidence found.")
    assert run_reputation(path, agent="absent").agents == ()


def test_ties_and_self_conflict_count_one_edge_once() -> None:
    graph = TrustGraph(
        4,
        5,
        tuple(TrustGraphNode("agent:" + name, "agent", name) for name in ("z", "a", "b")),
        (
            TrustGraphEdge("agent:z", "task:T", "positive_receipt", 1, 1, ""),
            TrustGraphEdge("agent:a", "task:T", "positive_receipt", 2, 2, ""),
            TrustGraphEdge("agent:b", "task:T", "positive_receipt", 3, 3, ""),
            TrustGraphEdge("agent:b", "task:T", "positive_receipt", 4, 4, ""),
            TrustGraphEdge("agent:b", "agent:b", "conflict_pair", 5, 5, ""),
        ),
    )
    report = build_reputation(graph)
    assert [a.agent for a in report.agents] == ["b", "a", "z"]
    assert report.agents[0].net == 1 and report.agents[0].negative_total == 1


@pytest.mark.parametrize("field", ["as_of", "since"])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf"), True])
def test_api_refuses_invalid_clocks_before_opening(
    tmp_path: Path, field: str, value: float
) -> None:
    path = tmp_path / "absent.db"
    with pytest.raises(ValueError, match="finite"):
        if field == "as_of":
            run_reputation(path, as_of=value)
        else:
            run_reputation(path, since=value)
    assert not path.exists()


def test_authenticated_reader_requires_existing_key_and_refuses_tamper(tmp_path: Path) -> None:
    path = tmp_path / "journal.db"
    store = EventStore(path)
    store.append("ledger_task", {"task_id": "T", "title": "task"}, ts=1)
    key_path = row_mac_key_path(path)
    key = load_or_create_row_mac_key(key_path, current_max_seq=1, log_has_macs=False)
    key_path.chmod(0o600)
    store.enable_row_mac(key)
    store.append(
        "ledger_progress",
        {
            "task_id": "T",
            "author": "agent",
            "kind": "assessment",
            "text": "release receipt: evidence=pytest tests/test_api.py -q",
        },
        ts=2,
    )
    store.close()
    assert run_reputation(path, row_mac_key_file=key_path).agents[0].positive == 1
    with pytest.raises(ValueError):
        run_reputation(path)
    missing = tmp_path / "missing.key"
    with pytest.raises(ValueError):
        run_reputation(path, row_mac_key_file=missing)
    assert not missing.exists()
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE events SET payload='{}' WHERE seq=2")
    with pytest.raises(ValueError, match="authentication"):
        run_reputation(path, row_mac_key_file=key_path)


@pytest.mark.parametrize("field", ["max_events", "max_payload_bytes"])
def test_exhausted_snapshot_refuses_instead_of_ranking_prefix(tmp_path: Path, field: str) -> None:
    path = tmp_path / "journal.db"
    _seed_store(path)
    with pytest.raises(ValueError):
        run_reputation(path, max_events=1) if field == "max_events" else run_reputation(
            path, max_payload_bytes=1
        )


def test_native_encrypted_snapshot_and_wrong_key(tmp_path: Path) -> None:
    key = tmp_path / "cipher.key"
    key.write_bytes(b"k" * 32)
    key.chmod(0o600)
    path = tmp_path / "cipher.db"
    if not sqlcipher_available():
        EventStore(path).close()
        with pytest.raises(RuntimeError):
            run_reputation(path, key_file=key)
        return
    store = EventStore(path, key_file=key)
    store.append("ledger_task", {"task_id": "T", "title": "task"}, ts=1)
    store.append(
        "ledger_progress",
        {
            "task_id": "T",
            "author": "alpha",
            "kind": "assessment",
            "text": "release receipt: evidence=pytest tests/test_api.py -q",
        },
        ts=2,
    )
    store.close()
    before = path.read_bytes()
    assert run_reputation(path, key_file=key).agents[0].net == 1
    assert path.read_bytes() == before
    wrong = tmp_path / "wrong.key"
    wrong.write_bytes(b"w" * 32)
    wrong.chmod(0o600)
    with pytest.raises(ValueError):
        run_reputation(path, key_file=wrong)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("lease_expires_at", None),
        ("lease_expires_at", 10**400),
        ("lease_expires_at", "private-invalid-clock"),
        ("paths", None),
        ("paths", False),
        ("paths", 7),
    ],
)
def test_malformed_claim_refuses_public_ranking_without_mutation(
    tmp_path: Path, field: str, value: object
) -> None:
    path = tmp_path / "journal.db"
    payload = TaskClaim("T", "alpha", "", 1, 2, paths=("src/a.py",)).as_dict()
    payload[field] = value
    store = EventStore(path)
    store.append("claim", payload, ts=1, durable=True)
    store.close()
    before = path.read_bytes()
    with pytest.raises(ValueError):
        run_reputation(path, as_of=3)
    assert path.read_bytes() == before
    assert not row_mac_key_path(path).exists()
