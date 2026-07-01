# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
"""End-to-end journeys for the analysis surface, over a written event log.

A short coordination journey declares a task and takes a file-scoped lease (a
claim/release pair), then the read-only analysis commands are run against the
same hub and its database exactly as a user would: event-query, causality,
reliability, accounting, conflicts, directory, identity audit, and relay.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from cli_e2e_helpers import isolated_hub, run_cli


def _scalar(db_path: Path, query: str) -> int:
    """Return a single-integer scalar from the hub event store."""
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return int(connection.execute(query).fetchone()[0])
    finally:
        connection.close()


def _max_seq(db_path: Path) -> int:
    """Return the highest sequence written to the hub event store."""
    return _scalar(db_path, "select max(seq) from events")


def _claim_seq(db_path: Path) -> int:
    """Return the sequence of the first claim event in the log."""
    return _scalar(db_path, "select seq from events where kind='claim' limit 1")


def _populate(uri: str) -> None:
    """Declare a task and take then release a file-scoped lease on the hub."""
    declared = run_cli("task", "declare", "BUILD", "--title", "build step", uri=uri)
    assert declared.ok(), declared.output
    # `lock <task> --paths ... -- <cmd>`: hold the lease while a trivial command
    # runs, writing a claim then a release to the log.
    locked = run_cli("lock", "BUILD", "--paths", "src/app.py", "--", "true", uri=uri)
    assert locked.ok(), locked.output


def test_event_query_reconstructs_a_task_timeline(tmp_path: Path) -> None:
    """``event-query`` prints the claim/release timeline for a task."""
    with isolated_hub(tmp_path) as hub:
        _populate(hub.uri)
        result = run_cli("event-query", str(hub.db_path), "task BUILD timeline")
        assert result.ok(), result.output
        assert "task BUILD timeline" in result.stdout
        assert "kind=claim" in result.stdout
        assert "kind=release" in result.stdout


def test_causality_effects_reads_the_written_log(tmp_path: Path) -> None:
    """``causality effects`` answers for a real sequence in the log."""
    with isolated_hub(tmp_path) as hub:
        _populate(hub.uri)
        seq = _max_seq(hub.db_path)
        result = run_cli("causality", "effects", str(hub.db_path), str(seq))
        assert result.ok(), result.output
        assert f"seq {seq}" in result.stdout


def test_reliability_reports_no_signals_on_a_clean_log(tmp_path: Path) -> None:
    """``reliability`` is honest audit signals, not scores; clean log = none."""
    with isolated_hub(tmp_path) as hub:
        _populate(hub.uri)
        result = run_cli("reliability", str(hub.db_path))
        assert result.ok(), result.output
        assert "No reliability signals found" in result.stdout


def test_accounting_reports_no_usage_without_model_events(tmp_path: Path) -> None:
    """``accounting report`` is opt-in evidence; none recorded on a fresh hub."""
    with isolated_hub(tmp_path) as hub:
        _populate(hub.uri)
        result = run_cli("accounting", "report", str(hub.db_path))
        assert result.ok(), result.output
        assert "No recorded model usage found" in result.stdout


def test_debug_fork_reconstructs_claim_state(tmp_path: Path) -> None:
    """``debug --fork-at`` reconstructs a named task's state at a sequence point."""
    with isolated_hub(tmp_path) as hub:
        _populate(hub.uri)
        claim_seq = _claim_seq(hub.db_path)
        result = run_cli("debug", str(hub.db_path), "--fork-at", str(claim_seq), "--task", "BUILD")
        assert result.ok(), result.output
        assert "Fork: BUILD" in result.stdout
        assert "Held at fork point: yes" in result.stdout


def test_conflicts_predicts_none_on_disjoint_state(tmp_path: Path) -> None:
    """``conflicts`` queries the live hub and predicts none after release."""
    with isolated_hub(tmp_path) as hub:
        _populate(hub.uri)
        result = run_cli("conflicts", uri=hub.uri)
        assert result.ok(), result.output
        assert "No predicted conflicts" in result.stdout


def test_directory_lists_the_trust_boundary(tmp_path: Path) -> None:
    """``directory`` prints its discovery-only trust boundary even when empty."""
    with isolated_hub(tmp_path) as hub:
        result = run_cli("directory", uri=hub.uri)
        assert result.ok(), result.output
        assert "Directory (0 entries)" in result.stdout
        assert "discovery metadata only" in result.stdout


def test_identity_audit_is_offline_and_needs_identity_files() -> None:
    """``identity audit`` audits identity *files* offline (no hub, no ``--uri``)."""
    missing = run_cli("identity", "audit")
    assert missing.returncode == 2
    assert "--identities" in missing.output

    # It reads identity files from disk; a missing path is a clear, non-zero error.
    not_found = run_cli("identity", "audit", "--identities", "does-not-exist.json")
    assert not_found.returncode != 0
    assert "does not exist" in not_found.output


def test_relay_replays_the_shared_feed(tmp_path: Path) -> None:
    """``relay`` replays the newline-delimited relay log the hub appended."""
    feed = tmp_path / "feed.ndjson"
    with isolated_hub(tmp_path, extra_args=("--relay-log", str(feed))) as hub:
        _populate(hub.uri)
        result = run_cli("relay", str(feed))
        assert result.ok(), result.output
        assert "BUILD" in result.stdout
