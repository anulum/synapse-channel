# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — causality-to-OpenTelemetry span projection regressions

from __future__ import annotations

from pathlib import Path

import pytest

from synapse_channel.core.causality import CONTENTION, DEPENDENCY
from synapse_channel.core.causality_otel import (
    SERVICE_NAME,
    SPAN_ID_HEX_LENGTH,
    SPAN_STATUS_ERROR,
    SPAN_STATUS_UNSET,
    TRACE_ID_HEX_LENGTH,
    OtelSpanRecord,
    build_otel_projection,
    projection_to_json,
    run_otel_projection,
    span_id_for_event,
    span_id_for_root,
    trace_id_for_task,
)
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore, StoredEvent


def _claim(
    seq: int,
    task: str,
    owner: str,
    *,
    status: str = "claimed",
    paths: tuple[str, ...] = (),
    kind: str = EventKind.CLAIM,
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
            "worktree": "w",
        },
    )


def _release(seq: int, task: str) -> StoredEvent:
    return StoredEvent(seq=seq, ts=float(seq), kind=EventKind.RELEASE, payload={"task_id": task})


def _ledger(seq: int, task: str, *, deps: tuple[str, ...] = ()) -> StoredEvent:
    return StoredEvent(
        seq=seq,
        ts=float(seq),
        kind=EventKind.LEDGER_TASK,
        payload={"task_id": task, "title": f"task {task}", "depends_on": list(deps)},
    )


def _interlocked_events() -> tuple[StoredEvent, ...]:
    """B completes and is released; A depends on B; C's claim contends B's paths."""
    return (
        _ledger(1, "B"),
        _claim(2, "B", "alice", paths=("src/x",)),
        _release(3, "B"),
        _ledger(4, "A", deps=("B",)),
        _claim(5, "A", "bob", paths=("src/y",)),
        _claim(6, "C", "carol", paths=("src/x",)),
    )


def _span(projection_spans: tuple[OtelSpanRecord, ...], span_id: str) -> OtelSpanRecord:
    return next(span for span in projection_spans if span.span_id_hex == span_id)


class TestDeterministicIds:
    def test_trace_and_span_ids_have_otel_lengths(self) -> None:
        assert len(trace_id_for_task("B")) == TRACE_ID_HEX_LENGTH
        assert len(span_id_for_event(7)) == SPAN_ID_HEX_LENGTH
        assert len(span_id_for_root("B")) == SPAN_ID_HEX_LENGTH

    def test_ids_are_deterministic_and_distinct(self) -> None:
        assert trace_id_for_task("B") == trace_id_for_task("B")
        assert trace_id_for_task("B") != trace_id_for_task("A")
        assert span_id_for_event(1) != span_id_for_event(2)
        assert span_id_for_root("B") != span_id_for_event(1)

    def test_reprojecting_the_same_log_yields_identical_records(self) -> None:
        events = _interlocked_events()

        assert build_otel_projection(events) == build_otel_projection(events)


