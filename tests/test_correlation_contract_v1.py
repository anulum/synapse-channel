# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — correlation contract v1 over real journal rows
"""One task trace from model call to verified operation, held to the frozen contract."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from synapse_channel.core.accounting import USAGE_NOTE_KIND, format_usage_note
from synapse_channel.core.causality_otel import (
    projection_to_json,
    run_otel_projection,
    span_id_for_event,
    span_id_for_root,
    trace_id_for_task,
)
from synapse_channel.core.causality_otel_correlation import point_span
from synapse_channel.core.correlation_contract import (
    CONTRACT_RESOURCE_ATTRIBUTE,
    CORRELATION_CONTRACT,
    CORRELATION_CONTRACT_V1,
    FAMILY_MODEL_CALL,
    FAMILY_OPERATION,
    FAMILY_OUTCOME,
    FAMILY_POLICY_DECISION,
    FAMILY_VERIFICATION,
    contract_mismatches,
)
from synapse_channel.core.correlation_points import (
    CorrelationPoint,
    CorrelationPoints,
    correlation_points,
)
from synapse_channel.core.execution_outcome import ExecutionOutcome, OutcomeState
from synapse_channel.core.journal import EventKind, record_claim_denial
from synapse_channel.core.operation_records import decode_operation_record, record_operation
from synapse_channel.core.persistence import EventStore, StoredEvent
from synapse_channel.core.result_verification_record import ResultVerification

SHA = "c" * 64
PATH = "out/secret-location.txt"
OBSERVATION = "content_matches_private_note"
DOCS = Path(__file__).resolve().parents[1] / "docs" / "observability.md"


def _verification(effect: str, result: str) -> dict[str, object]:
    return ResultVerification(
        "op-1", "file_change", "confirmed_done", effect, result, OBSERVATION, {"sha256": SHA}
    ).to_json()


def _record(store: EventStore, frame: dict[str, Any]) -> int:
    """Journal one operation record exactly as the hub does after accepting it."""
    request = decode_operation_record({"task_id": "T", "operation_id": "op-1", **frame})
    return record_operation(store, request, sender="P/worker", snapshot_epoch=3)


def _hub(path: Path) -> Path:
    """Write one claimed task with a model call, a denial and a verified operation."""
    store = EventStore(path)
    try:
        store.append(EventKind.CLAIM, {"task_id": "T", "owner": "P/worker", "status": "claimed"})
        store.append(
            EventKind.LEDGER_PROGRESS,
            {
                "task_id": "T",
                "kind": USAGE_NOTE_KIND,
                "author": "P/worker",
                "text": format_usage_note(
                    model="m-1", calls=1, input_tokens=12, output_tokens=0, cost=0.5
                ),
            },
        )
        record_claim_denial(
            store, claimant="Q/other", task_id="T", reason_code="scope", worktree="/w", paths=["a"]
        )
        _record(
            store,
            {
                "record": "declare",
                "epoch": 3,
                "effect": "file",
                "effect_target": {"path": PATH, "sha256": SHA},
                "budget_remaining": 0,
            },
        )
        outcome = ExecutionOutcome(
            "op-1", "worker_process", OutcomeState.CONFIRMED_DONE, OBSERVATION
        ).to_json()
        _record(store, {"record": "outcome", "report": outcome})
        for effect, result in (("absent", "not_verified"), ("observed", "verified")):
            _record(
                store, {"record": "verification", "verification": _verification(effect, result)}
            )
    finally:
        store.close()
    return path


def _events(path: Path) -> list[StoredEvent]:
    store = EventStore(path)
    try:
        return list(store.iter_events())
    finally:
        store.close()


def _points(path: Path) -> CorrelationPoints:
    return correlation_points(_events(path), ["T"])


def test_one_trace_runs_from_model_call_to_verified_operation(tmp_path: Path) -> None:
    """All five families join T's trace, each conforming exactly to contract v1."""
    points = _points(_hub(tmp_path / "hub.db"))
    assert (points.uncorrelated, points.filtered_out, points.unreadable) == (0, 0, 0)
    trace = points.by_task["T"]
    assert [point.family for point in trace] == [
        FAMILY_MODEL_CALL,
        FAMILY_POLICY_DECISION,
        FAMILY_OPERATION,
        FAMILY_OUTCOME,
        FAMILY_VERIFICATION,
        FAMILY_VERIFICATION,
    ]
    for point in trace:
        span = point_span(point, "hub-a")
        assert contract_mismatches(point.family, dict(span.attributes)) == ()
        assert span.trace_id_hex == trace_id_for_task("T")
        assert span.start_ns <= span.end_ns
    declaration = trace[2]
    assert declaration.name == "operation op-1"
    assert dict(declaration.attributes)["synapse.operation.budget_remaining"] == 0
    assert point_span(declaration, "hub-a").parent_span_id_hex == span_id_for_root("T")
    assert declaration.end_ts == max(point.start_ts for point in trace[2:])
    for child in trace[3:]:
        assert child.parent_seq == declaration.seq
        assert point_span(child, "hub-a").parent_span_id_hex == span_id_for_event(declaration.seq)
    assert [point.name for point in trace[3:]] == [
        "operation outcome confirmed_done",
        "operation verified absent not_verified",
        "operation verified observed verified",
    ]
    model = dict(trace[0].attributes)
    assert (model["gen_ai.usage.output_tokens"], model["synapse.usage.cost_usd"]) == (0, 0.5)


