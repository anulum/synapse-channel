# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the routing hub using fake server sockets

from __future__ import annotations

from pathlib import Path

from hub_helpers import FakeServerWS, _hub, _msg
from synapse_channel.core.hub import (
    SynapseHub,
)
from synapse_channel.core.persistence import EventStore

# --- shared blackboard -------------------------------------------------------


async def test_ledger_task_posted_is_broadcast() -> None:
    hub = _hub()
    ws = FakeServerWS()
    await hub.register(ws)
    await hub.handle_message(_msg(sender="P", type="ledger_task", task_id="T1", title="Parser"), ws)
    posted = [m for m in ws.decoded() if m.get("type") == "ledger_task_posted"]
    assert posted[-1]["task"]["task_id"] == "T1"
    assert posted[-1]["task"]["created_by"] == "P"
    assert hub.blackboard.tasks["T1"].title == "Parser"


async def test_ledger_task_missing_title_errors_sender() -> None:
    hub = _hub()
    ws = FakeServerWS()
    await hub.register(ws)
    await hub.handle_message(_msg(sender="P", type="ledger_task", task_id="T1"), ws)
    assert ws.last()["type"] == "error"
    assert "title is required" in ws.last()["payload"]


async def test_ledger_task_cycle_errors_sender() -> None:
    hub = _hub()
    ws = FakeServerWS()
    await hub.register(ws)
    await hub.handle_message(_msg(sender="P", type="ledger_task", task_id="A", title="a"), ws)
    await hub.handle_message(
        _msg(sender="P", type="ledger_task", task_id="B", title="b", depends_on=["A"]), ws
    )
    await hub.handle_message(
        _msg(sender="P", type="ledger_task", task_id="A", title="a", depends_on=["B"]), ws
    )
    assert ws.last()["type"] == "error"
    assert "cycle" in ws.last()["payload"]


async def test_ledger_task_update_broadcast_and_unknown_errors() -> None:
    hub = _hub()
    ws = FakeServerWS()
    await hub.register(ws)
    await hub.handle_message(_msg(sender="P", type="ledger_task", task_id="T1", title="t"), ws)
    await hub.handle_message(
        _msg(sender="P", type="ledger_task_update", task_id="T1", status="done"), ws
    )
    updated = [m for m in ws.decoded() if m.get("type") == "ledger_task_updated"]
    assert updated[-1]["task"]["status"] == "done"

    await hub.handle_message(
        _msg(sender="P", type="ledger_task_update", task_id="GHOST", status="done"), ws
    )
    assert ws.last()["type"] == "error"
    assert "not on the board" in ws.last()["payload"]


async def test_ledger_progress_posted_and_bad_kind_errors() -> None:
    hub = _hub()
    ws = FakeServerWS()
    await hub.register(ws)
    await hub.handle_message(
        _msg(sender="P", type="ledger_progress", task_id="T1", payload="started"), ws
    )
    notes = [m for m in ws.decoded() if m.get("type") == "ledger_progress_posted"]
    assert notes[-1]["note"]["text"] == "started"
    assert notes[-1]["note"]["author"] == "P"

    await hub.handle_message(
        _msg(sender="P", type="ledger_progress", task_id="T1", payload="x", kind="rant"), ws
    )
    assert ws.last()["type"] == "error"
    assert "Unknown progress kind" in ws.last()["payload"]


# --- capability cards --------------------------------------------------------


async def test_advertise_stores_card_and_broadcasts() -> None:
    hub = _hub()
    ws = FakeServerWS()
    await hub.register(ws)
    await hub.handle_message(
        _msg(
            sender="FAST",
            type="advertise",
            description="quick",
            skills=["ollama"],
            task_classes=["chat"],
            model="gemma3:4b",
        ),
        ws,
    )
    advertised = [m for m in ws.decoded() if m.get("type") == "capability_advertised"]
    assert advertised[-1]["agent"] == "FAST"
    assert advertised[-1]["card"]["task_classes"] == ["chat"]
    card = hub.capabilities.get("FAST")
    assert card is not None and card.model == "gemma3:4b"


async def test_manifest_request_returns_advertised_agents() -> None:
    hub = _hub()
    ws_fast = FakeServerWS()
    ws_user = FakeServerWS()
    await hub.register(ws_fast)
    await hub.register(ws_user)
    await hub.handle_message(_msg(sender="FAST", type="advertise", task_classes=["chat"]), ws_fast)
    await hub.handle_message(_msg(sender="USER", type="manifest_request"), ws_user)
    snap = ws_user.last()
    assert snap["type"] == "manifest_snapshot"
    assert [c["agent"] for c in snap["manifest"]] == ["FAST"]


async def test_capability_card_dropped_on_disconnect() -> None:
    hub = _hub()
    ws = FakeServerWS()
    await hub.register(ws)
    await hub.handle_message(_msg(sender="FAST", type="advertise", task_classes=["chat"]), ws)
    assert hub.capabilities.get("FAST") is not None
    await hub.unregister(ws)
    assert hub.capabilities.get("FAST") is None


async def test_board_request_returns_snapshot() -> None:
    hub = _hub()
    ws = FakeServerWS()
    await hub.register(ws)
    await hub.handle_message(_msg(sender="P", type="ledger_task", task_id="T1", title="t"), ws)
    await hub.handle_message(_msg(sender="P", type="board_request"), ws)
    snap = ws.last()
    assert snap["type"] == "board_snapshot"
    assert snap["board"]["tasks"][0]["task_id"] == "T1"
    assert snap["board"]["ready"] == ["T1"]


async def test_hub_replays_ledger_tasks_progress_and_updates(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    store_a = EventStore(db)
    hub_a = SynapseHub(hub_id="syn-a", journal=store_a)
    ws = FakeServerWS()
    await hub_a.register(ws)
    await hub_a.handle_message(
        _msg(sender="P", type="ledger_task", task_id="T1", title="Parser"), ws
    )
    await hub_a.handle_message(
        _msg(sender="P", type="ledger_task_update", task_id="T1", status="in_progress"), ws
    )
    await hub_a.handle_message(
        _msg(sender="P", type="ledger_progress", task_id="T1", payload="started"), ws
    )
    store_a.close()

    store_b = EventStore(db)
    hub_b = SynapseHub(hub_id="syn-b", journal=store_b)
    store_b.close()
    assert hub_b.blackboard.tasks["T1"].title == "Parser"
    assert hub_b.blackboard.tasks["T1"].status == "in_progress"
    assert [n.text for n in hub_b.blackboard.progress] == ["started"]


async def test_hub_replay_trims_progress_to_bound(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    store_a = EventStore(db)
    hub_a = SynapseHub(hub_id="syn-a", journal=store_a, max_progress=2)
    ws = FakeServerWS()
    await hub_a.register(ws)
    for i in range(4):
        await hub_a.handle_message(
            _msg(sender="P", type="ledger_progress", task_id="T", payload=str(i)), ws
        )
    store_a.close()

    store_b = EventStore(db)
    hub_b = SynapseHub(hub_id="syn-b", journal=store_b, max_progress=2)
    store_b.close()
    # The durable log holds all four notes; replay trims to the last two.
    assert [n.text for n in hub_b.blackboard.progress] == ["2", "3"]
