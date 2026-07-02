# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — cross-hub coordination-causality regressions

from __future__ import annotations

from pathlib import Path

import pytest

from synapse_channel.core.causality import CONTENTION, DEPENDENCY, LIFECYCLE
from synapse_channel.core.causality_federation import (
    FEDERATION,
    FederatedEdge,
    HubEventRef,
    build_federated_graph,
    federated_query,
    federated_to_json,
    parse_hub_ref,
    render_federated_dot,
    render_federated_markdown,
    run_federated_causality,
)
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore, StoredEvent


def _claim(
    seq: int,
    ts: float,
    task: str,
    owner: str,
    *,
    status: str = "claimed",
    paths: tuple[str, ...] = (),
    worktree: str = "w",
    kind: str = EventKind.CLAIM,
) -> StoredEvent:
    return StoredEvent(
        seq=seq,
        ts=ts,
        kind=kind,
        payload={
            "task_id": task,
            "owner": owner,
            "status": status,
            "paths": list(paths),
            "worktree": worktree,
        },
    )


def _release(seq: int, ts: float, task: str) -> StoredEvent:
    return StoredEvent(seq=seq, ts=ts, kind=EventKind.RELEASE, payload={"task_id": task})


def _ledger(seq: int, ts: float, task: str, *, deps: tuple[str, ...] = ()) -> StoredEvent:
    return StoredEvent(
        seq=seq,
        ts=ts,
        kind=EventKind.LEDGER_TASK,
        payload={"task_id": task, "title": f"task {task}", "depends_on": list(deps)},
    )


def _federated_logs() -> dict[str, tuple[StoredEvent, ...]]:
    """Two hubs whose logs interlock across every recorded relation.

    hub-a completes task B (claimed on ``src/x``) and releases it. hub-b then
    declares A depending on B and claims it (cross-hub dependency), claims D on
    B's freed ``src/x`` paths (cross-hub contention), and updates B itself
    (cross-hub lifecycle). hub-b's own release of A frees C (same-hub
    contention), keeping single-hub relations present beside the federated ones.
    """
    hub_a = (
        _ledger(1, 1.0, "B"),
        _claim(2, 2.0, "B", "alice", paths=("src/x",)),
        _claim(3, 3.0, "B", "alice", status="done", paths=("src/x",), kind=EventKind.TASK_UPDATE),
        _release(4, 4.0, "B"),
    )
    hub_b = (
        _ledger(1, 5.0, "A", deps=("B",)),
        _claim(2, 6.0, "A", "bob", paths=("src/y",)),
        _claim(3, 6.5, "D", "dave", paths=("src/x",)),
        _claim(
            4, 6.8, "B", "erin", status="observed", paths=("src/x",), kind=EventKind.TASK_UPDATE
        ),
        _release(5, 7.0, "A"),
        _claim(6, 8.0, "C", "carol", paths=("src/y",)),
    )
    return {"hub-a": hub_a, "hub-b": hub_b}


def _edge_index(
    graph_edges: tuple[FederatedEdge, ...], relation: str, basis: str
) -> list[tuple[str, str]]:
    """Return the ``(src, dst)`` rendered refs of edges matching a relation/basis pair."""
    return [
        (edge.src.render(), edge.dst.render())
        for edge in graph_edges
        if edge.relation == relation and edge.basis == basis
    ]


class TestFederatedGraph:
    def test_nodes_follow_the_merged_total_order_with_global_identities(self) -> None:
        graph = build_federated_graph(_federated_logs())

        assert graph.hubs == ("hub-a", "hub-b")
        assert [node.ref.render() for node in graph.nodes] == [
            "hub-a:1",
            "hub-a:2",
            "hub-a:3",
            "hub-a:4",
            "hub-b:1",
            "hub-b:2",
            "hub-b:3",
            "hub-b:4",
            "hub-b:5",
            "hub-b:6",
        ]

    def test_cross_hub_dependency_edge_is_tagged_federation(self) -> None:
        graph = build_federated_graph(_federated_logs())

        assert ("hub-a:4", "hub-b:2") in _edge_index(graph.edges, FEDERATION, DEPENDENCY)

    def test_cross_hub_contention_edge_is_tagged_federation(self) -> None:
        graph = build_federated_graph(_federated_logs())

        assert ("hub-a:4", "hub-b:3") in _edge_index(graph.edges, FEDERATION, CONTENTION)

    def test_cross_hub_lifecycle_edge_is_tagged_federation(self) -> None:
        graph = build_federated_graph(_federated_logs())

        assert ("hub-a:4", "hub-b:4") in _edge_index(graph.edges, FEDERATION, LIFECYCLE)

    def test_same_hub_edges_keep_their_recorded_relation(self) -> None:
        graph = build_federated_graph(_federated_logs())

        assert ("hub-b:5", "hub-b:6") in _edge_index(graph.edges, CONTENTION, CONTENTION)
        assert ("hub-a:1", "hub-a:2") in _edge_index(graph.edges, LIFECYCLE, LIFECYCLE)

    def test_timestamp_ties_break_by_hub_id_then_seq(self) -> None:
        logs = {
            "beta": (_claim(1, 1.0, "T1", "a"),),
            "alpha": (_claim(1, 1.0, "T2", "b"), _claim(2, 1.0, "T3", "c")),
        }

        graph = build_federated_graph(logs)

        assert [node.ref.render() for node in graph.nodes] == ["alpha:1", "alpha:2", "beta:1"]

    def test_duplicate_events_within_one_log_collapse_to_one_node(self) -> None:
        event = _claim(1, 1.0, "T", "a")

        graph = build_federated_graph({"hub-a": (event, event)})

        assert len(graph.nodes) == 1


