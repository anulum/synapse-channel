# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — coordination-causality graph regressions

from __future__ import annotations

from collections.abc import Iterable, Iterator
from pathlib import Path

import pytest

from synapse_channel.core.causality import (
    CONTENTION,
    DEFAULT_MAX_GRAPH_NODES,
    DEPENDENCY,
    GRAPH_KINDS,
    LIFECYCLE,
    CausalEdge,
    build_causal_graph,
    causality_to_json,
    causes,
    counterfactual,
    effects,
    render_markdown,
    run_causality,
)
from synapse_channel.core.journal import EventKind
from synapse_channel.core.path_identity import CanonicalPathIdentity, ClaimScopeIdentity
from synapse_channel.core.persistence import EventStore, StoredEvent


def _claim(
    seq: int,
    task: str,
    owner: str,
    *,
    status: str = "claimed",
    paths: tuple[str, ...] = (),
    worktree: str = "wt1",
    kind: str = EventKind.CLAIM,
    path_identity: dict[str, object] | None = None,
) -> StoredEvent:
    return StoredEvent(
        seq=seq,
        ts=float(seq),
        kind=kind,
        payload={
            "task_id": task,
            "owner": owner,
            "status": status,
            "paths": list(paths),
            "worktree": worktree,
            **({"path_identity": path_identity} if path_identity is not None else {}),
        },
    )


def _release(seq: int, task: str) -> StoredEvent:
    return StoredEvent(seq=seq, ts=float(seq), kind=EventKind.RELEASE, payload={"task_id": task})


def _ledger(seq: int, task: str, *, deps: tuple[str, ...] = (), title: str = "") -> StoredEvent:
    return StoredEvent(
        seq=seq,
        ts=float(seq),
        kind=EventKind.LEDGER_TASK,
        payload={
            "task_id": task,
            "title": title or f"task {task}",
            "depends_on": list(deps),
            "status": "open",
        },
    )


def _chain_events() -> tuple[StoredEvent, ...]:
    """B done & released; A depends on B and is claimed after; C contends A's paths."""
    return (
        _ledger(1, "B"),
        _claim(2, "B", "alice", paths=("src/x",)),
        _claim(3, "B", "alice", status="done", paths=("src/x",), kind=EventKind.TASK_UPDATE),
        _release(4, "B"),
        _ledger(5, "A", deps=("B",)),
        _claim(6, "A", "bob", paths=("src/y",)),
        _claim(7, "A", "bob", status="done", paths=("src/y",), kind=EventKind.TASK_UPDATE),
        _release(8, "A"),
        _claim(9, "C", "carol", paths=("src/y",)),
    )


def _seed(path: Path, events: tuple[StoredEvent, ...]) -> None:
    store = EventStore(path)
    for event in events:
        store.append(event.kind, event.payload, ts=event.ts)
    store.close()


def _edge(edges: tuple[CausalEdge, ...], src: int, dst: int) -> CausalEdge | None:
    return next((edge for edge in edges if edge.src == src and edge.dst == dst), None)


def test_build_graph_records_three_relations() -> None:
    graph = build_causal_graph(_chain_events())
    assert [node.seq for node in graph.nodes] == [1, 2, 3, 4, 5, 6, 7, 8, 9]
    assert graph.generated_from_seq == 9
    lifecycle = _edge(graph.edges, 1, 2)
    assert lifecycle is not None
    assert lifecycle.relation == LIFECYCLE
    dependency = _edge(graph.edges, 4, 6)
    assert dependency is not None
    assert dependency.relation == DEPENDENCY
    contention = _edge(graph.edges, 8, 9)
    assert contention is not None
    assert contention.relation == CONTENTION


def test_contention_edge_uses_canonical_hardlink_identity() -> None:
    first_identity = ClaimScopeIdentity(
        worktree_path="wt1",
        worktree_object_id="root:1",
        filesystem_namespace="host:1",
        case_sensitive=True,
        paths=(CanonicalPathIdentity("owned.py", "owned.py", "1:2"),),
    ).as_dict()
    second_identity = ClaimScopeIdentity(
        worktree_path="wt1",
        worktree_object_id="root:1",
        filesystem_namespace="host:1",
        case_sensitive=True,
        paths=(CanonicalPathIdentity("alias.py", "alias.py", "1:2"),),
    ).as_dict()
    graph = build_causal_graph(
        (
            _claim(1, "A", "alice", paths=("owned.py",), path_identity=first_identity),
            _release(2, "A"),
            _claim(3, "B", "bob", paths=("alias.py",), path_identity=second_identity),
        )
    )
    contention = _edge(graph.edges, 2, 3)
    assert contention is not None
    assert contention.relation == CONTENTION