def test_targets_observations_and_digests_never_reach_a_span(tmp_path: Path) -> None:
    """The journal rows carry the target, observation and digest; no span value does."""
    path = _hub(tmp_path / "hub.db")
    journal = repr([event.payload for event in _events(path)])
    assert PATH in journal and SHA in journal and OBSERVATION in journal
    values = [
        str(value) for point in _points(path).by_task["T"] for _key, value in point.attributes
    ]
    assert not [value for value in values if value in {PATH, SHA, OBSERVATION}]


def _write(path: Path, rows: list[tuple[str, dict[str, object], float]]) -> list[StoredEvent]:
    store = EventStore(path)
    try:
        store.append(EventKind.CLAIM, {"task_id": "T", "owner": "P/worker"}, ts=100.0)
        store.append(EventKind.CLAIM, {"task_id": "U", "owner": "P/worker"}, ts=100.0)
        for kind, payload, ts in rows:
            store.append(kind, payload, ts=ts)
        return list(store.iter_events())
    finally:
        store.close()


def _row(kind: str, **fields: object) -> dict[str, object]:
    payload: dict[str, object] = {"task_id": "T", "operation_id": "op-1", "sender": "P/worker"}
    if kind == EventKind.OPERATION_DECLARED:
        payload.update(effect="file", snapshot_epoch=3)
    elif kind == EventKind.OPERATION_OUTCOME:
        payload.update(state="unknown", boundary="worker_process", external_effect="unknown")
    else:
        payload.update(
            verifier="file_change", communication="unknown", effect="unknown", result="not_checked"
        )
    payload.update(fields)
    return payload


def test_a_child_without_its_declaration_hangs_from_the_task_root(tmp_path: Path) -> None:
    """Parents are found by task and operation; an early child does not end its parent early."""
    declared, outcome = EventKind.OPERATION_DECLARED, EventKind.OPERATION_OUTCOME
    events = _write(
        tmp_path / "hub.db",
        [
            (outcome, _row(outcome, operation_id="op-x"), 110.0),
            (declared, _row(declared), 200.0),
            (outcome, _row(outcome), 150.0),
            (declared, _row(declared), 300.0),
            (outcome, _row(outcome, task_id="U"), 400.0),
        ],
    )
    trace = correlation_points(events, ["T", "U"]).by_task
    orphan, first, child, second = trace["T"]
    assert orphan.parent_seq is None
    assert child.parent_seq == first.seq
    assert (first.start_ts, first.end_ts) == (200.0, 200.0)
    assert (second.parent_seq, second.end_ts) == (None, 300.0)
    assert trace["U"][0].parent_seq is None


@pytest.mark.parametrize(
    ("kind", "changes"),
    [
        (EventKind.OPERATION_DECLARED, {"sender": ""}),
        (EventKind.OPERATION_DECLARED, {"snapshot_epoch": "3"}),
        (EventKind.OPERATION_DECLARED, {"snapshot_epoch": True}),
        (EventKind.OPERATION_DECLARED, {"budget_remaining": -1}),
        (EventKind.OPERATION_DECLARED, {"operation_id": 7}),
        (EventKind.OPERATION_OUTCOME, {"state": None}),
        (EventKind.OPERATION_VERIFIED, {"result": ""}),
        (EventKind.OPERATION_VERIFIED, {"task_id": ""}),
    ],
)
def test_an_operation_row_missing_a_hub_field_is_counted_unreadable(
    tmp_path: Path, kind: str, changes: dict[str, object]
) -> None:
    """A row the contract cannot read emits nothing and is counted."""
    events = _write(tmp_path / "hub.db", [(kind, _row(kind, **changes), 120.0)])
    points = correlation_points(events, ["T"])
    assert (points.by_task, points.unreadable) == ({}, 1)