class TestFederatedQuery:
    def test_causes_of_a_dependent_claim_cross_the_hub_boundary(self) -> None:
        query = federated_query(_federated_logs(), "causes", HubEventRef("hub-b", 2))

        assert query.present is True
        assert query.hubs == ("hub-a", "hub-b")
        federated = [link for link in query.direct if link.edge.relation == FEDERATION]
        assert [link.node.ref.render() for link in federated] == ["hub-a:4"]
        assert {node.ref.render() for node in query.transitive} >= {"hub-a:2", "hub-a:4"}

    def test_effects_of_a_release_reach_the_peer_hub(self) -> None:
        query = federated_query(_federated_logs(), "effects", HubEventRef("hub-a", 4))

        reached = {node.ref.render() for node in query.transitive}
        assert {"hub-b:2", "hub-b:3", "hub-b:4"} <= reached

    def test_counterfactual_collapses_descendants_without_independent_support(self) -> None:
        logs = {
            "hub-a": (
                _ledger(1, 1.0, "B"),
                _claim(2, 2.0, "B", "alice", paths=("src/x",)),
                _release(3, 3.0, "B"),
            ),
            "hub-b": (_claim(1, 4.0, "D", "dave", paths=("src/x",)),),
        }

        query = federated_query(logs, "counterfactual", HubEventRef("hub-a", 3))

        assert [node.ref.render() for node in query.unsupported] == ["hub-b:1"]

    def test_absent_reference_reports_not_present(self) -> None:
        query = federated_query(_federated_logs(), "causes", HubEventRef("hub-a", 999))

        assert query.present is False
        assert query.node is None
        assert query.direct == ()

    def test_unknown_hub_in_the_reference_is_refused(self) -> None:
        with pytest.raises(ValueError, match="unknown hub 'hub-x'"):
            federated_query(_federated_logs(), "causes", HubEventRef("hub-x", 1))

    def test_unknown_direction_is_refused(self) -> None:
        with pytest.raises(ValueError, match="unknown direction 'sideways'"):
            federated_query(_federated_logs(), "sideways", HubEventRef("hub-a", 1))


class TestParseHubRef:
    def test_plain_integer_resolves_against_the_default_hub(self) -> None:
        assert parse_hub_ref("42", "primary") == HubEventRef("primary", 42)

    def test_hub_qualified_reference_names_the_hub_explicitly(self) -> None:
        assert parse_hub_ref("peer:7", "primary") == HubEventRef("peer", 7)

    def test_hub_ids_containing_colons_split_on_the_last_one(self) -> None:
        assert parse_hub_ref("ws:8876:7", "primary") == HubEventRef("ws:8876", 7)

    def test_non_integer_sequence_is_refused(self) -> None:
        with pytest.raises(ValueError, match="expected SEQ or HUB:SEQ"):
            parse_hub_ref("peer:abc", "primary")

    def test_empty_hub_part_is_refused(self) -> None:
        with pytest.raises(ValueError, match="hub part before ':' is empty"):
            parse_hub_ref(":5", "primary")


