# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the durable session-metric reporter
"""Tests for :mod:`synapse_channel.participants.session_metric_report`.

The suite asserts that the reporter keeps the latest cumulative snapshot per ``(agent, session)``
regardless of event order, ignores non-session-metric and unparsable events, totals across
sessions, carries the highest rate-limit utilisation, reads a real on-disk store, and renders
both the empty and populated reports plus a stable JSON shape.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore, StoredEvent
from synapse_channel.participants.session_metric_note import (
    SESSION_METRIC_NOTE_KIND,
    format_session_metric_note,
)
from synapse_channel.participants.session_metric_report import (
    build_session_metric_report,
    render_session_metric_report,
    run_session_metric_report,
    session_metric_report_to_json,
)
from synapse_channel.participants.session_telemetry import SessionMetrics


def _body(**overrides: object) -> str:
    base: dict[str, object] = {
        "turns": 2,
        "errors": 0,
        "abstentions": 0,
        "input_tokens": 100,
        "output_tokens": 20,
        "cost_usd": 0.1,
        "total_latency_seconds": 2.0,
        "max_rate_limit_utilisation": None,
        "last_input_tokens": 60,
    }
    base.update(overrides)
    return format_session_metric_note(SessionMetrics(**base))  # type: ignore[arg-type]


def _event(
    *,
    seq: int,
    author: str,
    session: str,
    body: str,
    ts: float = 1.0,
    kind: str = SESSION_METRIC_NOTE_KIND,
) -> StoredEvent:
    return StoredEvent(
        seq=seq,
        ts=ts,
        kind=EventKind.LEDGER_PROGRESS,
        payload={"kind": kind, "text": body, "author": author, "task_id": session},
    )


def test_keeps_latest_snapshot_per_session_regardless_of_order() -> None:
    events = [
        # Session A: ascending — the higher-seq snapshot supersedes.
        _event(seq=1, author="alpha", session="A", body=_body(turns=2)),
        _event(seq=3, author="alpha", session="A", body=_body(turns=5)),
        # Session B: descending in the stream — the lower-seq later event must NOT win.
        _event(seq=5, author="beta", session="B", body=_body(turns=9)),
        _event(seq=2, author="beta", session="B", body=_body(turns=1)),
    ]
    report = build_session_metric_report(events)
    by_session = report.by_session
    assert by_session[("alpha", "A")].turns == 5
    assert by_session[("beta", "B")].turns == 9
    assert report.totals.sessions == 2
    assert report.totals.turns == 14
    assert report.generated_from_seq == 5


def test_ignores_unrelated_and_unparsable_events() -> None:
    events = [
        StoredEvent(1, 1.0, EventKind.CHAT, {"text": "hi"}),
        _event(seq=2, author="a", session="s", body="not a session metric", kind="note"),
        _event(seq=3, author="a", session="s", body="garbage-prefix turns=4"),
        _event(seq=4, author="a", session="s", body=_body(turns=3)),
    ]
    report = build_session_metric_report(events)
    assert report.totals.sessions == 1
    assert report.sessions[0].turns == 3


def test_totals_carry_the_highest_utilisation_across_sessions() -> None:
    events = [
        _event(seq=1, author="a", session="x", body=_body(max_rate_limit_utilisation=None)),
        _event(seq=2, author="a", session="y", body=_body(max_rate_limit_utilisation=0.6)),
        _event(seq=3, author="a", session="z", body=_body(max_rate_limit_utilisation=0.4)),
    ]
    report = build_session_metric_report(events)
    assert report.totals.max_rate_limit_utilisation == pytest.approx(0.6)


def test_derived_rates_and_latency_on_a_record() -> None:
    events = [
        _event(
            seq=1,
            author="a",
            session="s",
            body=_body(turns=4, errors=1, total_latency_seconds=8.0),
        )
    ]
    record = build_session_metric_report(events).sessions[0]
    assert record.total_tokens == 120
    assert record.error_rate == pytest.approx(0.25)
    assert record.mean_latency_seconds == pytest.approx(2.0)


def test_empty_report_has_zero_totals_and_renders_a_notice() -> None:
    report = build_session_metric_report([])
    assert report.totals.sessions == 0
    assert report.totals.error_rate == 0.0
    assert report.totals.mean_latency_seconds == 0.0
    assert report.generated_from_seq == 0
    rendered = render_session_metric_report(report)
    assert "No recorded session telemetry" in rendered


def test_render_lists_sessions_with_optional_fallbacks() -> None:
    events = [
        # Attributed session with a utilisation suffix.
        _event(seq=1, author="alpha", session="A", body=_body(max_rate_limit_utilisation=0.5)),
        # Unattributed, no session id — exercises the render fallbacks.
        _event(seq=2, author="", session="", body=_body()),
    ]
    rendered = render_session_metric_report(build_session_metric_report(events))
    assert "alpha/A" in rendered
    assert "max_rate_limit=0.500" in rendered
    assert "(unattributed)/(no-session)" in rendered


def test_to_json_is_stable_and_complete() -> None:
    events = [_event(seq=1, author="a", session="s", body=_body(max_rate_limit_utilisation=0.3))]
    payload = session_metric_report_to_json(build_session_metric_report(events))
    assert cast(str, payload["note"]).startswith("opt-in operational telemetry")
    totals = cast("dict[str, object]", payload["totals"])
    assert totals["sessions"] == 1
    assert totals["total_tokens"] == 120
    sessions = cast("list[dict[str, object]]", payload["sessions"])
    assert sessions[0]["session_id"] == "s"
    assert sessions[0]["max_rate_limit_utilisation"] == pytest.approx(0.3)


def test_coordination_task_id_surfaces_in_record_json_and_render() -> None:
    body = format_session_metric_note(
        SessionMetrics(turns=2, input_tokens=100, output_tokens=20), task_id="quantum/T42"
    )
    report = build_session_metric_report([_event(seq=1, author="a", session="s", body=body)])

    record = report.sessions[0]
    assert record.session_id == "s"  # the note's slot carries the session
    assert record.task_id == "quantum/T42"  # the body carries the coordination task
    payload = session_metric_report_to_json(report)
    sessions = cast("list[dict[str, object]]", payload["sessions"])
    assert sessions[0]["task_id"] == "quantum/T42"
    assert "task=quantum/T42" in render_session_metric_report(report)


def test_a_body_without_a_task_id_leaves_the_record_task_empty() -> None:
    report = build_session_metric_report([_event(seq=1, author="a", session="s", body=_body())])

    assert report.sessions[0].task_id == ""
    assert "task=" not in render_session_metric_report(report)


def test_run_report_missing_store_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="missing event store"):
        run_session_metric_report(tmp_path / "nope.db")


def test_run_report_reads_a_real_store(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    store = EventStore(db)
    try:
        store.append(
            EventKind.LEDGER_PROGRESS,
            {
                "kind": SESSION_METRIC_NOTE_KIND,
                "text": _body(turns=6),
                "author": "alpha",
                "task_id": "live",
            },
        )
    finally:
        store.close()
    report = run_session_metric_report(db)
    assert report.totals.sessions == 1
    assert report.by_session[("alpha", "live")].turns == 6