def test_build_graph_empty_log() -> None:
    graph = build_causal_graph(())
    assert graph.nodes == ()
    assert graph.edges == ()
    assert graph.generated_from_seq == 0


def test_build_graph_ignores_non_graph_kinds_and_taskless_events() -> None:
    events = (
        StoredEvent(seq=1, ts=1.0, kind=EventKind.CHAT, payload={"from": "x", "payload": "hi"}),
        StoredEvent(seq=2, ts=2.0, kind=EventKind.CLAIM, payload={"owner": "bob"}),  # no task_id
        _claim(3, "A", "bob", paths=("src/a",)),
    )
    graph = build_causal_graph(events)
    # The chat is dropped; the task-less claim becomes a node with no lifecycle edge.
    assert {node.seq for node in graph.nodes} == {2, 3}
    assert graph.edges == ()
    # A task-less node renders without a task= field.
    assert "task=" not in render_markdown(causes(graph, 2))


def test_single_event_task_has_no_lifecycle_edge() -> None:
    graph = build_causal_graph((_claim(1, "A", "bob"),))
    assert graph.edges == ()


def test_dependency_edge_requires_completion_before_claim() -> None:
    # B is declared and claimed but never completes before A's claim -> no dependency edge.
    events = (
        _claim(1, "B", "alice", paths=("src/x",)),
        _ledger(2, "A", deps=("B",)),
        _claim(3, "A", "bob", paths=("src/y",)),
    )
    graph = build_causal_graph(events)
    assert not [edge for edge in graph.edges if edge.relation == DEPENDENCY]


def test_dependency_edge_from_status_completion_not_only_release() -> None:
    # B reaches a done status (no release) before A claims -> dependency edge fires.
    events = (
        _claim(1, "B", "alice", paths=("src/x",)),
        _claim(2, "B", "alice", status="done", paths=("src/x",), kind=EventKind.TASK_UPDATE),
        _ledger(3, "A", deps=("B",)),
        _claim(4, "A", "bob", paths=("src/y",)),
    )
    graph = build_causal_graph(events)
    dependency = _edge(graph.edges, 2, 4)
    assert dependency is not None
    assert dependency.relation == DEPENDENCY


def test_dependency_picks_latest_completion() -> None:
    # B completes twice before A claims; the dependency edge uses the latest completion.
    events = (
        _claim(1, "B", "alice", status="done", paths=("src/x",), kind=EventKind.TASK_UPDATE),
        _claim(2, "B", "alice", status="done", paths=("src/x",), kind=EventKind.TASK_UPDATE),
        _ledger(3, "A", deps=("B",)),
        _claim(4, "A", "bob"),
    )
    graph = build_causal_graph(events)
    assert _edge(graph.edges, 2, 4) is not None
    assert _edge(graph.edges, 1, 4) is None


def test_declared_but_never_claimed_task_has_no_dependency_edge() -> None:
    events = (_ledger(1, "X"), _ledger(2, "Y", deps=("X",)))
    graph = build_causal_graph(events)
    assert not [edge for edge in graph.edges if edge.relation == DEPENDENCY]


def test_dependency_on_unknown_task_is_skipped() -> None:
    events = (_ledger(1, "A", deps=("GHOST",)), _claim(2, "A", "bob"))
    graph = build_causal_graph(events)
    assert not [edge for edge in graph.edges if edge.relation == DEPENDENCY]


def test_ledger_without_depends_on_yields_no_dependency() -> None:
    events = (_ledger(1, "A"), _claim(2, "A", "bob"))
    graph = build_causal_graph(events)
    assert not [edge for edge in graph.edges if edge.relation == DEPENDENCY]


def test_task_with_no_ledger_declaration_has_no_dependency() -> None:
    events = (_claim(1, "A", "bob"), _release(2, "A"))
    graph = build_causal_graph(events)
    assert not [edge for edge in graph.edges if edge.relation == DEPENDENCY]


def test_contention_skips_same_owner() -> None:
    events = (
        _claim(1, "B", "alice", paths=("src/y",)),
        _release(2, "B"),
        _claim(3, "C", "alice", paths=("src/y",)),  # same owner -> no contention
    )
    graph = build_causal_graph(events)
    assert not [edge for edge in graph.edges if edge.relation == CONTENTION]


def test_contention_skips_different_worktree() -> None:
    events = (
        _claim(1, "B", "alice", paths=("src/y",), worktree="wtA"),
        _release(2, "B"),
        _claim(3, "C", "carol", paths=("src/y",), worktree="wtB"),
    )
    graph = build_causal_graph(events)
    assert not [edge for edge in graph.edges if edge.relation == CONTENTION]