class TestRunFederatedCausality:
    def _seed(self, path: Path, events: tuple[StoredEvent, ...]) -> None:
        store = EventStore(path)
        for event in events:
            store.append(event.kind, event.payload, ts=event.ts)
        store.close()

    def _stores(self, tmp_path: Path) -> dict[str, Path]:
        logs = _federated_logs()
        paths = {hub: tmp_path / f"{hub}.db" for hub in logs}
        for hub, events in logs.items():
            self._seed(paths[hub], events)
        return paths

    def test_answers_a_cross_hub_query_from_persisted_stores(self, tmp_path: Path) -> None:
        query = run_federated_causality(self._stores(tmp_path), "causes", HubEventRef("hub-b", 2))

        assert query.present is True
        assert any(link.edge.relation == FEDERATION for link in query.direct)

    def test_missing_store_is_refused_with_its_hub_named(self, tmp_path: Path) -> None:
        stores = self._stores(tmp_path)
        stores["hub-b"] = tmp_path / "absent.db"

        with pytest.raises(ValueError, match="missing event store for hub 'hub-b'"):
            run_federated_causality(stores, "causes", HubEventRef("hub-a", 1))

    def test_node_ceiling_bounds_the_whole_union(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="would exceed 3 coordination events"):
            run_federated_causality(
                self._stores(tmp_path), "causes", HubEventRef("hub-a", 1), max_nodes=3
            )

    def test_zero_lifts_the_node_ceiling(self, tmp_path: Path) -> None:
        query = run_federated_causality(
            self._stores(tmp_path), "causes", HubEventRef("hub-a", 1), max_nodes=0
        )

        assert query.present is True


class TestRenderings:
    def test_json_carries_identities_relations_and_bases(self) -> None:
        query = federated_query(_federated_logs(), "causes", HubEventRef("hub-b", 2))

        payload = federated_to_json(query)

        assert payload["ref"] == {"hub_id": "hub-b", "seq": 2}
        assert payload["hubs"] == ["hub-a", "hub-b"]
        node = payload["node"]
        assert isinstance(node, dict)
        assert node["hub_id"] == "hub-b"
        direct = payload["direct"]
        assert isinstance(direct, list)
        federated = [link for link in direct if link["relation"] == FEDERATION]
        assert federated
        assert federated[0]["basis"] == DEPENDENCY
        assert federated[0]["src"] == {"hub_id": "hub-a", "seq": 4}
        assert federated[0]["dst"] == {"hub_id": "hub-b", "seq": 2}

    def test_json_of_an_absent_reference_has_a_null_node(self) -> None:
        query = federated_query(_federated_logs(), "causes", HubEventRef("hub-a", 999))

        payload = federated_to_json(query)

        assert payload["present"] is False
        assert payload["node"] is None

    def test_markdown_exposes_a_cross_hub_edge_and_its_basis(self) -> None:
        query = federated_query(_federated_logs(), "causes", HubEventRef("hub-b", 2))

        text = render_federated_markdown(query)

        assert "# Federated causality (causes): hub-b:2" in text
        assert "- Hubs: hub-a, hub-b" in text
        assert f"[{FEDERATION}:{DEPENDENCY}] hub-a:4" in text

    def test_markdown_of_an_absent_reference_names_the_merged_hubs(self) -> None:
        query = federated_query(_federated_logs(), "causes", HubEventRef("hub-a", 999))

        text = render_federated_markdown(query)

        assert "No coordination event at hub-a:999 across hubs hub-a, hub-b." in text

    def test_markdown_counterfactual_lists_the_lost_support(self) -> None:
        logs = {
            "hub-a": (
                _claim(1, 1.0, "B", "alice", paths=("src/x",)),
                _release(2, 2.0, "B"),
            ),
            "hub-b": (_claim(1, 3.0, "D", "dave", paths=("src/x",)),),
        }

        text = render_federated_markdown(
            federated_query(logs, "counterfactual", HubEventRef("hub-a", 2))
        )

        assert "## Loses recorded support" in text
        assert "hub-b:1" in text

    def test_markdown_counterfactual_with_no_lost_support_says_none(self) -> None:
        query = federated_query(_federated_logs(), "counterfactual", HubEventRef("hub-b", 6))

        text = render_federated_markdown(query)

        assert "- Loses recorded support: 0" in text
        assert text.rstrip().endswith("- none")

    def test_markdown_of_a_leaf_event_renders_empty_sections(self) -> None:
        logs = {"hub-a": (_claim(1, 1.0, "B", "alice"),)}

        text = render_federated_markdown(federated_query(logs, "causes", HubEventRef("hub-a", 1)))

        assert "## Direct causes\n- none" in text
        assert "## Transitive\n- none" in text


