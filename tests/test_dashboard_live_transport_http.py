# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — dashboard live-transport real HTTP tests

"""Exercise the authenticated NDJSON route through the production HTTP server."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

import synapse_channel.dashboard as dashboard_module
from dashboard_helpers import _feeds_server, _http_get
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore
from synapse_channel.dashboard import DashboardSnapshot


async def _snapshot(**_kwargs: object) -> DashboardSnapshot:
    return DashboardSnapshot(
        online_agents=["SYNAPSE-CHANNEL/worker"],
        state={"active_claims": []},
        board={"tasks": []},
        manifest=[],
        hub_version="test",
        hub_id="hub-test",
        config_epoch="epoch-test",
    )


def _seed_store(path: Path) -> None:
    store = EventStore(path)
    store.append(
        EventKind.CLAIM,
        {"task_id": "T-1", "owner": "worker", "status": "claimed", "paths": []},
        ts=1.0,
    )
    store.close()


def test_live_transport_streams_one_authenticated_multiplexed_cycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = tmp_path / "hub.db"
    _seed_store(db)
    monkeypatch.setattr(dashboard_module, "fetch_dashboard_snapshot", _snapshot)
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    server = _feeds_server(reliability_db=db)
    token = server.dashboard_token
    assert token is not None
    request = Request(
        server.url("/live.ndjson?cycles=2"),
        headers={"Authorization": f"Bearer {token}", "Connection": "close"},
    )
    try:
        with urlopen(request, timeout=3) as response:  # nosec B310
            content_type = response.headers.get_content_type()
            buffering = response.headers.get("X-Accel-Buffering")
            frames = [json.loads(line) for line in response]
    finally:
        server.close()

    assert content_type == "application/x-ndjson"
    assert buffering == "no"
    assert [frame["sequence"] for frame in frames] == list(range(1, len(frames) + 1))
    assert frames[0]["kind"] == "hello"
    assert frames[0]["data"]["channels"] == [
        "snapshot",
        "events",
        "receipts",
        "operator_actions",
    ]
    channel_frames = [frame for frame in frames if frame["kind"] == "channel"]
    by_channel = {frame.get("channel"): frame for frame in channel_frames}
    snapshot_frames = [frame for frame in channel_frames if frame.get("channel") == "snapshot"]
    event_frames = [frame for frame in channel_frames if frame.get("channel") == "events"]
    assert snapshot_frames[0]["data"]["hub_id"] == "hub-test"
    assert snapshot_frames[1]["status"] == "unchanged"
    assert "data" not in snapshot_frames[1]
    assert by_channel["events"]["status"] == "live"
    assert event_frames[0]["data"]["events"][0]["seq"] == 1
    assert by_channel["receipts"]["status"] == "live"
    assert by_channel["operator_actions"]["status"] == "live"
    assert frames[-1]["kind"] == "close"


def test_live_transport_requires_access_and_rejects_bad_diagnostic_query() -> None:
    server = _feeds_server(dashboard_token="stream-secret")
    try:
        denied, _, _ = _http_get(server.url("/live.ndjson?cycles=1"))
        malformed, _, body = _http_get(
            server.url("/live.ndjson?cycles=0"), authorization="Bearer stream-secret"
        )
    finally:
        server.close()

    assert denied == 401
    assert malformed == 400
    assert "within 1..8" in body


def test_live_transport_closes_cleanly_when_the_reader_disconnects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(dashboard_module, "fetch_dashboard_snapshot", _snapshot)
    server = _feeds_server()
    token = server.dashboard_token
    assert token is not None
    request = Request(
        server.url("/live.ndjson"),
        headers={"Authorization": f"Bearer {token}", "Connection": "close"},
    )
    try:
        response = urlopen(request, timeout=3)  # nosec B310
        first = json.loads(response.readline())
        response.close()
    except HTTPError as exc:  # pragma: no cover - assertion aid
        pytest.fail(f"stream failed with HTTP {exc.code}")
    finally:
        server.close()

    assert first["kind"] == "hello"


async def test_live_transport_serializes_overlapping_snapshot_fetches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Concurrent streams share one snapshot identity without overlap."""
    first_entered = threading.Event()
    state_lock = threading.Lock()
    active = 0
    peak_active = 0

    async def slow_snapshot(**_kwargs: object) -> DashboardSnapshot:
        nonlocal active, peak_active
        with state_lock:
            active += 1
            peak_active = max(peak_active, active)
            first = not first_entered.is_set()
            first_entered.set()
        if first:
            await asyncio.sleep(0.1)
        with state_lock:
            active -= 1
        return await _snapshot()

    monkeypatch.setattr(dashboard_module, "fetch_dashboard_snapshot", slow_snapshot)
    server = _feeds_server()
    token = server.dashboard_token
    assert token is not None
    url = server.url("/live.ndjson?cycles=1")
    try:
        first = asyncio.create_task(
            asyncio.to_thread(_http_get, url, authorization=f"Bearer {token}")
        )
        assert await asyncio.to_thread(first_entered.wait, 1.0)
        second = asyncio.create_task(
            asyncio.to_thread(_http_get, url, authorization=f"Bearer {token}")
        )
        responses = await asyncio.gather(first, second)
    finally:
        server.close()

    assert peak_active == 1
    for status, content_type, body in responses:
        assert status == 200
        assert content_type == "application/x-ndjson"
        frames = [json.loads(line) for line in body.splitlines()]
        snapshot = next(frame for frame in frames if frame.get("channel") == "snapshot")
        assert snapshot["status"] == "live"