class TestProjection:
    def test_one_trace_per_task_with_a_root_and_event_spans(self) -> None:
        projection = build_otel_projection(_interlocked_events())

        assert projection.trace_count == 3
        root = _span(projection.spans, span_id_for_root("B"))
        assert root.name == "B"
        assert root.parent_span_id_hex == ""
        assert root.trace_id_hex == trace_id_for_task("B")
        assert root.start_ns == 1_000_000_000
        assert root.end_ns == 3_000_000_000
        event = _span(projection.spans, span_id_for_event(2))
        assert event.parent_span_id_hex == root.span_id_hex
        assert event.name == "claim B"
        assert event.start_ns == event.end_ns == 2_000_000_000

    def test_dependency_edge_becomes_a_link_on_the_dependent_claim(self) -> None:
        projection = build_otel_projection(_interlocked_events())

        dependent_claim = _span(projection.spans, span_id_for_event(5))
        relations = {link.relation for link in dependent_claim.links}
        assert DEPENDENCY in relations
        dependency = next(link for link in dependent_claim.links if link.relation == DEPENDENCY)
        assert dependency.trace_id_hex == trace_id_for_task("B")
        assert dependency.span_id_hex == span_id_for_event(3)

    def test_contention_edge_becomes_a_link_on_the_freed_claim(self) -> None:
        projection = build_otel_projection(_interlocked_events())

        freed_claim = _span(projection.spans, span_id_for_event(6))
        contention = next(link for link in freed_claim.links if link.relation == CONTENTION)
        assert contention.span_id_hex == span_id_for_event(3)
        assert "freed by an overlapping release" in contention.detail

    def test_lifecycle_edges_do_not_become_links(self) -> None:
        projection = build_otel_projection(_interlocked_events())

        release_span = _span(projection.spans, span_id_for_event(3))
        assert release_span.links == ()

    def test_taskless_events_are_counted_not_silently_dropped(self) -> None:
        events = (
            _claim(1, "B", "alice"),
            StoredEvent(seq=2, ts=2.0, kind=EventKind.RELEASE, payload={}),
        )

        projection = build_otel_projection(events)

        assert projection.skipped_events == 1
        assert projection.trace_count == 1

    def test_empty_attribute_values_are_omitted_and_pairs_sorted(self) -> None:
        projection = build_otel_projection((_release(1, "B"),))

        span = _span(projection.spans, span_id_for_event(1))
        keys = [key for key, _ in span.attributes]
        assert keys == sorted(keys)
        assert "synapse.owner" not in dict(span.attributes)
        assert dict(span.attributes)["service.name"] == SERVICE_NAME


