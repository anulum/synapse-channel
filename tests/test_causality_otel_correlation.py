# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — model usage and policy denials in task traces
"""Correlated OTel projection over real event stores and the real CLI."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from synapse_channel import cli
from synapse_channel.core.accounting import USAGE_NOTE_KIND, format_usage_note
from synapse_channel.core.causality_otel import (
    projection_to_json,
    run_otel_projection,
    span_id_for_root,
    trace_id_for_task,
)
from synapse_channel.core.causality_otel_correlation import (
    CORRELATION_SCHEMA_URL,
    GENAI_REQUEST_MODEL,
    GENAI_USAGE_INPUT_TOKENS,
    GENAI_USAGE_OUTPUT_TOKENS,
)
from synapse_channel.core.journal import EventKind, record_claim_denial
from synapse_channel.core.persistence import EventStore


def _usage(store: EventStore, task_id: str, text: str, kind: str = USAGE_NOTE_KIND) -> None:
    """Append one real progress note of ``kind`` for ``task_id``."""
    store.append(
        EventKind.LEDGER_PROGRESS,
        {"task_id": task_id, "kind": kind, "text": text, "author": "AGENT"},
    )


def _journal(path: Path) -> Path:
    """Write a hub log with two tasks, their usage, a denial and unmatched records."""
    store = EventStore(path)
    try:
        store.append(
            EventKind.LEDGER_TASK,
            {"task_id": "T", "title": "work", "status": "open", "owner": "AGENT"},
        )
        store.append(EventKind.CLAIM, {"task_id": "T", "owner": "AGENT", "status": "claimed"})
        store.append(EventKind.CLAIM, {"task_id": "U", "owner": "PEER", "status": "claimed"})
        _usage(
            store,
            "T",
            format_usage_note(model="m-1", calls=2, input_tokens=30, output_tokens=7, cost=0.25),
        )
        _usage(store, "T", format_usage_note(model="m-2", input_tokens=5))
        _usage(store, "T", "a plain progress note", kind="note")
        _usage(store, "T", "usage calls=1")
        record_claim_denial(
            store, claimant="OTHER", task_id="T", reason_code="scope", worktree="/w", paths=["a"]
        )
        record_claim_denial(
            store, claimant="OTHER", task_id="NOT-A-TASK", reason_code="x", worktree="", paths=[]
        )
        _usage(store, "UNKNOWN", format_usage_note(model="m-3"))
        _usage(store, "U", format_usage_note(model="m-u", input_tokens=1, output_tokens=2))
    finally:
        store.close()
    return path


def test_correlated_projection_places_usage_and_denials_in_the_task_trace(
    tmp_path: Path,
) -> None:
    """Usage and the matching denial join T's trace; unmatched records are counted."""
    path = _journal(tmp_path / "hub.db")
    projection = run_otel_projection(path, correlate=True)
    assert len(projection.spans) == len(run_otel_projection(path).spans) + 4
    assert projection.schema_url == CORRELATION_SCHEMA_URL
    assert projection.uncorrelated_events == 2
    assert projection.filtered_out_events == 0
    by_name = {span.name: span for span in projection.spans}
    first = dict(by_name["model usage m-1"].attributes)
    assert first[GENAI_REQUEST_MODEL] == "m-1"
    assert first[GENAI_USAGE_INPUT_TOKENS] == 30
    assert first[GENAI_USAGE_OUTPUT_TOKENS] == 7
    assert first["synapse.usage.calls"] == 2
    assert first["synapse.usage.cost_usd"] == 0.25
    assert first["synapse.agent"] == "AGENT"
    second = dict(by_name["model usage m-2"].attributes)
    assert "synapse.usage.cost_usd" not in second
    assert second[GENAI_USAGE_OUTPUT_TOKENS] == 0
    assert dict(by_name["model usage m-u"].attributes)["synapse.task_id"] == "U"
    denial = by_name["claim denied T"]
    assert dict(denial.attributes)["synapse.policy.decision"] == "denied"
    assert dict(denial.attributes)["synapse.policy.reason"] == "scope"
    for span in (by_name["model usage m-1"], denial):
        assert span.trace_id_hex == trace_id_for_task("T")
        assert span.parent_span_id_hex == span_id_for_root("T")
    assert "model usage m-3" not in by_name
    assert not any(name.startswith("claim denied NOT") for name in by_name)
    document = projection_to_json(projection)
    assert document["schema_url"] == CORRELATION_SCHEMA_URL
    assert document["uncorrelated_events"] == 2
    assert document["filtered_out_events"] == 0
    parsed: dict[str, Any] = json.loads(json.dumps(document))
    assert parsed == document
    attributes = next(s["attributes"] for s in parsed["spans"] if s["name"] == "model usage m-1")
    assert attributes[GENAI_USAGE_INPUT_TOKENS] == 30


