# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — evidence trust-graph regressions

from __future__ import annotations

from pathlib import Path

import pytest

from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.state import TaskClaim
from synapse_channel.core.trust_graph import (
    TRUST_GRAPH_BOUNDARY,
    UNKNOWN_LABEL,
    TrustGraph,
    _dot_quote,
    build_trust_graph,
    graph_involving,
    render_trust_graph_dot,
    render_trust_graph_human,
    run_trust_graph,
    trust_graph_to_json,
)


def _claim(
    *,
    task_id: str,
    owner: str,
    paths: tuple[str, ...],
    lease_expires_at: float,
    epoch: int = 1,
) -> TaskClaim:
    return TaskClaim(
        task_id=task_id,
        owner=owner,
        note="work",
        claimed_at=1.0,
        lease_expires_at=lease_expires_at,
        status="claimed",
        data_ref="",
        worktree="repo",
        paths=paths,
        epoch=epoch,
        checkpoint="",
    )


def _seed_store(path: Path) -> None:
    store = EventStore(path)
    store.append(
        EventKind.LEDGER_TASK,
        {"task_id": "ROUTING", "title": "Python routing cleanup", "description": ""},
        ts=1.0,
    )
    store.append(
        EventKind.LEDGER_PROGRESS,
        {
            "task_id": "ROUTING",
            "author": "alpha",
            "kind": "assessment",
            "text": "release receipt: evidence=pytest tests/test_routing.py -q",
            "posted_at": 2.0,
        },
        ts=2.0,
    )
    store.append(
        EventKind.LEDGER_PROGRESS,
        {
            "task_id": "ROUTING",
            "author": "alpha",
            "kind": "assessment",
            "text": "release receipt: known_failures=mypy failed; epistemic_status=degraded",
            "posted_at": 3.0,
        },
        ts=3.0,
    )
    store.append(
        EventKind.CLAIM,
        _claim(
            task_id="STALE",
            owner="beta",
            paths=("stale/task.py",),
            lease_expires_at=20.0,
        ).as_dict(),
        ts=4.0,
        durable=True,
    )
    store.append(
        EventKind.CLAIM,
        _claim(
            task_id="OVERLAP-A",
            owner="alpha",
            paths=("src/api.py",),
            lease_expires_at=200.0,
        ).as_dict(),
        ts=5.0,
        durable=True,
    )
    store.append(
        EventKind.CLAIM,
        _claim(
            task_id="OVERLAP-B",
            owner="beta",
            paths=("src",),
            epoch=2,
            lease_expires_at=200.0,
        ).as_dict(),
        ts=6.0,
        durable=True,
    )
    store.append(
        EventKind.HANDOFF,
        _claim(
            task_id="HANDOFF-BROKEN",
            owner="gamma",
            paths=("docs/broken.md",),
            epoch=3,
            lease_expires_at=30.0,
        ).as_dict(),
        ts=7.0,
        durable=True,
    )
    store.close()


def _graph_from(path: Path, *, as_of: float = 100.0) -> TrustGraph:
    store = EventStore(path)
    try:
        events = list(store.read_all())
    finally:
        store.close()
    return build_trust_graph(events, as_of=as_of)