def test_an_unparsable_usage_note_is_counted_and_other_notes_are_not(tmp_path: Path) -> None:
    """Only a usage-kind note that cannot be parsed is unreadable."""
    progress = EventKind.LEDGER_PROGRESS
    events = _write(
        tmp_path / "hub.db",
        [
            (progress, {"task_id": "T", "kind": USAGE_NOTE_KIND, "text": "usage calls=1"}, 120.0),
            (progress, {"task_id": "T", "kind": "note", "text": "plain"}, 121.0),
        ],
    )
    points = correlation_points(events, ["T"])
    assert (points.by_task, points.unreadable) == ({}, 1)


def test_operation_records_of_other_tasks_are_counted_apart(tmp_path: Path) -> None:
    """A filtered task's rows and an unrecorded task's rows are never silently dropped."""
    declared = EventKind.OPERATION_DECLARED
    events = _write(
        tmp_path / "hub.db",
        [
            (declared, _row(declared, task_id="U"), 120.0),
            (declared, _row(declared, task_id="GHOST", operation_id="op-2"), 121.0),
        ],
    )
    points = correlation_points(events, ["T"], recorded_task_ids=["T", "U"])
    assert (points.by_task, points.filtered_out, points.uncorrelated) == ({}, 1, 1)


def test_contract_mismatches_names_every_departure() -> None:
    """Unknown families and keys, missing required keys and wrong types are named."""
    assert contract_mismatches("nope", {}) == ("nope is not a family of synapse.correlation.v1",)
    point = CorrelationPoint(
        "T", 5, FAMILY_OPERATION, "operation op", 1.0, 1.0, None, (("synapse.extra", "x"),)
    )
    found = contract_mismatches(FAMILY_OPERATION, dict(point_span(point, "s").attributes))
    assert "synapse.extra is not part of the operation family" in found
    assert "synapse.operation.id is required for the operation family" in found
    typed = {
        "service.name": "s",
        "synapse.kind": "k",
        "synapse.seq": 1,
        "synapse.task_id": "T",
        "gen_ai.request.model": "m",
        "gen_ai.usage.input_tokens": True,
        "gen_ai.usage.output_tokens": 1.0,
        "synapse.usage.calls": 1,
        "synapse.usage.cost_usd": 1,
        "synapse.agent": None,
    }
    assert contract_mismatches(FAMILY_MODEL_CALL, typed) == (
        "gen_ai.usage.input_tokens must be int",
        "gen_ai.usage.output_tokens must be int",
        "synapse.agent must be string",
        "synapse.seq must be string",
        "synapse.usage.cost_usd must be double",
    )


def _documented_contract() -> dict[str, set[tuple[str, str, bool]]]:
    """Read the attribute table of the contract section in the observability guide."""
    text = DOCS.read_text(encoding="utf-8")
    section = text.split("### Correlation contract v1", 1)[1].split("\n## ", 1)[0]
    row = re.compile(r"^\| (every|`(\w+)`) \| `([\w.]+)` \| (string|int|double) \| (yes|no) \|$")
    common: set[tuple[str, str, bool]] = set()
    families: dict[str, set[tuple[str, str, bool]]] = {}
    for line in section.splitlines():
        match = row.match(line)
        if match is None:
            continue
        entry = (match.group(3), match.group(4), match.group(5) == "yes")
        if match.group(2) is None:
            common.add(entry)
        else:
            families.setdefault(match.group(2), set()).add(entry)
    return {family: rows | common for family, rows in families.items()}


def test_the_documented_table_is_the_frozen_contract() -> None:
    """The public table and the code constant name the same keys, types and presence."""
    frozen = {
        family: {(row.key, row.kind, row.required) for row in rows}
        for family, rows in CORRELATION_CONTRACT_V1.items()
    }
    assert _documented_contract() == frozen


def test_the_contract_is_named_in_the_document_and_on_the_resource(tmp_path: Path) -> None:
    """A correlated projection names its contract; the operation spans are in it."""
    projection = run_otel_projection(_hub(tmp_path / "hub.db"), correlate=True)
    document = projection_to_json(projection)
    assert document["correlation_contract"] == CORRELATION_CONTRACT
    assert document["unreadable_events"] == 0
    names = {span.name for span in projection.spans}
    assert {"operation op-1", "operation outcome confirmed_done"} <= names
    pytest.importorskip("opentelemetry.sdk.trace")
    from synapse_channel.otel_export import sdk_spans

    resources = {span.resource for span in sdk_spans(projection)}
    assert {r.attributes.get(CONTRACT_RESOURCE_ATTRIBUTE) for r in resources} == {
        CORRELATION_CONTRACT
    }
    plain = sdk_spans(run_otel_projection(tmp_path / "hub.db"))
    assert {CONTRACT_RESOURCE_ATTRIBUTE in span.resource.attributes for span in plain} == {False}
