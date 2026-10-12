# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — dashboard live snapshot and durable delta streaming
"""Emit the existing bounded NDJSON snapshot and durable-feed stream."""

from __future__ import annotations

import time
import uuid as uuid
from collections.abc import Callable
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING

from synapse_channel.dashboard_live_transport import (
    LIVE_TRANSPORT_CHANNELS as LIVE_TRANSPORT_CHANNELS,
)
from synapse_channel.dashboard_live_transport import (
    LIVE_TRANSPORT_CONTENT_TYPE as LIVE_TRANSPORT_CONTENT_TYPE,
)
from synapse_channel.dashboard_live_transport import (
    LIVE_TRANSPORT_PATH as LIVE_TRANSPORT_PATH,
)
from synapse_channel.dashboard_live_transport import (
    LiveFrameSequence as LiveFrameSequence,
)
from synapse_channel.dashboard_live_transport import (
    LiveTransportCursor as LiveTransportCursor,
)
from synapse_channel.dashboard_live_transport import (
    decode_feed_response as decode_feed_response,
)
from synapse_channel.dashboard_live_transport import (
    parse_diagnostic_cycles as parse_diagnostic_cycles,
)
from synapse_channel.dashboard_live_transport import (
    snapshot_fingerprint as snapshot_fingerprint,
)

if TYPE_CHECKING:
    from typing import Protocol

    from synapse_channel.dashboard import DashboardSnapshot
    from synapse_channel.dashboard_feed_serving import FeedResponse
    from synapse_channel.dashboard_http_pages import ResponseWriter

    class FrameSender(Protocol):
        """Encode one existing live-transport frame through the owning stream."""

        def __call__(
            self,
            kind: str,
            *,
            channel: str | None = None,
            status: str | None = None,
            data: object | None = None,
            detail: str | None = None,
        ) -> None:
            """Write and flush one complete NDJSON frame."""
            ...


@dataclass(frozen=True)
class LiveStreamBindings:
    """Bounded collection and HTTP I/O owned by one admitted live request.

    Parameters
    ----------
    fetch, unavailable_error, feed, feed_specs
        Existing snapshot/feed callbacks and late-bound channel paths.
    a2a_state_file
        Optional existing read-side A2A projection input.
    start, write, flush, refuse
        Owner callbacks for headers, bytes, flush and pre-stream HTTP refusal.
    """

    fetch: Callable[[], DashboardSnapshot]
    unavailable_error: type[Exception]
    feed: Callable[[str, str], FeedResponse | None]
    feed_specs: Callable[[], tuple[tuple[str, str], ...]]
    a2a_state_file: Path | None
    start: Callable[[], None]
    write: Callable[[bytes], object]
    flush: Callable[[], None]
    refuse: ResponseWriter


def _send_snapshot(
    ports: LiveStreamBindings, send: FrameSender, previous: str | None
) -> str | None:
    """Publish an actual snapshot change or its original error/unchanged status."""
    try:
        snapshot = ports.fetch()
    except (ports.unavailable_error, TimeoutError) as exc:
        send("channel", channel="snapshot", status="error", detail=str(exc))
        return previous
    document = snapshot.to_dict(a2a_state_file=ports.a2a_state_file)
    fingerprint = snapshot_fingerprint(document)
    if fingerprint == previous:
        send("channel", channel="snapshot", status="unchanged")
    else:
        send("channel", channel="snapshot", status="live", data=document)
        previous = fingerprint
    return previous


def _send_feeds(ports: LiveStreamBindings, send: FrameSender, cursors: LiveTransportCursor) -> None:
    """Emit each original durable delta and advance only its successful cursor."""
    for channel, path in ports.feed_specs():
        response = ports.feed(path, cursors.query(channel))
        if response is None:
            send("channel", channel=channel, status="absent", detail="feed unavailable")
            continue
        status, data, detail = decode_feed_response(response)
        send("channel", channel=channel, status=status, data=data, detail=detail)
        if status == "live" and isinstance(data, dict):
            cursors.advance(channel, data)


def serve_dashboard_live(query: str, ports: LiveStreamBindings) -> None:
    """Validate the diagnostic bound, then emit the original live stream lifecycle.

    Parameters
    ----------
    query
        Admitted query string whose diagnostic cycle limit is parsed unchanged.
    ports
        Existing response, snapshot and durable-feed owners for this request.
    """
    try:
        cycle_limit = parse_diagnostic_cycles(query)
    except ValueError as exc:
        ports.refuse(HTTPStatus.BAD_REQUEST, f"{exc}\n".encode(), content_type="text/plain")
        return
    ports.start()
    frames, cursors = LiveFrameSequence(uuid.uuid4().hex), LiveTransportCursor()
    previous: str | None = None

    def send(
        kind: str,
        *,
        channel: str | None = None,
        status: str | None = None,
        data: object | None = None,
        detail: str | None = None,
    ) -> None:
        ports.write(
            frames.encode(
                kind,
                sent_at=int(time.time() * 1000),
                channel=channel,
                status=status,
                data=data,
                detail=detail,
            )
        )
        ports.flush()

    try:
        send(
            "hello",
            status="live",
            data={"channels": list(LIVE_TRANSPORT_CHANNELS), "cadence_ms": 2000},
        )
        cycles = 0
        while cycle_limit is None or cycles < cycle_limit:
            cycles += 1
            previous = _send_snapshot(ports, send, previous)
            _send_feeds(ports, send, cursors)
            if cycle_limit is None or cycles < cycle_limit:
                time.sleep(2.0)
        send("close", status="complete", detail="bounded diagnostic stream complete")
    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, TimeoutError):
        # A client that left is not an error of the server; Windows reports it as an abort.
        return
