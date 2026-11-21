# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — offline readers leave the journal they read unwritten
"""Every offline report reads the hub's journal without writing a byte of it.

Each case runs one public entry point against a journal in the state a release
from before the store-format floor left it. The hub's writer, opened on such a
journal, records the format at once, so a reader that still opened the writer
would change the file before reading anything. A domain refusal after the read,
such as an unknown task, is allowed; the journal's bytes must not change either way.
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Callable
from contextlib import closing
from pathlib import Path

import pytest

from cli_e2e_helpers import run_cli
from synapse_channel.client.diagnostics import check_sqlcipher_event_store
from synapse_channel.core.accounting import run_accounting_report
from synapse_channel.core.adaptive_ttl import run_ttl_advice
from synapse_channel.core.approvals import run_approval_report
from synapse_channel.core.at_rest import generate_key_file
from synapse_channel.core.capability_observations import read_observed_capability_index
from synapse_channel.core.causality import run_causality
from synapse_channel.core.causality_federation import HubEventRef, run_federated_causality
from synapse_channel.core.causality_health import run_causal_health
from synapse_channel.core.causality_otel import run_otel_projection
from synapse_channel.core.cross_repo_graph import run_cross_repo_graph
from synapse_channel.core.fleet_health import run_local_fleet_health
from synapse_channel.core.memory_projection import read_memory_recall
from synapse_channel.core.merkle import run_proof, run_root
from synapse_channel.core.merkle_checkpoint import MerkleCheckpointStore
from synapse_channel.core.operation_report import run_operation_report, run_operation_reports
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.persistence_sqlcipher import connect_sqlcipher, sqlcipher_available
from synapse_channel.core.postmortem import run_task_postmortem
from synapse_channel.core.reliability import run_reliability_report
from synapse_channel.core.replay import load_task_for_seq, run_fork
from synapse_channel.core.reproduce import run_reproduction
from synapse_channel.core.trust_graph import run_trust_graph
from synapse_channel.core.yield_advice import run_yield_advice
from synapse_channel.dashboard_store_feeds import (
    build_causality_feed,
    build_events_tail,
    build_findings_feed,
    build_metrics_feed,
    build_operator_actions_feed,
    build_receipts_feed,
    build_state_at_feed,
    build_waits_feed,
    latest_cursor,
    resolve_task_last_seq,
    store_task_projects,
)
from synapse_channel.effect_suite.observer import journal_reading
from synapse_channel.native_query import run_native_query
from synapse_channel.participants.session_metric_report import run_session_metric_report


def _pre_floor_journal(path: Path) -> None:
    """Write a real journal through the hub's writer, then return it to the pre-floor state."""
    with EventStore(path) as store:
        store.append("chat", {"sender": "PROJ/alice", "text": "Finding: the cache is cold"})
        store.append("chat", {"sender": "PROJ/bob", "text": "agreed"})
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("DROP TABLE store_format")
        connection.execute("PRAGMA user_version = 0")
        connection.commit()


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _cli(*args: str) -> None:
    result = run_cli(*args)
    assert "Traceback" not in result.stderr, result.stderr


def _suggest_compute(db: Path, work: Path) -> None:
    tasks = work / "tasks.json"
    tasks.write_text('{"tasks": []}', encoding="utf-8")
    tasks.chmod(0o600)
    _cli(
        "entitlements",
        "suggest-compute",
        "--file",
        str(tasks),
        "--hub-db",
        str(db),
        "--reviewer",
        "CEO/claude",
        "--store",
        str(work / "ledger.db"),
    )


def _merkle_checkpoint_verify(db: Path, work: Path) -> None:
    checkpoints = work / "checkpoints.db"
    MerkleCheckpointStore(checkpoints).close()
    _cli("merkle", "checkpoint", str(db), "--checkpoint-db", str(checkpoints), "--verify")


