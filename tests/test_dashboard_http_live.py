# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — public live stream with an unavailable optional feed
"""Exercise the bounded producer using an actual owned NDJSON output file."""

from __future__ import annotations

import asyncio
import json
import socket
import time
from http import HTTPStatus
from pathlib import Path

from synapse_channel.dashboard import (
    DashboardSnapshot,
    DashboardUnavailable,
    fetch_dashboard_snapshot,
)
from synapse_channel.dashboard_http_live import LiveStreamBindings, serve_dashboard_live


def test_public_live_producer_marks_an_unavailable_feed_without_stopping_snapshot(
    tmp_path: Path,
) -> None:
    """An unavailable optional feed retains snapshot delivery and complete framing."""
    snapshot = DashboardSnapshot(online_agents=[], state={}, board={}, manifest=[])
    started: list[bool] = []
    refusals: list[HTTPStatus] = []

    def refuse(status: HTTPStatus, body: bytes, *, content_type: str) -> None:
        """Record an unexpected pre-stream refusal through the public writer."""
        refusals.append(status)

    path = tmp_path / "stream.ndjson"
    with path.open("wb") as output:
        bindings = LiveStreamBindings(
            fetch=lambda: snapshot,
            unavailable_error=DashboardUnavailable,
            feed=lambda _path, _query: None,
            feed_specs=lambda: (("events", "/events.json"),),
            a2a_state_file=None,
            start=lambda: started.append(True),
            write=output.write,
            flush=output.flush,
            refuse=refuse,
        )
        serve_dashboard_live("cycles=1", bindings)
    frames = [json.loads(line) for line in path.read_text().splitlines()]
    assert started == [True] and refusals == []
    assert [frame["kind"] for frame in frames] == ["hello", "channel", "channel", "close"]
    assert frames[1]["channel"] == "snapshot" and frames[1]["status"] == "live"
    assert frames[2]["channel"] == "events" and frames[2]["status"] == "absent"
    assert frames[2]["detail"] == "feed unavailable"
    assert frames[-1]["status"] == "complete"
    assert [frame["sequence"] for frame in frames] == [1, 2, 3, 4]


def test_actual_unreachable_hub_emits_snapshot_error_and_completes_stream(tmp_path: Path) -> None:
    """A real failed hub collection remains an explicit bounded channel failure."""
    refused: list[HTTPStatus] = []

    def refuse(status: HTTPStatus, body: bytes, *, content_type: str) -> None:
        """Retain any unexpected pre-stream HTTP refusal for the assertion."""
        refused.append(status)

    def fetch() -> DashboardSnapshot:
        """Collect through the real async API against an unavailable local endpoint."""
        return asyncio.run(
            fetch_dashboard_snapshot(
                uri="ws://127.0.0.1:1",
                name="LIVE/offline",
                token=None,
                ready_timeout=0.05,
                response_timeout=0.05,
            )
        )

    path = tmp_path / "offline.ndjson"
    with path.open("wb") as output:
        serve_dashboard_live(
            "cycles=1",
            LiveStreamBindings(
                fetch=fetch,
                unavailable_error=DashboardUnavailable,
                feed=lambda _path, _query: None,
                feed_specs=lambda: (),
                a2a_state_file=None,
                start=lambda: None,
                write=output.write,
                flush=output.flush,
                refuse=refuse,
            ),
        )
    frames = [json.loads(line) for line in path.read_text().splitlines()]
    assert refused == []
    assert [frame["kind"] for frame in frames] == ["hello", "channel", "close"]
    assert frames[1]["channel"] == "snapshot" and frames[1]["status"] == "error"
    assert frames[1]["detail"] and "data" not in frames[1]
    assert frames[-1]["status"] == "complete"


def test_a_two_cycle_stream_pauses_between_cycles_and_not_after_the_last(tmp_path: Path) -> None:
    """Two cycles are one cadence apart; the stream closes without a trailing pause."""
    snapshot = DashboardSnapshot(online_agents=[], state={}, board={}, manifest=[])
    refused: list[HTTPStatus] = []

    def refuse(status: HTTPStatus, body: bytes, *, content_type: str) -> None:
        """Retain any unexpected pre-stream HTTP refusal for the assertion."""
        refused.append(status)

    path = tmp_path / "two-cycles.ndjson"
    started = time.monotonic()
    with path.open("wb") as output:
        serve_dashboard_live(
            "cycles=2",
            LiveStreamBindings(
                fetch=lambda: snapshot,
                unavailable_error=DashboardUnavailable,
                feed=lambda _path, _query: None,
                feed_specs=lambda: (),
                a2a_state_file=None,
                start=lambda: None,
                write=output.write,
                flush=output.flush,
                refuse=refuse,
            ),
        )
    elapsed = time.monotonic() - started
    frames = [json.loads(line) for line in path.read_text().splitlines()]

    assert refused == []
    assert frames[0]["kind"] == "hello" and frames[0]["data"]["cadence_ms"] == 2000
    assert frames[-1]["kind"] == "close" and frames[-1]["status"] == "complete"
    assert [frame["sequence"] for frame in frames] == list(range(1, len(frames) + 1))
    # One pause for two cycles: at least the cadence, and well short of two of them.
    assert 2.0 <= elapsed < 3.5


def test_a_client_that_left_ends_the_stream_quietly_on_every_platform() -> None:
    """Writing to a connection the client closed ends the stream without an error."""
    snapshot = DashboardSnapshot(online_agents=[], state={}, board={}, manifest=[])
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    client = socket.create_connection(listener.getsockname(), timeout=5.0)
    served, _address = listener.accept()
    listener.close()
    client.close()
    written: list[int] = []

    def write(frame: bytes) -> int:
        """Send one frame on the real connection and count it once it went out."""
        served.sendall(frame)
        written.append(len(frame))
        # The first frame after the client's close draws its reset; give it time to arrive.
        time.sleep(0.2)
        return len(frame)

    try:
        serve_dashboard_live(
            "cycles=2",
            LiveStreamBindings(
                fetch=lambda: snapshot,
                unavailable_error=DashboardUnavailable,
                feed=lambda _path, _query: None,
                feed_specs=lambda: (),
                a2a_state_file=None,
                start=lambda: None,
                write=write,
                flush=lambda: None,
                refuse=lambda _status, _body, content_type: None,
            ),
        )
    finally:
        served.close()

    # The greeting went out; a later frame met the closed connection and ended the stream.
    assert 1 <= len(written) < 4
