# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — correlated native activity and atomic observer journeys
"""Validate native pages on real connections before committing durable wake work."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from websockets.asyncio.server import ServerConnection, serve

from hub_e2e_helpers import close_agents, connect_agent, running_hub
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType
from synapse_channel.core.streaming import StreamProducer, encode_stream_frame
from synapse_channel.mcp.bridge import SynapseHubBridge
from synapse_channel.mcp.wake_ledger import WakeLedger
from synapse_channel.mcp.wake_pointer import WakeScope
from synapse_channel.relay import append_jsonl, encode_lite
from test_hub_activity_network import fence


@asynccontextmanager
async def connected_bridge(
    uri: str, *, feed: Path | None = None, cursor: Path | None = None
) -> AsyncIterator[SynapseHubBridge]:
    """Own one bridge connection and drain it before leaving."""
    bridge = SynapseHubBridge(
        uri=uri,
        name="ALPHA/worker",
        request_timeout=0.3,
        inbox_feed=feed,
        inbox_cursor=cursor,
    )
    task = asyncio.create_task(bridge.agent.connect())
    try:
        assert await bridge.agent.wait_until_ready(3)
        yield bridge
    finally:
        bridge.agent.running = False
        assert bridge.agent.connection is not None
        await bridge.agent.connection.close()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_real_native_page_commits_all_sources_and_reopens_outbox(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "hub.db")
    hub = SynapseHub(journal=store, hub_id="mcp-activity", anti_rollback_checkpoint=False)
    feed, local_cursor = tmp_path / "feed.ndjson", tmp_path / "feed.cursor"
    append_jsonl(
        feed,
        encode_lite(
            {
                "type": "chat",
                "sender": "ALPHA/local",
                "target": "ALPHA/worker",
                "payload": "local-only",
                "timestamp": 1.0,
                "msg_id": 1,
            }
        ),
    )
    try:
        async with running_hub(hub) as (_, uri):
            publisher = await connect_agent("ALPHA/publisher", uri)
            try:
                async with connected_bridge(uri, feed=feed, cursor=local_cursor) as bridge:
                    assert (await bridge.activity_page()).items == ()
                    assert len(hub.agent_sockets) == 2
                    await publisher.agent.chat("private directed body", target="ALPHA/worker")
                    await publisher.agent.chat("another private body", target="ALPHA/worker")
                    await publisher.agent.post_task(
                        "ALPHA/plan",
                        "private plan",
                        suggested_owner="ALPHA/worker",
                        project="ALPHA",
                    )
                    await publisher.agent.claim("ALPHA/lease")
                    await publisher.recorder.wait_for(
                        lambda frame: frame.get("type") == MessageType.CLAIM_GRANTED
                    )
                    await publisher.agent.handoff("ALPHA/lease", "ALPHA/worker")
                    await fence(publisher)
                    first = await bridge.activity_page(limit=1)
                    assert first.has_more and first.sources == ("messages",)
                    page = await bridge.activity_page()
                    assert page.identity == "ALPHA/worker" and page.hub_id == "mcp-activity"
                    assert page.sources == ("messages", "tasks", "handoffs")
                    assert [item.kind for item in page.items] == [
                        "messages",
                        "messages",
                        "tasks",
                        "handoffs",
                    ]
                    assert await bridge.activity_page() == page
                    ledger = WakeLedger(
                        tmp_path / "wake",
                        WakeScope("subject", "ALPHA", "ALPHA/worker", "c" * 64, page.hub_id),
                    )
                    assert ledger.observe_page(0, page.cursor, sources=page.sources)
                    restored = WakeLedger(tmp_path / "wake", ledger.scope)
                    assert restored.scan_cursor() == page.cursor
                    empty = await bridge.activity_page(hub_id=page.hub_id, after_seq=page.cursor)
                    assert empty.items == () and empty.sources == ()
                    assert not restored.observe_page(
                        page.cursor, empty.cursor, sources=empty.sources
                    )
                    attempt = restored.acquire(100)
                    assert attempt is not None
                    assert json.loads(attempt.body)["changes"] == ["messages", "tasks", "handoffs"]
                    assert "private" not in attempt.body.decode()
                    assert len(hub.agent_sockets) == 2
                    assert not local_cursor.exists()
                    local = json.loads(await bridge.inbox())
                    assert [item["payload"] for item in local["messages"]] == ["local-only"]
                    assert local_cursor.exists()
            finally:
                await close_agents(publisher)
    finally:
        store.close()


@pytest.mark.parametrize("terminal", ["done", "abort"])
async def test_stream_progress_cannot_trigger_a_model_wake(tmp_path: Path, terminal: str) -> None:
    store = EventStore(tmp_path / "hub.db")
    hub = SynapseHub(journal=store, hub_id="stream-wake", anti_rollback_checkpoint=False)
    try:
        async with running_hub(hub) as (_, uri):
            publisher = await connect_agent("ALPHA/publisher", uri)
            try:
                async with connected_bridge(uri) as bridge:
                    assert (await bridge.activity_page()).items == ()
                    producer = StreamProducer("wake-test-stream")
                    for frame in (producer.open(), producer.chunk("private stream text")):
                        await publisher.agent.send_message(
                            MessageType.CHAT,
                            target="ALPHA/worker",
                            payload=frame.text,
                            **encode_stream_frame(frame),
                        )
                    await fence(publisher)
                    partial = await bridge.activity_page()
                    assert partial.cursor > 0 and partial.items == ()
                    ledger = WakeLedger(
                        tmp_path / "wake",
                        WakeScope("subject", "ALPHA", "ALPHA/worker", "f" * 64, partial.hub_id),
                    )
                    assert not ledger.observe_page(0, partial.cursor, sources=partial.sources)
                    assert ledger.acquire(100) is None
                    frame = producer.done() if terminal == "done" else producer.abort("stopped")
                    await publisher.agent.send_message(
                        MessageType.CHAT,
                        target="ALPHA/worker",
                        payload=frame.text,
                        **encode_stream_frame(frame),
                    )
                    await fence(publisher)
                    finished = await bridge.activity_page(
                        hub_id=partial.hub_id, after_seq=partial.cursor
                    )
                    assert finished.sources == ("messages",) and len(finished.items) == 1
                    assert ledger.observe_page(
                        partial.cursor, finished.cursor, sources=finished.sources
                    )
                    assert ledger.acquire(101) is not None
                    inbox = json.loads(await bridge.inbox_page())
                    assert [item["frame_type"] for item in inbox["messages"]] == [
                        "open",
                        "chunk",
                        terminal,
                    ]
                    assert [item["stream_seq"] for item in inbox["messages"]] == [0, 1, 2]
            finally:
                await close_agents(publisher)
    finally:
        store.close()


@pytest.mark.parametrize(
    "changes",
    [
        {"version": True},
        {"version": 2},
        {"available": False},
        {"identity": "ALPHA/foreign"},
        {"hub_id": ""},
        {"hub_id": "foreign"},
        {"extra": "private diagnostic"},
        {"cursor": True},
        {"cursor": -1},
        {"cursor": 2**63},
        {"cursor": 0, "has_more": True},
        {"has_more": "yes"},
        {"items": {}},
        {"items": [None]},
        {"items": [{"seq": 1}]},
        {"items": [{"seq": True, "kind": "messages"}]},
        {"items": [{"seq": 0, "kind": "messages"}]},
        {"items": [{"seq": 9, "kind": "messages"}]},
        {"items": [{"seq": 1, "kind": ["messages"]}]},
        {"items": [{"seq": 1, "kind": "unknown"}]},
        {"items": [{"seq": 1, "kind": "streams"}]},
        {"items": [{"seq": 1, "kind": "messages", "body": "private"}]},
        {"items": [{"seq": 1, "kind": "messages"}] * 2},
        {"items": [{"seq": 1, "kind": "messages"}] * 101},
    ],
)
async def test_untrusted_wire_pages_cannot_reach_observation_state(
    tmp_path: Path, changes: dict[str, Any]
) -> None:
    async def endpoint(socket: ServerConnection) -> None:
        await socket.send(json.dumps({"type": "welcome", "hub_id": "fake-hub"}))
        async for raw in socket:
            request = json.loads(raw)
            if request.get("type") != MessageType.HISTORY_REQUEST:
                continue
            page = {
                "version": 1,
                "available": True,
                "identity": "ALPHA/worker",
                "hub_id": "fake-hub",
                "cursor": 5,
                "has_more": False,
                "items": [{"seq": 1, "kind": "messages"}],
                **changes,
            }
            await socket.send(
                json.dumps(
                    {
                        "type": MessageType.HISTORY_SNAPSHOT,
                        "target": "ALPHA/worker",
                        "request_id": "unrelated",
                        "activity_page": page,
                    }
                )
            )
            await socket.send(
                json.dumps(
                    {
                        "type": MessageType.HISTORY_SNAPSHOT,
                        "target": "ALPHA/worker",
                        "request_id": request["request_id"],
                        "activity_page": page,
                    }
                )
            )

    async with serve(endpoint, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        async with connected_bridge(f"ws://127.0.0.1:{port}") as bridge:
            ledger = WakeLedger(
                tmp_path / "wake",
                WakeScope("subject", "ALPHA", "ALPHA/worker", "d" * 64, "fake-hub"),
            )
            with pytest.raises(RuntimeError) as failure:
                await bridge.activity_page()
            assert str(failure.value) == (
                "durable activity page unavailable; observer cursor unchanged"
            )
            assert not ledger.path.exists()


@pytest.mark.parametrize(
    "mode",
    ["legacy", "wrong-target", "rebound", "timeout", "bound-mismatch", "blank-hub", "long-hub"],
)
async def test_legacy_uncorrelated_or_rebound_connection_refuses(mode: str) -> None:
    async def endpoint(socket: ServerConnection) -> None:
        actual_hub = "" if mode == "blank-hub" else "x" * 257 if mode == "long-hub" else "fake-hub"
        await socket.send(json.dumps({"type": "welcome", "hub_id": actual_hub}))
        async for raw in socket:
            request = json.loads(raw)
            if request.get("type") != MessageType.HISTORY_REQUEST:
                continue
            if mode == "timeout":
                continue
            frame: dict[str, Any] = {
                "type": MessageType.HISTORY_SNAPSHOT,
                "target": "ALPHA/worker",
                "request_id": request["request_id"],
            }
            if mode == "legacy":
                frame["history"] = []
            else:
                frame["activity_page"] = {
                    "version": 1,
                    "available": True,
                    "identity": "ALPHA/worker",
                    "hub_id": actual_hub,
                    "cursor": 1,
                    "items": [],
                    "has_more": False,
                }
            if mode == "wrong-target":
                frame["target"] = "ALPHA/foreign"
            if mode == "rebound":
                await socket.send(json.dumps({"type": "welcome", "hub_id": "new-hub"}))
                frame["activity_page"]["hub_id"] = "new-hub"
            await socket.send(json.dumps(frame))

    async with serve(endpoint, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        async with connected_bridge(f"ws://127.0.0.1:{port}") as bridge:
            with pytest.raises(RuntimeError, match="observer cursor unchanged"):
                await bridge.activity_page(hub_id="foreign" if mode == "bound-mismatch" else "")


@pytest.mark.parametrize(
    "arguments",
    [
        {"after_seq": True},
        {"after_seq": -1},
        {"after_seq": 1},
        {"hub_id": None},
        {"hub_id": "x" * 257},
        {"limit": True},
        {"limit": 0},
        {"limit": 101},
    ],
)
async def test_invalid_request_bounds_refuse_before_connection(arguments: dict[str, Any]) -> None:
    bridge = SynapseHubBridge(name="ALPHA/worker", uri="ws://127.0.0.1:1")
    with pytest.raises(ValueError, match="invalid activity query"):
        await bridge.activity_page(**arguments)


async def test_unadmitted_connection_is_explicitly_unavailable() -> None:
    bridge = SynapseHubBridge(name="ALPHA/worker", uri="ws://127.0.0.1:1")
    with pytest.raises(RuntimeError, match="observer cursor unchanged"):
        await bridge.activity_page()
