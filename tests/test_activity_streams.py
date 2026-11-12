# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — native stream metadata contract tests
"""Exercise scoped projection, version negotiation and real correlated pages."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from hub_e2e_helpers import close_agents, connect_agent, running_hub
from synapse_channel.core.activity_pages import ActivityQuery, read_activity_page
from synapse_channel.core.activity_streams import StreamActivity, stream_activity
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.streaming import StreamProducer, encode_stream_frame
from test_hub_activity_network import fence, query


def frame(**changes: Any) -> dict[str, Any]:
    return {
        "sender": "ALPHA/publisher",
        "target": "ALPHA/worker",
        "kind": "stream",
        "stream_id": "private-stream",
        "stream_seq": 0,
        "frame_type": "open",
    } | changes


@pytest.mark.parametrize(
    "changes",
    [
        {"target": "all"},
        {"sender": "ALPHA/worker"},
        {"sender": None},
        {"sender": ""},
        {"channel": "shared"},
        {"stream_id": None},
        {"kind": "plain"},
        {"stream_seq": True},
    ],
)
def test_irrelevant_and_malformed_streams_have_no_pointer(changes: dict[str, Any]) -> None:
    assert stream_activity(frame(**changes), "ALPHA/worker", seq=1, ts=100) is None


@pytest.mark.parametrize("ts", [float("nan"), float("inf"), -1, float(2**63)])
def test_invalid_native_timestamp_refuses(ts: float) -> None:
    with pytest.raises(ValueError, match="journal timestamp"):
        stream_activity(frame(), "ALPHA/worker", seq=1, ts=ts)


@pytest.mark.parametrize(
    "fields",
    [
        (True, "a" * 64, False, 1),
        (0, "a" * 64, False, 1),
        (1, 1, False, 1),
        (1, "A" * 64, False, 1),
        (1, "a" * 64, 0, 1),
        (1, "a" * 64, False, True),
        (1, "a" * 64, False, -1),
    ],
)
def test_stream_metadata_bounds(fields: tuple[Any, ...]) -> None:
    with pytest.raises(ValueError, match="invalid stream activity"):
        StreamActivity(*fields)


@pytest.mark.parametrize("value", [None, {}, {"key": "a" * 64, "terminal": False, "at": 1, "x": 1}])
def test_stream_metadata_shape(value: Any) -> None:
    with pytest.raises(ValueError, match="invalid stream activity"):
        StreamActivity.from_value(1, value)


def test_stream_metadata_round_trips_through_its_wire_shape() -> None:
    """A reader decodes exactly what the hub encodes, bound to the row it came with."""
    sent = StreamActivity(3, "a" * 64, True, 5)
    assert sent.encode() == {"key": "a" * 64, "terminal": True, "at": 5}
    assert StreamActivity.from_value(3, sent.encode()) == sent


def test_native_version_two_keeps_version_one_stable_and_hides_content(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        store.append("chat", frame(text="private chunk"), ts=100)
        store.append("chat", frame(frame_type="chunk", stream_seq=1), ts=101)
        store.append("chat", frame(frame_type="done", stream_seq=2), ts=102)
        store.append("chat", frame(sender="ALPHA/other"), ts=103)
        old = read_activity_page(store, ActivityQuery("ALPHA/worker", "", 0, 100), hub_id="h")
        assert old["items"] == [{"seq": 3, "kind": "messages"}] and old["version"] == 1
        new = read_activity_page(store, ActivityQuery("ALPHA/worker", "", 0, 100, 2), hub_id="h")
        items: Any = new["items"]
        assert [i["kind"] for i in items] == ["streams", "streams", "messages", "streams"]
        assert items[0]["stream"]["key"] == items[2]["stream"]["key"]
        assert items[0]["stream"]["key"] != items[3]["stream"]["key"]
        assert items[2]["stream"]["terminal"] is True
        assert items[0]["stream"]["at"] == 100
        assert "private" not in str(new) and "publisher" not in str(new)
        assert (
            ActivityQuery.from_value(
                {
                    "version": 2,
                    "identity": "ALPHA/worker",
                    "hub_id": "",
                    "since_seq": 0,
                    "limit": 100,
                }
            ).version
            == 2
        )
    finally:
        store.close()


async def test_real_native_v2_client_receives_partial_and_terminal_metadata(tmp_path: Path) -> None:
    """A connected recipient reads stream progress, then its end, with no stream text or id."""
    store = EventStore(tmp_path / "hub.db")
    hub = SynapseHub(journal=store, hub_id="stream-v2", anti_rollback_checkpoint=False)
    try:
        async with running_hub(hub) as (_, uri):
            publisher = await connect_agent("ALPHA/publisher", uri)
            worker = await connect_agent("ALPHA/worker", uri)
            try:
                producer = StreamProducer("private-live-stream")
                for part in (producer.open(), producer.chunk("private-live-text")):
                    await publisher.agent.send_message(
                        "chat", target="ALPHA/worker", **encode_stream_frame(part)
                    )
                await fence(publisher)
                assert (await query(worker))["items"] == []
                page = await query(worker, {"version": 2})
                assert page["version"] == 2 and page["hub_id"] == "stream-v2"
                assert [item["kind"] for item in page["items"]] == ["streams", "streams"]
                assert all(item["stream"]["terminal"] is False for item in page["items"])
                assert page["items"][0]["stream"]["key"] == page["items"][1]["stream"]["key"]
                assert "private" not in str(page) and "publisher" not in str(page)
                await publisher.agent.send_message(
                    "chat", target="ALPHA/worker", **encode_stream_frame(producer.done())
                )
                await fence(publisher)
                end = await query(
                    worker, {"version": 2, "hub_id": page["hub_id"], "since_seq": page["cursor"]}
                )
                assert [item["kind"] for item in end["items"]] == ["messages"]
                assert end["items"][0]["stream"]["terminal"] is True
                assert end["items"][0]["stream"]["key"] == page["items"][0]["stream"]["key"]
                assert set(hub.agent_sockets) == {"ALPHA/publisher", "ALPHA/worker"}
            finally:
                await close_agents(publisher, worker)
    finally:
        store.close()