def test_contention_skips_non_overlapping_paths() -> None:
    events = (
        _claim(1, "B", "alice", paths=("src/x",)),
        _release(2, "B"),
        _claim(3, "C", "carol", paths=("src/y",)),
    )
    graph = build_causal_graph(events)
    assert not [edge for edge in graph.edges if edge.relation == CONTENTION]


def test_contention_with_empty_paths_overlaps_whole_tree() -> None:
    events = (
        _claim(1, "B", "alice", paths=()),  # whole-tree scope
        _release(2, "B"),
        _claim(3, "C", "carol", paths=("src/y",)),
    )
    graph = build_causal_graph(events)
    assert _edge(graph.edges, 2, 3) is not None


def test_contention_uses_latest_overlapping_release() -> None:
    events = (
        _claim(1, "A", "alice", paths=("src/y",)),
        _release(2, "A"),
        _claim(3, "B", "bob", paths=("src/y",)),
        _release(4, "B"),
        _claim(5, "C", "carol", paths=("src/y",)),  # gated by the later release (4), not (2)
    )
    graph = build_causal_graph(events)
    assert _edge(graph.edges, 4, 5) is not None
    assert _edge(graph.edges, 2, 5) is None


def test_renewal_of_live_claim_is_not_contention() -> None:
    # A re-claims its own already-held overlapping paths -> lifecycle, never contention.
    events = (
        _claim(1, "A", "alice", paths=("src/y",)),
        _claim(2, "A", "alice", paths=("src/y",), kind=EventKind.TASK_UPDATE),
    )
    graph = build_causal_graph(events)
    assert not [edge for edge in graph.edges if edge.relation == CONTENTION]
    assert _edge(graph.edges, 1, 2) is not None


def test_release_of_non_live_task_is_ignored() -> None:
    # A release with no matching live claim must not crash or seed a contention source.
    events = (
        _release(1, "GHOST"),
        _claim(2, "C", "carol", paths=("src/y",)),
    )
    graph = build_causal_graph(events)
    assert graph.edges == ()


def test_reach_dedupes_a_node_reached_by_two_edges() -> None:
    # B releases overlapping paths A depends on, so release(3) -> claim(5) is both a
    # dependency edge and a contention edge: traversal must visit the node once.
    events = (
        _claim(1, "B", "alice", paths=("src/y",)),
        _claim(2, "B", "alice", status="done", paths=("src/y",), kind=EventKind.TASK_UPDATE),
        _release(3, "B"),
        _ledger(4, "A", deps=("B",)),
        _claim(5, "A", "bob", paths=("src/y",)),
    )
    graph = build_causal_graph(events)
    parallel = [edge for edge in graph.edges if edge.src == 3 and edge.dst == 5]
    assert {edge.relation for edge in parallel} == {DEPENDENCY, CONTENTION}
    query = effects(graph, 3)
    assert [node.seq for node in query.transitive] == [5]
    assert [link.node.seq for link in query.direct] == [5, 5]


def test_causes_returns_upstream_chain() -> None:
    graph = build_causal_graph(_chain_events())
    query = causes(graph, 6)
    assert query.present is True
    assert query.direction == "causes"
    assert [link.node.seq for link in query.direct] == [4, 5]
    assert [node.seq for node in query.transitive] == [1, 2, 3, 4, 5]
    assert query.unsupported == ()


def test_effects_returns_downstream_chain() -> None:
    graph = build_causal_graph(_chain_events())
    query = effects(graph, 4)
    assert query.direction == "effects"
    assert [link.node.seq for link in query.direct] == [6]
    assert [node.seq for node in query.transitive] == [6, 7, 8, 9]


def test_counterfactual_keeps_independently_supported_nodes() -> None:
    graph = build_causal_graph(_chain_events())
    query = counterfactual(graph, 2)
    # B's own lifecycle (3, 4) loses support; A's claim (6) keeps support from its
    # ledger declaration (5), so 6, 7, 8, 9 survive.
    assert [node.seq for node in query.transitive] == [3, 4, 6, 7, 8, 9]
    assert [node.seq for node in query.unsupported] == [3, 4]


def test_counterfactual_collapses_a_pure_chain() -> None:
    events = (
        _claim(1, "A", "alice", paths=("src/x",)),
        _claim(2, "A", "alice", status="done", paths=("src/x",), kind=EventKind.TASK_UPDATE),
        _release(3, "A"),
    )
    graph = build_causal_graph(events)
    query = counterfactual(graph, 1)
    assert [node.seq for node in query.unsupported] == [2, 3]


def test_query_absent_sequence() -> None:
    graph = build_causal_graph(_chain_events())
    query = causes(graph, 999)
    assert query.present is False
    assert query.node is None
    assert query.direct == ()
    assert query.transitive == ()
    assert query.unsupported == ()