READERS: dict[str, Callable[[Path, Path], object]] = {
    "accounting": lambda db, _: run_accounting_report(db),
    "adaptive-ttl": lambda db, _: run_ttl_advice(db),
    "approvals": lambda db, _: run_approval_report(db),
    "capability-observations": lambda db, _: read_observed_capability_index(db),
    "causality": lambda db, _: run_causality(db, "causes", 1),
    "causality-federation": lambda db, _: run_federated_causality(
        {"hub-a": db}, "causes", HubEventRef("hub-a", 1)
    ),
    "causal-health": lambda db, _: run_causal_health(db),
    "causality-otel": lambda db, _: run_otel_projection(db),
    "cross-repo-graph": lambda db, work: run_cross_repo_graph(work, db_path=db),
    "fleet-health": lambda db, _: run_local_fleet_health(db),
    "memory-recall": lambda db, _: read_memory_recall(db, "cache"),
    "merkle-root": lambda db, _: run_root(db),
    "merkle-proof": lambda db, _: run_proof(db, 1),
    "operation-report": lambda db, _: run_operation_report(db, "op-absent"),
    "operation-reports": lambda db, _: run_operation_reports(db),
    "postmortem": lambda db, _: run_task_postmortem(db, "T-absent"),
    "reliability": lambda db, _: run_reliability_report(db),
    "replay-fork": lambda db, _: run_fork(db, "T-absent", fork_seq=1, overrides={}),
    "replay-task": lambda db, _: load_task_for_seq(db, 1),
    "reproduce": lambda db, _: run_reproduction(db, "T-absent"),
    "trust-graph": lambda db, _: run_trust_graph(db),
    "yield-advice": lambda db, _: run_yield_advice(db),
    "effect-suite-journal": lambda db, _: journal_reading(db, "T-absent"),
    "native-query": lambda db, _: run_native_query(db, "native-messages all"),
    "session-metrics": lambda db, _: run_session_metric_report(db),
    "cli-attention-sync": lambda db, work: _cli(
        "attention", "sync", str(db), "--store", str(work / "q.db")
    ),
    "cli-ingest": lambda db, _: _cli("ingest", str(db)),
    "cli-verdict-status": lambda db, _: _cli("verdict", "status", str(db)),
    "cli-entitlements-suggest-compute": _suggest_compute,
    "cli-merkle-checkpoint-verify": _merkle_checkpoint_verify,
    "cli-multihub-observe": lambda db, _: _cli(
        "multihub", "observe", "--peer-db", str(db), "--peer-id", "peer-a", "--json"
    ),
    "dashboard-events-tail": lambda db, _: build_events_tail(db),
    "dashboard-state-at": lambda db, _: build_state_at_feed(db, seq=1),
    "dashboard-task-projects": lambda db, _: store_task_projects(db),
    "dashboard-waits": lambda db, _: build_waits_feed(db),
    "dashboard-operator-actions": lambda db, _: build_operator_actions_feed(db),
    "dashboard-receipts": lambda db, _: build_receipts_feed(db),
    "dashboard-metrics": lambda db, _: build_metrics_feed(db),
    "dashboard-findings": lambda db, _: build_findings_feed(db),
    "dashboard-latest-cursor": lambda db, _: latest_cursor(db),
    "dashboard-task-last-seq": lambda db, _: resolve_task_last_seq(db, "T-absent"),
    "dashboard-causality": lambda db, _: build_causality_feed(db, direction="causes", seq=1),
}


@pytest.mark.parametrize("reader", sorted(READERS))
def test_an_offline_reader_leaves_the_journal_unwritten(tmp_path: Path, reader: str) -> None:
    db = tmp_path / "journal" / "hub.db"
    db.parent.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    _pre_floor_journal(db)
    before = _digest(db)

    try:
        READERS[reader](db, work)
    except (ValueError, LookupError):
        pass  # a domain refusal after the read; the journal is what this test checks

    assert _digest(db) == before
    with closing(sqlite3.connect(db)) as connection:
        assert connection.execute(
            "SELECT count(*) FROM sqlite_master WHERE name = 'store_format'"
        ).fetchone() == (0,)


@pytest.mark.parametrize("reader", sorted(READERS))
def test_an_offline_reader_creates_no_journal_at_a_missing_path(
    tmp_path: Path, reader: str
) -> None:
    """A mistyped path is refused; the writer used to create an empty journal there."""
    db = tmp_path / "journal" / "hub.db"
    db.parent.mkdir()
    work = tmp_path / "work"
    work.mkdir()

    try:
        READERS[reader](db, work)
    except (ValueError, LookupError):
        pass  # a missing journal is refused

    assert sorted(entry.name for entry in db.parent.iterdir()) == []


def test_the_sqlcipher_diagnostic_reads_the_encrypted_journal_unwritten(tmp_path: Path) -> None:
    """With a SQLCipher driver the diagnostic reads an encrypted pre-floor journal unwritten.

    Without the driver it reports the missing driver as a failure, and the journal
    it was pointed at is left unchanged.
    """
    key = generate_key_file(tmp_path / "hub.key")
    db = tmp_path / "journal" / "hub.db"
    db.parent.mkdir()
    if not sqlcipher_available():
        with EventStore(db) as store:
            store.append("chat", {"sender": "PROJ/alice", "text": "kept"})
        unchanged = (_digest(db), sorted(entry.name for entry in db.parent.iterdir()))
        missing = check_sqlcipher_event_store(db, key)
        assert (missing.status, missing.detail) == ("fail", "SQLCipher driver not installed")
        assert (_digest(db), sorted(entry.name for entry in db.parent.iterdir())) == unchanged
        return
    with EventStore(db, key_file=key) as store:
        store.append("chat", {"sender": "PROJ/alice", "text": "kept"})
    connection = connect_sqlcipher(db, key.read_bytes())
    try:
        connection.execute("DROP TABLE store_format")
        connection.execute("PRAGMA user_version = 0")
        connection.commit()
    finally:
        connection.close()
    before = _digest(db)

    diagnosis = check_sqlcipher_event_store(db, key)

    assert diagnosis.status == "pass", diagnosis
    assert _digest(db) == before
