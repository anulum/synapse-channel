# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — dashboard live-transport contract tests

"""Tests for versioning, framing, cursors, and bounded diagnostics."""

from __future__ import annotations

import copy
import json
from http import HTTPStatus
from typing import Any

import pytest

from synapse_channel.dashboard_feed_serving import FeedResponse
from synapse_channel.dashboard_live_transport import (
    LIVE_TRANSPORT_CHANNELS,
    LIVE_TRANSPORT_VERSION,
    LiveFrameSequence,
    LiveTransportCursor,
    decode_feed_response,
    parse_diagnostic_cycles,
    snapshot_fingerprint,
)


def test_live_frame_sequence_is_versioned_monotonic_and_canonical() -> None:
    frames = LiveFrameSequence("connection-1")

    first = json.loads(frames.encode("hello", sent_at=10, status="live"))
    second = json.loads(
        frames.encode(
            "channel",
            sent_at=11,
            channel="events",
            status="live",
            data={"events": []},
        )
    )

    assert first == {
        "connection_id": "connection-1",
        "kind": "hello",
        "sent_at": 10,
        "sequence": 1,
        "status": "live",
        "version": LIVE_TRANSPORT_VERSION,
    }
    assert second["sequence"] == 2
    assert second["channel"] == "events"
    assert second["data"] == {"events": []}
    third = json.loads(frames.encode("close", sent_at=12, channel="events", detail="done"))
    assert "status" not in third and "data" not in third
    assert (third["sequence"], third["detail"]) == (3, "done")


def test_live_cursor_bootstraps_history_then_advances_monotonically() -> None:
    cursor = LiveTransportCursor()

    assert cursor.query("events") == "since=latest&limit=250&history=1"
    assert cursor.query("receipts") == "since=0&limit=100"
    cursor.advance("events", {"next_cursor": 41})
    cursor.advance("events", {"next_cursor": 12})
    cursor.advance("receipts", {"next_cursor": 9})
    cursor.advance("operator_actions", {"next_cursor": 7})

    assert cursor.query("events") == "since=41&limit=1000"
    assert cursor.query("receipts") == "since=9&limit=100"
    assert cursor.query("operator_actions") == "since=7&limit=100"
    with pytest.raises(ValueError, match="unknown live transport channel"):
        cursor.query("unknown")
    cursor.advance("unknown", {"next_cursor": 99})
    assert cursor == LiveTransportCursor(events=41, receipts=9, operator_actions=7)


@pytest.mark.parametrize("next_cursor", [None, -1, True, 1.5, "2"])
def test_live_cursor_rejects_non_monotonic_cursor_shapes(next_cursor: object) -> None:
    cursor = LiveTransportCursor(events=5, receipts=4, operator_actions=3)

    cursor.advance("events", {"next_cursor": next_cursor})
    cursor.advance("receipts", {"next_cursor": next_cursor})
    cursor.advance("operator_actions", {"next_cursor": next_cursor})

    assert cursor == LiveTransportCursor(events=5, receipts=4, operator_actions=3)


def test_feed_response_preserves_live_absent_and_error_states() -> None:
    live = FeedResponse(HTTPStatus.OK, b'{"next_cursor":3}', "application/json")
    absent = FeedResponse(HTTPStatus.NOT_FOUND, b"not configured\n", "text/plain")
    error = FeedResponse(HTTPStatus.SERVICE_UNAVAILABLE, b"store failed\n", "text/plain")
    malformed = FeedResponse(HTTPStatus.OK, b"[]", "application/json")

    assert decode_feed_response(live) == ("live", {"next_cursor": 3}, None)
    assert decode_feed_response(absent) == ("absent", None, "not configured")
    assert decode_feed_response(error) == ("error", None, "store failed")
    assert decode_feed_response(malformed) == (
        "error",
        None,
        "feed returned a non-object JSON document",
    )
    for body in (b"{not json", b"\xff\xfe"):
        broken = FeedResponse(HTTPStatus.OK, body, "application/json")
        assert decode_feed_response(broken) == ("error", None, "feed returned invalid JSON")


def test_diagnostic_cycles_are_bounded_and_strict() -> None:
    assert parse_diagnostic_cycles("") is None
    assert parse_diagnostic_cycles("cycles=1") == 1
    assert parse_diagnostic_cycles("cycles=8") == 8
    for query in (
        "once=1",
        "cycles=",
        "cycles=0",
        "cycles=9",
        "cycles=-1",
        "cycles=1.5",
        "cycles=1&cycles=2",
        "cycles=1&other=2",
    ):
        with pytest.raises(ValueError):
            parse_diagnostic_cycles(query)

    assert LIVE_TRANSPORT_CHANNELS == (
        "snapshot",
        "events",
        "receipts",
        "operator_actions",
    )


def _fingerprinted(
    *,
    fleet_at: float = 1.0,
    state_at: float = 1.0,
    seen: float = 1.0,
    live: tuple[str, ...] = ("a",),
) -> dict[str, Any]:
    return {
        "hub_id": "hub",
        "online_agents": list(live),
        "fleet": {"generated_at": fleet_at, "agents": {"live": list(live)}},
        "state": {
            "generated_at": state_at,
            "active_claims": [],
            "agents": [{"agent": name, "last_seen": seen} for name in live] + ["not-a-row"],
        },
    }


def test_snapshot_fingerprint_ignores_the_three_per_fetch_stamps_only() -> None:
    """Only fleet.generated_at, state.generated_at and agents' last_seen are left out."""
    first = _fingerprinted()
    pristine = copy.deepcopy(first)
    later = _fingerprinted(fleet_at=2.0, state_at=3.0, seen=4.0)

    assert snapshot_fingerprint(first) == snapshot_fingerprint(later)
    assert first == pristine
    for changed in (
        _fingerprinted(live=("a", "b")),
        {**first, "online_agents": []},
        {**first, "state": {**first["state"], "active_claims": [{"task_id": "T"}]}},
        {**first, "state": {**first["state"], "agents": [{"agent": "b", "last_seen": 1.0}]}},
        {**first, "state": {**first["state"], "agents": None}},
        {**first, "state": "not a mapping"},
        {**first, "fleet": None},
    ):
        assert snapshot_fingerprint(changed) != snapshot_fingerprint(first)