def test_node_with_no_neighbours() -> None:
    graph = build_causal_graph((_claim(1, "A", "bob"),))
    query = effects(graph, 1)
    assert query.present is True
    assert query.direct == ()
    assert query.transitive == ()


def test_run_causality_loads_store(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    _seed(db, _chain_events())
    query = run_causality(db, "effects", 4)
    assert query.present is True
    assert [node.seq for node in query.transitive] == [6, 7, 8, 9]


def test_run_causality_unknown_direction_raises(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    _seed(db, _chain_events())
    with pytest.raises(ValueError, match="unknown direction"):
        run_causality(db, "sideways", 4)


def test_run_causality_missing_store_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="missing event store"):
        run_causality(tmp_path / "absent.db", "causes", 1)


def test_json_present_and_absent() -> None:
    graph = build_causal_graph(_chain_events())
    present = causality_to_json(causes(graph, 6))
    assert present["present"] is True
    assert present["node"] is not None
    assert present["direction"] == "causes"
    direct = present["direct"]
    assert isinstance(direct, list)
    first = direct[0]
    assert isinstance(first, dict)
    assert first["relation"] in {LIFECYCLE, DEPENDENCY, CONTENTION}
    assert "node" in first

    absent = causality_to_json(causes(graph, 999))
    assert absent["present"] is False
    assert absent["node"] is None


def test_render_markdown_absent() -> None:
    graph = build_causal_graph(_chain_events())
    text = render_markdown(causes(graph, 999))
    assert "No coordination event at seq 999" in text


def test_render_markdown_causes_and_effects() -> None:
    graph = build_causal_graph(_chain_events())
    causes_md = render_markdown(causes(graph, 6))
    assert "# Causality (causes): seq 6" in causes_md
    assert "## Direct causes" in causes_md
    assert "[dependency]" in causes_md
    assert "## Transitive" in causes_md

    effects_md = render_markdown(effects(graph, 4))
    assert "## Direct effects" in effects_md


def test_render_markdown_counterfactual_sections() -> None:
    graph = build_causal_graph(_chain_events())
    text = render_markdown(counterfactual(graph, 2))
    assert "# Causality (counterfactual): seq 2" in text
    assert "Loses recorded support" in text
    assert "## Loses recorded support" in text


def test_render_markdown_empty_sections() -> None:
    graph = build_causal_graph((_claim(1, "A", "bob"),))
    text = render_markdown(counterfactual(graph, 1))
    # Direct, transitive, and unsupported are all empty -> each renders "- none".
    assert text.count("- none") == 3


def test_node_text_prefers_title_then_falls_back() -> None:
    titled = build_causal_graph((_ledger(1, "A", title="Build it"), _claim(2, "A", "bob")))
    assert titled.nodes[0].text == "Build it"
    # A claim carries no title/note/text/data_ref here -> empty text.
    assert titled.nodes[1].text == ""


def test_node_summary_includes_owner_and_status() -> None:
    graph = build_causal_graph((_claim(1, "A", "bob", status="blocked"),))
    text = render_markdown(causes(graph, 1))
    assert "owner=bob" in text
    assert "status=blocked" in text
    assert "task=A" in text


def test_run_causality_streams_only_coordination_kinds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The store read is the streaming kind-filtered cursor, never the whole log."""
    db = tmp_path / "hub.db"
    _seed(db, _chain_events())

    def refuse_read_all(self: EventStore) -> list[StoredEvent]:
        msg = "run_causality materialised the log"
        raise AssertionError(msg)

    seen_kinds: set[str] = set()
    original = EventStore.iter_events

    def spying_iter(
        self: EventStore,
        *,
        through_seq: int | None = None,
        kinds: Iterable[str] | None = None,
    ) -> Iterator[StoredEvent]:
        for event in original(self, through_seq=through_seq, kinds=kinds):
            seen_kinds.add(event.kind)
            yield event

    monkeypatch.setattr(EventStore, "read_all", refuse_read_all)
    monkeypatch.setattr(EventStore, "iter_events", spying_iter)
    query = run_causality(db, "effects", 4)
    assert query.present
    assert seen_kinds  # the stream ran
    assert seen_kinds <= GRAPH_KINDS  # nothing outside the graph kinds crossed over


def test_run_causality_rejects_a_log_over_the_node_ceiling(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    _seed(db, _chain_events())
    with pytest.raises(ValueError, match="would exceed 3 coordination events"):
        run_causality(db, "effects", 4, max_nodes=3)


def test_run_causality_lifts_the_ceiling_on_zero_or_none(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    _seed(db, _chain_events())
    assert run_causality(db, "effects", 4, max_nodes=0).present
    assert run_causality(db, "effects", 4, max_nodes=None).present


def test_default_node_ceiling_is_generous() -> None:
    assert DEFAULT_MAX_GRAPH_NODES >= 100_000
