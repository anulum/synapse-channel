# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for deterministic memory projection and recall

from __future__ import annotations

import json
from pathlib import Path

import pytest

from synapse_channel.core.journal import EventKind
from synapse_channel.core.memory_projection import (
    MEMORY_RECALL_TRUST_BOUNDARY,
    MemoryRecallInputError,
    memory_recall_to_json,
    project_memory_events,
    read_memory_recall,
    recall_memory,
    render_memory_recall,
)
from synapse_channel.core.persistence import EventStore, StoredEvent


def _memory_store(path: Path) -> None:
    """Write representative memory records to ``path``."""
    store = EventStore(path)
    store.append(EventKind.CHAT, {"payload": "private transport chat"}, ts=1.0)
    store.append(
        EventKind.FINDING,
        {
            "statement": "The websocket transport reconnect loop is stable.",
            "evidence_ref": "tests/test_transport.py::test_reconnect",
            "provenance": {"actor": "codex-a", "project": "SYNAPSE-CHANNEL"},
        },
        ts=2.0,
        durable=True,
    )
    store.append(
        EventKind.CHECKPOINT,
        {
            "task_id": "MEM-1",
            "owner": "codex-b",
            "checkpoint": "Captured vector recall parser wiring.",
            "paths": ["src/synapse_channel/cli.py"],
        },
        ts=3.0,
    )
    store.append(
        EventKind.HANDOFF,
        {
            "task_id": "MEM-2",
            "from": "codex-b",
            "to": "codex-c",
            "note": "Continue memory projection tests and documentation.",
        },
        ts=4.0,
    )
    store.close()


def test_project_memory_events_preserves_provenance_and_ignores_recall_queries() -> None:
    events = [
        StoredEvent(seq=1, ts=1.0, kind=EventKind.CHAT, payload={"payload": "ignored"}),
        StoredEvent(seq=2, ts=2.0, kind=EventKind.RECALL, payload={"query_text": "ignored"}),
        StoredEvent(
            seq=3,
            ts=3.0,
            kind=EventKind.FINDING,
            payload={"statement": "Durable transport memory", "provenance": {"actor": "agent-a"}},
        ),
    ]

    records = project_memory_events(events)

    assert len(records) == 1
    assert records[0].seq == 3
    assert records[0].kind == EventKind.FINDING
    assert records[0].source == "finding.statement"
    assert records[0].actor == "agent-a"
    assert records[0].tokens == ("durable", "memory", "transport")


def test_recall_memory_ranks_hits_and_explains_matches() -> None:
    events = [
        StoredEvent(
            seq=7,
            ts=7.0,
            kind=EventKind.FINDING,
            payload={
                "statement": "Websocket transport reconnect logic",
                "evidence_ref": "tests/test_transport.py",
                "provenance": {"actor": "codex-a"},
            },
        ),
        StoredEvent(
            seq=8,
            ts=8.0,
            kind=EventKind.CHECKPOINT,
            payload={"task_id": "DOC", "owner": "codex-b", "checkpoint": "Documentation pass"},
        ),
    ]

    report = recall_memory(events, "transport websocket")

    assert report.query == "transport websocket"
    assert report.trust_boundary == MEMORY_RECALL_TRUST_BOUNDARY
    assert report.hits[0].seq == 7
    assert report.hits[0].score == pytest.approx(1.0)
    assert report.hits[0].matched_tokens == ("transport", "websocket")
    assert report.hits[0].evidence_ref == "tests/test_transport.py"


def test_recall_memory_handles_empty_query_and_limit() -> None:
    events = [
        StoredEvent(
            seq=3,
            ts=3.0,
            kind=EventKind.FINDING,
            payload={"statement": "Durable transport memory"},
        )
    ]

    assert recall_memory(events, "and the", limit=5).hits == ()
    assert recall_memory(events, "transport", limit=0).hits == ()


def test_projection_drops_empty_records_and_uses_payload_actor_fallbacks() -> None:
    events = [
        StoredEvent(seq=1, ts=1.0, kind=EventKind.FINDING, payload={"statement": "and the"}),
        StoredEvent(
            seq=2,
            ts=2.0,
            kind=EventKind.CHECKPOINT,
            payload={"task_id": "T", "owner": "owner-a", "paths": ["src/a.py", 3]},
        ),
        StoredEvent(
            seq=3,
            ts=3.0,
            kind=EventKind.HANDOFF,
            payload={"note": "fallback branch", "provenance": {"actor": ""}},
        ),
    ]

    records = project_memory_events(events)

    assert len(records) == 2
    assert records[0].actor == "owner-a"
    assert records[0].text == "src/a.py"
    assert records[1].actor == ""


def test_memory_recall_json_and_human_rendering_are_stable(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    _memory_store(db)

    report = read_memory_recall(db, "memory projection", since_seq=0, limit=3)
    payload = json.loads(memory_recall_to_json(report))
    rendered = render_memory_recall(report)

    assert payload["query"] == "memory projection"
    assert payload["query_tokens"] == ["memory", "projection"]
    assert payload["hits"][0]["task_id"] == "MEM-2"
    assert payload["hits"][0]["source"] == "handoff.note"
    assert "Memory recall for: memory projection" in rendered
    assert "MEM-2" in rendered
    assert MEMORY_RECALL_TRUST_BOUNDARY in rendered


def test_render_memory_recall_reports_empty_result() -> None:
    report = recall_memory((), "and the")

    rendered = render_memory_recall(report)

    assert "Query tokens: (none)" in rendered
    assert "No matching memory records." in rendered


def test_read_memory_recall_rejects_missing_store(tmp_path: Path) -> None:
    missing = tmp_path / "missing.db"

    with pytest.raises(MemoryRecallInputError, match=f"missing event store: {missing}"):
        read_memory_recall(missing, "memory")