def test_graph_composes_every_evidence_layer(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    _seed_store(db)
    graph = _graph_from(db)
    kinds = sorted({edge.kind for edge in graph.edges})
    assert kinds == [
        "broken_handoff_candidate",
        "conflict_pair",
        "declared_failed_check",
        "positive_receipt",
        "stale_claim",
    ]
    node_ids = {node.id for node in graph.nodes}
    assert {"agent:alpha", "agent:beta", "agent:gamma", "task:ROUTING", "task:STALE"} <= node_ids
    assert graph.trust_boundary == TRUST_GRAPH_BOUNDARY


def test_conflict_pair_maps_to_exactly_one_edge(tmp_path: Path) -> None:
    # The reliability layer emits two symmetric conflict findings; the graph
    # must keep one agent-to-agent edge naming both tasks.
    db = tmp_path / "events.db"
    _seed_store(db)
    graph = _graph_from(db)
    conflicts = [edge for edge in graph.edges if edge.kind == "conflict_pair"]
    assert len(conflicts) == 1
    (conflict,) = conflicts
    assert {conflict.source, conflict.target} == {"agent:alpha", "agent:beta"}
    assert set(conflict.tasks) == {"OVERLAP-A", "OVERLAP-B"}
    assert conflict.evidence["paths"] == ["src/api.py", "src"]


def test_edges_carry_event_log_provenance_in_seq_order(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    _seed_store(db)
    graph = _graph_from(db)
    assert [edge.seq for edge in graph.edges] == sorted(edge.seq for edge in graph.edges)
    positive = next(edge for edge in graph.edges if edge.kind == "positive_receipt")
    assert positive.source == "agent:alpha"
    assert positive.target == "task:ROUTING"
    assert positive.ts == 2.0
    assert "pytest" in positive.detail
    assert "routing" in positive.evidence["tokens"]


def test_agent_filter_keeps_conflicts_touching_the_agent(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    _seed_store(db)
    graph = _graph_from(db)
    focused = graph_involving(graph, agent="gamma")
    # gamma's expired handoff is both a broken-handoff candidate and (as the
    # live snapshot of that task) a stale claim — two facts, two edges.
    assert {edge.kind for edge in focused.edges} == {"broken_handoff_candidate", "stale_claim"}
    conflict_side = graph_involving(graph, agent="beta")
    assert "conflict_pair" in {edge.kind for edge in conflict_side.edges}
    assert all("agent:beta" in (edge.source, edge.target) for edge in conflict_side.edges)


def test_task_filter_reaches_conflict_edges_through_their_tasks(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    _seed_store(db)
    graph = _graph_from(db)
    focused = graph_involving(graph, task="OVERLAP-B")
    assert {edge.kind for edge in focused.edges} == {"conflict_pair"}
    assert {node.kind for node in focused.nodes} == {"agent", "task"}


def test_since_filter_is_the_decay_window(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    _seed_store(db)
    graph = _graph_from(db)
    recent = graph_involving(graph, since=5.0)
    assert all(edge.ts >= 5.0 for edge in recent.edges)
    assert len(recent.edges) < len(graph.edges)
    nothing = graph_involving(graph, since=1e9)
    assert nothing.edges == ()
    assert nothing.nodes == ()


def test_filters_compose_conjunctively(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    _seed_store(db)
    graph = _graph_from(db)
    focused = graph_involving(graph, agent="alpha", task="ROUTING")
    assert focused.edges
    assert all(edge.target == "task:ROUTING" for edge in focused.edges)
    assert graph_involving(graph, agent="alpha", task="STALE").edges == ()


def test_missing_author_becomes_the_unknown_label(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    store = EventStore(db)
    store.append(
        EventKind.LEDGER_PROGRESS,
        {"task_id": "T", "kind": "note", "text": "checks failed", "posted_at": 1.0},
        ts=1.0,
    )
    store.close()
    graph = _graph_from(db)
    (edge,) = graph.edges
    assert edge.source == f"agent:{UNKNOWN_LABEL}"
    assert any(node.label == UNKNOWN_LABEL for node in graph.nodes)


def test_run_trust_graph_requires_an_existing_store(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="missing event store"):
        run_trust_graph(tmp_path / "absent.db")


def test_run_trust_graph_reads_the_store_once(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    _seed_store(db)
    graph = run_trust_graph(db, as_of=100.0)
    assert graph.edges
    assert graph.generated_from_seq >= max(edge.seq for edge in graph.edges)


def test_json_projection_is_stable_and_labelled(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    _seed_store(db)
    graph = _graph_from(db)
    payload = trust_graph_to_json(graph)
    assert payload["note"] == "evidence graph, not scores"
    assert payload["trust_boundary"] == TRUST_GRAPH_BOUNDARY
    nodes = payload["nodes"]
    edges = payload["edges"]
    assert isinstance(nodes, list) and isinstance(edges, list)
    assert len(nodes) == len(graph.nodes)
    assert len(edges) == len(graph.edges)
    first = edges[0]
    assert set(first) == {"source", "target", "kind", "seq", "ts", "detail", "tasks", "evidence"}


def test_dot_rendering_shapes_nodes_and_dashes_conflicts(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    _seed_store(db)
    dot = render_trust_graph_dot(_graph_from(db))
    assert dot.startswith("digraph trust_graph {")
    assert dot.endswith("}")
    assert '"agent:alpha" [label="alpha", shape=ellipse];' in dot
    assert '"task:ROUTING" [label="ROUTING", shape=box];' in dot
    assert "dir=none, style=dashed" in dot
    assert "conflict_pair seq=" in dot


def test_dot_quoting_escapes_quotes_and_backslashes() -> None:
    assert _dot_quote('a"b') == '"a\\"b"'
    assert _dot_quote("a\\b") == '"a\\\\b"'


def test_empty_graph_renders_honestly(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    EventStore(db).close()
    graph = run_trust_graph(db)
    assert "No evidence edges found." in render_trust_graph_human(graph)
    dot = render_trust_graph_dot(graph)
    assert dot.startswith("digraph trust_graph {")
    assert dot.endswith("}")


def test_human_rendering_lists_every_edge_with_provenance(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    _seed_store(db)
    graph = _graph_from(db)
    text = render_trust_graph_human(graph)
    assert text.startswith("Trust graph: evidence with event-log provenance, not scores")
    assert f"nodes={len(graph.nodes)} edges={len(graph.edges)}" in text
    assert "alpha -[positive_receipt seq=" in text
