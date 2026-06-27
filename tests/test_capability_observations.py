# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for observed capability routing evidence

from __future__ import annotations

import json
from pathlib import Path

from synapse_channel.core.capability_observations import (
    OBSERVED_CAPABILITY_TRUST_BOUNDARY,
    build_observed_capability_index,
    observed_capability_index_to_json,
    read_observed_capability_index,
)
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore


def _seed_store(path: Path) -> None:
    """Write ledger tasks and receipts used by observed-capability tests."""
    store = EventStore(path)
    store.append(
        EventKind.LEDGER_TASK,
        {
            "task_id": "ROUTED",
            "title": "Python routing cleanup",
            "description": "Improve deterministic route fallback.",
            "depends_on": [],
            "status": "done",
            "suggested_owner": "",
            "created_by": "planner",
            "created_at": 1.0,
            "updated_at": 2.0,
        },
        ts=1.0,
        durable=True,
    )
    store.append(
        EventKind.LEDGER_PROGRESS,
        {
            "task_id": "ROUTED",
            "author": "FAST",
            "kind": "assessment",
            "text": (
                "release receipt: evidence=pytest tests/test_routing.py -q; "
                "changed_files=src/synapse_channel/core/semantic_routing.py; "
                "epistemic_status=supported"
            ),
            "posted_at": 3.0,
        },
        ts=3.0,
    )
    store.append(
        EventKind.LEDGER_PROGRESS,
        {
            "task_id": "ROUTED",
            "author": "SLOW",
            "kind": "assessment",
            "text": "release receipt: known_failures=mypy failed; epistemic_status=degraded",
            "posted_at": 4.0,
        },
        ts=4.0,
    )
    store.append(
        EventKind.LEDGER_PROGRESS,
        {
            "task_id": "MISSING",
            "author": "FAST",
            "kind": "assessment",
            "text": "release receipt: evidence=pytest -q",
            "posted_at": 5.0,
        },
        ts=5.0,
    )
    store.close()


def test_build_observed_capability_index_preserves_positive_evidence(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    _seed_store(db)
    store = EventStore(db)
    try:
        index = build_observed_capability_index(store.read_all())
    finally:
        store.close()

    assert index.trust_boundary == OBSERVED_CAPABILITY_TRUST_BOUNDARY
    assert [evidence.agent for evidence in index.evidence] == ["FAST"]
    evidence = index.evidence[0]
    assert evidence.task_id == "ROUTED"
    assert evidence.seq == 2
    assert evidence.tokens == (
        "cleanup",
        "deterministic",
        "fallback",
        "improve",
        "python",
        "route",
        "routing",
        "semantic_routing",
        "src",
        "synapse_channel",
        "test_routing",
        "tests",
    )
    assert index.tokens_for_agent("FAST") == set(evidence.tokens)
    assert index.evidence_for_agent("FAST") == (evidence,)
    assert index.evidence_for_agent("UNKNOWN") == ()


def test_observed_capability_index_json_and_read_path_are_stable(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    _seed_store(db)

    payload = json.loads(observed_capability_index_to_json(read_observed_capability_index(db)))

    assert payload["trust_boundary"] == OBSERVED_CAPABILITY_TRUST_BOUNDARY
    assert payload["evidence"][0]["agent"] == "FAST"
    assert payload["evidence"][0]["source"] == "release_receipt"


def test_read_observed_capability_index_rejects_missing_store(tmp_path: Path) -> None:
    missing = tmp_path / "missing.db"

    try:
        read_observed_capability_index(missing)
    except ValueError as exc:
        assert str(exc) == f"missing event store: {missing}"
    else:  # pragma: no cover - defensive branch for test clarity
        raise AssertionError("missing event store was accepted")


def test_observed_capability_index_ignores_malformed_and_empty_events(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    store = EventStore(db)
    store.append(
        EventKind.LEDGER_TASK,
        {
            "task_id": "",
            "title": "Blank id",
            "description": "ignored",
            "depends_on": [],
            "status": "done",
            "suggested_owner": "",
            "created_by": "planner",
            "created_at": 1.0,
            "updated_at": 2.0,
        },
        ts=1.0,
    )
    store.append(
        EventKind.LEDGER_TASK,
        {
            "task_id": "EMPTY",
            "title": "a",
            "description": "of",
            "depends_on": [],
            "status": "done",
            "suggested_owner": "",
            "created_by": "planner",
            "created_at": 1.0,
            "updated_at": 2.0,
        },
        ts=2.0,
    )
    store.append(
        EventKind.LEDGER_PROGRESS,
        {
            "task_id": "EMPTY",
            "author": "FAST",
            "kind": "note",
            "text": "release receipt: evidence=pytest -q",
            "posted_at": 3.0,
        },
        ts=3.0,
    )
    store.append(
        EventKind.LEDGER_PROGRESS,
        {
            "task_id": "EMPTY",
            "author": "FAST",
            "kind": "assessment",
            "text": "release receipt:",
            "posted_at": 4.0,
        },
        ts=4.0,
    )
    try:
        index = build_observed_capability_index(store.read_all())
    finally:
        store.close()

    assert index.evidence == ()
