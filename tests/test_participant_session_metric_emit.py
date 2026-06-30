# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the durable session-metric emit bridge
"""Tests for :mod:`synapse_channel.participants.session_metric_emit`.

A recording poster captures what :func:`emit_session_metric` would append to the progress ledger,
so the suite asserts the snapshot rides the ``session_metric`` channel with the session id as the
note's task id, that the body round-trips through the codec, and that an empty (zero-turn) session
is skipped rather than written.
"""

from __future__ import annotations

from synapse_channel.participants.session_metric_emit import emit_session_metric
from synapse_channel.participants.session_metric_note import (
    SESSION_METRIC_NOTE_KIND,
    parse_session_metric_note,
)
from synapse_channel.participants.session_telemetry import SessionMetrics


class _RecordingPoster:
    """Capture every progress note posted, mimicking ``SynapseAgent.post_progress``."""

    def __init__(self) -> None:
        self.notes: list[tuple[str, str, str]] = []

    async def __call__(self, task_id: str, text: str, *, kind: str = "note") -> None:
        self.notes.append((task_id, text, kind))


async def test_emits_a_parseable_snapshot_on_the_session_metric_channel() -> None:
    poster = _RecordingPoster()
    metrics = SessionMetrics(
        turns=3,
        errors=1,
        input_tokens=900,
        output_tokens=120,
        cost_usd=0.4,
        total_latency_seconds=5.0,
        max_rate_limit_utilisation=0.7,
        last_input_tokens=300,
    )
    emitted = await emit_session_metric(metrics, post_progress=poster, session_id="sess-9")
    assert emitted is True
    assert len(poster.notes) == 1
    task_id, text, kind = poster.notes[0]
    assert task_id == "sess-9"
    assert kind == SESSION_METRIC_NOTE_KIND
    parsed = parse_session_metric_note(text)
    assert parsed is not None
    assert parsed["turns"] == 3
    assert parsed["errors"] == 1
    assert parsed["last_input_tokens"] == 300
    assert parsed["max_rate_limit_utilisation"] == 0.7


async def test_empty_session_is_skipped() -> None:
    poster = _RecordingPoster()
    emitted = await emit_session_metric(
        SessionMetrics(), post_progress=poster, session_id="sess-empty"
    )
    assert emitted is False
    assert poster.notes == []