class TestRunAndJson:
    def _seed(self, path: Path) -> None:
        store = EventStore(path)
        for event in _interlocked_events():
            store.append(event.kind, event.payload, ts=event.ts)
        store.close()

    def test_runs_from_a_persisted_store(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        self._seed(db)

        projection = run_otel_projection(db)

        assert projection.trace_count == 3
        assert len(projection.spans) == 9

    def test_missing_store_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="missing event store"):
            run_otel_projection(tmp_path / "absent.db")

    def test_node_ceiling_is_enforced(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        self._seed(db)

        with pytest.raises(ValueError, match="would exceed 2 coordination events"):
            run_otel_projection(db, max_nodes=2)

    def test_zero_lifts_the_node_ceiling(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        self._seed(db)

        assert run_otel_projection(db, max_nodes=0).trace_count == 3

    def test_json_shape_carries_ids_links_and_counts(self) -> None:
        projection = build_otel_projection(_interlocked_events())

        payload = projection_to_json(projection)

        assert payload["service_name"] == SERVICE_NAME
        assert payload["trace_count"] == 3
        assert payload["skipped_events"] == 0
        spans = payload["spans"]
        assert isinstance(spans, list)
        linked = [span for span in spans if span["links"]]
        assert linked
        link = linked[0]["links"][0]
        assert set(link) == {"trace_id", "span_id", "relation", "detail"}


class TestServiceName:
    def test_override_flows_into_spans_and_json(self) -> None:
        projection = build_otel_projection(_interlocked_events(), service_name="hub-eu")

        assert projection.service_name == "hub-eu"
        assert all(dict(span.attributes)["service.name"] == "hub-eu" for span in projection.spans)
        assert projection_to_json(projection)["service_name"] == "hub-eu"

    def test_default_stays_the_module_constant(self) -> None:
        projection = build_otel_projection(_interlocked_events())

        assert projection.service_name == SERVICE_NAME

    def test_run_threads_the_override_from_a_store(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        store = EventStore(db)
        for event in _interlocked_events():
            store.append(event.kind, event.payload, ts=event.ts)
        store.close()

        projection = run_otel_projection(db, service_name="hub-us")

        assert projection.service_name == "hub-us"


class TestTaskFilter:
    def test_projects_only_the_named_tasks_and_counts_exclusions(self) -> None:
        projection = build_otel_projection(_interlocked_events(), task_filter=["B"])

        assert projection.trace_count == 1
        assert projection.filtered_out_tasks == 2
        assert {span.trace_id_hex for span in projection.spans} == {trace_id_for_task("B")}

    def test_links_into_excluded_tasks_are_kept(self) -> None:
        # C's claim was freed by B's release; filtering to C must keep the
        # contention link — the deterministic ids resolve against any export
        # that carried B's trace, so dropping it would be silent truncation.
        projection = build_otel_projection(_interlocked_events(), task_filter=["C"])

        freed_claim = _span(projection.spans, span_id_for_event(6))
        contention = next(link for link in freed_claim.links if link.relation == CONTENTION)
        assert contention.trace_id_hex == trace_id_for_task("B")
        assert contention.span_id_hex == span_id_for_event(3)

    def test_unrecorded_task_is_refused(self) -> None:
        with pytest.raises(ValueError, match="task\\(s\\) not recorded in the log: NOPE, ZERO"):
            build_otel_projection(_interlocked_events(), task_filter=["NOPE", "B", "ZERO"])

    def test_no_filter_reports_zero_filtered_out(self) -> None:
        projection = build_otel_projection(_interlocked_events())

        assert projection.filtered_out_tasks == 0
        assert projection_to_json(projection)["filtered_out_tasks"] == 0

    def test_run_threads_the_filter_from_a_store(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        store = EventStore(db)
        for event in _interlocked_events():
            store.append(event.kind, event.payload, ts=event.ts)
        store.close()

        projection = run_otel_projection(db, task_filter=["A", "B"])

        assert projection.trace_count == 2
        assert projection.filtered_out_tasks == 1


class TestSpanStatus:
    def test_failure_terminal_projects_error_on_event_and_root(self) -> None:
        events = (
            _claim(1, "B", "alice"),
            _claim(2, "B", "alice", status="failed", kind=EventKind.TASK_UPDATE),
        )

        projection = build_otel_projection(events)

        assert _span(projection.spans, span_id_for_event(2)).status == SPAN_STATUS_ERROR
        assert _span(projection.spans, span_id_for_root("B")).status == SPAN_STATUS_ERROR

    def test_progress_and_completion_stay_unset(self) -> None:
        projection = build_otel_projection(_interlocked_events())

        assert all(span.status == SPAN_STATUS_UNSET for span in projection.spans)

    def test_failed_midway_but_recovered_root_stays_unset(self) -> None:
        # only the FINAL recorded status marks the root; the failed event span
        # itself still carries ERROR
        events = (
            _claim(1, "B", "alice", status="failed", kind=EventKind.TASK_UPDATE),
            _claim(2, "B", "alice", status="done", kind=EventKind.TASK_UPDATE),
        )

        projection = build_otel_projection(events)

        assert _span(projection.spans, span_id_for_event(1)).status == SPAN_STATUS_ERROR
        assert _span(projection.spans, span_id_for_root("B")).status == SPAN_STATUS_UNSET

    def test_json_carries_the_status(self) -> None:
        events = (_claim(1, "B", "alice", status="failed", kind=EventKind.TASK_UPDATE),)

        payload = projection_to_json(build_otel_projection(events))

        spans = payload["spans"]
        assert isinstance(spans, list)
        statuses = {span["span_id"]: span["status"] for span in spans}
        assert statuses[span_id_for_event(1)] == SPAN_STATUS_ERROR


def test_link_whose_source_has_no_task_is_dropped() -> None:
    """A contention edge from a taskless release cannot resolve to any trace."""
    events = (
        _claim(1, "", "alice", paths=("src/x",)),
        StoredEvent(seq=2, ts=2.0, kind=EventKind.RELEASE, payload={"task_id": ""}),
        _claim(3, "C", "carol", paths=("src/x",)),
    )

    projection = build_otel_projection(events)

    freed = next(span for span in projection.spans if span.span_id_hex == span_id_for_event(3))
    assert freed.links == ()
    assert projection.skipped_events == 2