def test_filtered_out_records_are_counted_apart_from_unmatched_ones(tmp_path: Path) -> None:
    """Usage of a task the filter excluded is not reported as matching no task."""
    projection = run_otel_projection(
        _journal(tmp_path / "hub.db"), task_filter=["T"], correlate=True
    )
    assert projection.uncorrelated_events == 2
    assert projection.filtered_out_events == 1
    assert projection.filtered_out_tasks == 1
    assert not any(span.name == "model usage m-u" for span in projection.spans)
    assert projection_to_json(projection)["filtered_out_events"] == 1


def test_uncorrelated_projection_keeps_its_previous_shape(tmp_path: Path) -> None:
    """Without the flag no correlation span, schema URL or new JSON key appears."""
    projection = run_otel_projection(_journal(tmp_path / "hub.db"))
    assert projection.schema_url == ""
    assert projection.uncorrelated_events == 0
    assert not any(
        span.name.startswith(("model usage", "claim denied")) for span in projection.spans
    )
    document = projection_to_json(projection)
    assert "schema_url" not in document
    assert "uncorrelated_events" not in document
    assert "filtered_out_events" not in document
    assert projection.filtered_out_events == 0


def test_attribute_names_match_the_locked_semantic_conventions() -> None:
    """The literal names equal the pinned OpenTelemetry GenAI conventions."""
    gen_ai = pytest.importorskip("opentelemetry.semconv._incubating.attributes.gen_ai_attributes")
    schemas = pytest.importorskip("opentelemetry.semconv.schemas")
    assert GENAI_REQUEST_MODEL == gen_ai.GEN_AI_REQUEST_MODEL
    assert GENAI_USAGE_INPUT_TOKENS == gen_ai.GEN_AI_USAGE_INPUT_TOKENS
    assert GENAI_USAGE_OUTPUT_TOKENS == gen_ai.GEN_AI_USAGE_OUTPUT_TOKENS
    assert CORRELATION_SCHEMA_URL in {schema.value for schema in schemas.Schemas}


def test_exported_resource_carries_the_schema_only_when_correlated(tmp_path: Path) -> None:
    """The SDK resource holds the schema URL of a correlated projection."""
    pytest.importorskip("opentelemetry.sdk.trace")
    from synapse_channel.otel_export import sdk_spans

    path = _journal(tmp_path / "hub.db")
    correlated = sdk_spans(run_otel_projection(path, correlate=True))
    plain = sdk_spans(run_otel_projection(path))
    assert {span.resource.schema_url for span in correlated} == {CORRELATION_SCHEMA_URL}
    assert {span.resource.schema_url for span in plain} == {""}


def test_exported_usage_attributes_carry_the_semantic_convention_types(tmp_path: Path) -> None:
    """Token and call counts reach the SDK as int and the cost as float."""
    pytest.importorskip("opentelemetry.sdk.trace")
    from synapse_channel.otel_export import sdk_spans

    spans = sdk_spans(run_otel_projection(_journal(tmp_path / "hub.db"), correlate=True))
    usage = next(span for span in spans if span.name == "model usage m-1")
    assert usage.attributes is not None
    types = {key: type(value) for key, value in usage.attributes.items()}
    assert types[GENAI_REQUEST_MODEL] is str
    assert types[GENAI_USAGE_INPUT_TOKENS] is int
    assert types[GENAI_USAGE_OUTPUT_TOKENS] is int
    assert types["synapse.usage.calls"] is int
    assert types["synapse.usage.cost_usd"] is float
    assert usage.attributes[GENAI_USAGE_INPUT_TOKENS] == 30


def test_cli_correlate_writes_and_reports_unmatched_records(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``causality otel --correlate`` writes the correlated document and counts misses."""
    db = _journal(tmp_path / "hub.db")
    out = tmp_path / "spans.json"
    assert cli.main(["causality", "otel", str(db), "--out", str(out), "--correlate"]) == 0
    printed = capsys.readouterr().out
    assert "2 usage/denial record(s) matched no task" in printed
    document = json.loads(out.read_text(encoding="utf-8"))
    assert document["schema_url"] == CORRELATION_SCHEMA_URL
    assert cli.build_parser().parse_args(["causality", "otel", "hub.db"]).correlate is False
    narrowed = ["causality", "otel", str(db), "--out", str(out), "--correlate", "--filter", "T"]
    assert cli.main(narrowed) == 0
    printed = capsys.readouterr().out
    assert "1 task(s) filtered out" in printed
    assert "1 usage/denial record(s) of filtered-out tasks" in printed
    assert "2 usage/denial record(s) matched no task" in printed


def test_cli_refuses_correlate_outside_the_otel_mode(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``--correlate`` belongs to the otel mode only."""
    db = _journal(tmp_path / "hub.db")
    assert cli.main(["causality", "causes", str(db), "1", "--correlate"]) == 2
    assert "--correlate belong to the otel mode" in capsys.readouterr().err