class TestInducedEdges:
    def test_query_carries_the_induced_subgraph_edges(self) -> None:
        query = federated_query(_federated_logs(), "causes", HubEventRef("hub-b", 2))

        rendered = {(edge.src.render(), edge.dst.render()) for edge in query.edges}
        # the cross-hub dependency into the queried claim AND the same-hub
        # lifecycle chain among its hub-a ancestors — edges `transitive` alone
        # could never carry
        assert ("hub-a:4", "hub-b:2") in rendered
        assert ("hub-a:2", "hub-a:3") in rendered

    def test_induced_edges_exclude_nodes_outside_the_answer(self) -> None:
        query = federated_query(_federated_logs(), "causes", HubEventRef("hub-b", 2))

        members = {query.ref.render()} | {node.ref.render() for node in query.transitive}
        for edge in query.edges:
            assert edge.src.render() in members
            assert edge.dst.render() in members

    def test_json_carries_the_induced_edges(self) -> None:
        payload = federated_to_json(
            federated_query(_federated_logs(), "causes", HubEventRef("hub-b", 2))
        )

        edges = payload["edges"]
        assert isinstance(edges, list)
        assert {"relation", "basis", "src", "dst", "detail"} == set(edges[0])

    def test_absent_reference_has_no_induced_edges(self) -> None:
        query = federated_query(_federated_logs(), "causes", HubEventRef("hub-a", 999))

        assert query.edges == ()


class TestDotRendering:
    def test_hubs_render_as_clusters_and_the_queried_node_is_marked(self) -> None:
        query = federated_query(_federated_logs(), "causes", HubEventRef("hub-b", 2))

        dot = render_federated_dot(query)

        assert dot.startswith("digraph federated_causality {")
        assert 'label="hub-a";' in dot
        assert 'label="hub-b";' in dot
        assert "subgraph cluster_0 {" in dot
        assert "subgraph cluster_1 {" in dot
        assert '"hub-b:2" [label="hub-b:2\\nclaim A", shape=box, peripheries=2];' in dot

    def test_federation_edges_are_coloured_and_carry_their_basis(self) -> None:
        query = federated_query(_federated_logs(), "causes", HubEventRef("hub-b", 2))

        dot = render_federated_dot(query)

        assert f'"hub-a:4" -> "hub-b:2" [label="{FEDERATION}:{DEPENDENCY}", color=blue];' in dot

    def test_same_hub_edges_stay_plain(self) -> None:
        query = federated_query(_federated_logs(), "causes", HubEventRef("hub-b", 2))

        dot = render_federated_dot(query)

        same_hub_line = next(line for line in dot.splitlines() if '"hub-a:2" -> "hub-a:3"' in line)
        assert same_hub_line == f'  "hub-a:2" -> "hub-a:3" [label="{LIFECYCLE}"];'

    def test_counterfactual_unsupported_nodes_are_dashed(self) -> None:
        logs = {
            "hub-a": (
                _claim(1, 1.0, "B", "alice", paths=("src/x",)),
                _release(2, 2.0, "B"),
            ),
            "hub-b": (_claim(1, 3.0, "D", "dave", paths=("src/x",)),),
        }

        dot = render_federated_dot(federated_query(logs, "counterfactual", HubEventRef("hub-a", 2)))

        unsupported_line = next(line for line in dot.splitlines() if '"hub-b:1" [' in line)
        assert "style=dashed" in unsupported_line

    def test_taskless_node_labels_carry_no_task_suffix(self) -> None:
        # a release recorded without a task id still frees an overlapping
        # claim; its node renders with kind only
        logs = {
            "hub-a": (
                _claim(1, 1.0, "", "alice", paths=("src/x",)),
                StoredEvent(seq=2, ts=2.0, kind=EventKind.RELEASE, payload={"task_id": ""}),
            ),
            "hub-b": (_claim(1, 3.0, "D", "dave", paths=("src/x",)),),
        }

        dot = render_federated_dot(federated_query(logs, "causes", HubEventRef("hub-b", 1)))

        assert '"hub-a:2" [label="hub-a:2\\nrelease", shape=box];' in dot

    def test_absent_reference_renders_a_placeholder_digraph(self) -> None:
        query = federated_query(_federated_logs(), "causes", HubEventRef("hub-a", 999))

        dot = render_federated_dot(query)

        assert '"absent" [label="no such event"];' in dot
        assert dot.rstrip().endswith("}")

    def test_hub_without_answer_members_renders_no_cluster(self) -> None:
        # a leaf event on hub-a only: hub-b contributes nothing to the answer,
        # so no empty cluster is emitted for it
        logs = {
            "hub-a": (_claim(1, 1.0, "B", "alice"),),
            "hub-b": (_claim(1, 2.0, "Z", "zoe"),),
        }

        dot = render_federated_dot(federated_query(logs, "causes", HubEventRef("hub-a", 1)))

        assert 'label="hub-a";' in dot
        assert 'label="hub-b";' not in dot
