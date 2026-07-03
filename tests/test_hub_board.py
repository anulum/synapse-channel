# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL - end-to-end tests for hub blackboard and capability cards

from __future__ import annotations

from pathlib import Path

from hub_e2e_helpers import close_agents, connect_agent, running_hub
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.persistence import EventStore


async def test_ledger_task_posted_is_broadcast_end_to_end() -> None:
    async with running_hub() as (hub, uri):
        poster = await connect_agent("P", uri)
        watcher = await connect_agent("WATCH", uri)
        try:
            await poster.agent.post_task("T1", "Parser")
            posted = await watcher.recorder.wait_for(
                lambda m: (
                    m.get("type") == "ledger_task_posted"
                    and m.get("task", {}).get("task_id") == "T1"
                )
            )
            assert posted["task"]["created_by"] == "P"
            assert hub.blackboard.tasks["T1"].title == "Parser"
        finally:
            await close_agents(poster, watcher)


async def test_ledger_task_missing_title_errors_sender_end_to_end() -> None:
    async with running_hub() as (_, uri):
        poster = await connect_agent("P", uri)
        try:
            await poster.agent.send_message("ledger_task", task_id="T1")
            error = await poster.recorder.wait_for(lambda m: m.get("type") == "error")
            assert "title is required" in error["payload"]
        finally:
            await close_agents(poster)


async def test_ledger_task_cycle_errors_sender_end_to_end() -> None:
    async with running_hub() as (_, uri):
        poster = await connect_agent("P", uri)
        try:
            await poster.agent.post_task("A", "a")
            await poster.agent.post_task("B", "b", depends_on=["A"])
            await poster.agent.post_task("A", "a", depends_on=["B"])
            error = await poster.recorder.wait_for(lambda m: m.get("type") == "error")
            assert "cycle" in error["payload"]
        finally:
            await close_agents(poster)


async def test_ledger_task_update_broadcast_and_unknown_errors_end_to_end() -> None:
    async with running_hub() as (_, uri):
        poster = await connect_agent("P", uri)
        watcher = await connect_agent("WATCH", uri)
        try:
            await poster.agent.post_task("T1", "t")
            await poster.agent.update_ledger_task("T1", status="done")
            updated = await watcher.recorder.wait_for(
                lambda m: (
                    m.get("type") == "ledger_task_updated"
                    and m.get("task", {}).get("task_id") == "T1"
                )
            )
            assert updated["task"]["status"] == "done"
            await poster.agent.update_ledger_task("GHOST", status="done")
            error = await poster.recorder.wait_for(
                lambda m: m.get("type") == "error" and "not on the board" in str(m.get("payload"))
            )
            assert "not on the board" in error["payload"]
        finally:
            await close_agents(poster, watcher)


async def test_ledger_progress_posted_and_bad_kind_errors_end_to_end() -> None:
    async with running_hub() as (_, uri):
        poster = await connect_agent("P", uri)
        watcher = await connect_agent("WATCH", uri)
        try:
            await poster.agent.post_progress("T1", "started")
            note = await watcher.recorder.wait_for(
                lambda m: m.get("type") == "ledger_progress_posted"
            )
            assert note["note"]["text"] == "started"
            assert note["note"]["author"] == "P"
            await poster.agent.post_progress("T1", "x", kind="rant")
            error = await poster.recorder.wait_for(
                lambda m: (
                    m.get("type") == "error" and "Unknown progress kind" in str(m.get("payload"))
                )
            )
            assert "Unknown progress kind" in error["payload"]
        finally:
            await close_agents(poster, watcher)


async def test_advertise_stores_card_and_broadcasts_end_to_end() -> None:
    async with running_hub() as (hub, uri):
        fast = await connect_agent("FAST", uri)
        watcher = await connect_agent("WATCH", uri)
        try:
            await fast.agent.advertise(
                description="quick",
                skills=["ollama"],
                task_classes=["chat"],
                model="gemma3:4b",
                contracts=[
                    {
                        "task_class": " chat ",
                        "input_schema": {"type": "object"},
                        "output_schema": {"type": "string"},
                        "preconditions": ["ready", "ready"],
                    }
                ],
            )
            advertised = await watcher.recorder.wait_for(
                lambda m: m.get("type") == "capability_advertised"
            )
            assert advertised["agent"] == "FAST"
            assert advertised["card"]["task_classes"] == ["chat"]
            assert advertised["card"]["contracts"] == [
                {
                    "task_class": "chat",
                    "input_schema": {"type": "object"},
                    "output_schema": {"type": "string"},
                    "preconditions": ["ready"],
                    "postconditions": [],
                }
            ]
            card = hub.capabilities.get("FAST")
            assert card is not None and card.model == "gemma3:4b"
            assert [contract.task_class for contract in card.contracts] == ["chat"]
        finally:
            await close_agents(fast, watcher)


async def test_manifest_request_returns_advertised_agents_end_to_end() -> None:
    async with running_hub() as (_, uri):
        fast = await connect_agent("FAST", uri)
        user = await connect_agent("USER", uri)
        try:
            await fast.agent.advertise(
                task_classes=["chat"],
                contracts=[
                    {
                        "task_class": "chat",
                        "input_schema": {"type": "object"},
                    }
                ],
            )
            await fast.recorder.wait_for(lambda m: m.get("type") == "capability_advertised")
            await user.agent.request_manifest()
            snap = await user.recorder.wait_for(lambda m: m.get("type") == "manifest_snapshot")
            assert [c["agent"] for c in snap["manifest"]] == ["FAST"]
            assert snap["manifest"][0]["contracts"][0]["task_class"] == "chat"
        finally:
            await close_agents(fast, user)


async def test_capability_card_dropped_on_disconnect_end_to_end() -> None:
    async with running_hub() as (hub, uri):
        fast = await connect_agent("FAST", uri)
        await fast.agent.advertise(task_classes=["chat"])
        await fast.recorder.wait_for(lambda m: m.get("type") == "capability_advertised")
        assert hub.capabilities.get("FAST") is not None
        await fast.close()
        assert hub.capabilities.get("FAST") is None


async def test_board_request_returns_snapshot_end_to_end() -> None:
    async with running_hub() as (_, uri):
        poster = await connect_agent("P", uri)
        try:
            await poster.agent.post_task("T1", "t")
            await poster.agent.request_board()
            snap = await poster.recorder.wait_for(lambda m: m.get("type") == "board_snapshot")
            assert snap["board"]["tasks"][0]["task_id"] == "T1"
            assert snap["board"]["ready"] == ["T1"]
        finally:
            await close_agents(poster)


async def test_hub_replays_ledger_tasks_progress_and_updates(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    store_a = EventStore(db)
    async with running_hub(SynapseHub(hub_id="syn-a", journal=store_a)) as (_, uri):
        poster = await connect_agent("P", uri)
        try:
            await poster.agent.post_task("T1", "Parser")
            await poster.recorder.wait_for(lambda m: m.get("type") == "ledger_task_posted")
            await poster.agent.update_ledger_task("T1", status="in_progress")
            await poster.recorder.wait_for(lambda m: m.get("type") == "ledger_task_updated")
            await poster.agent.post_progress("T1", "started")
            await poster.recorder.wait_for(lambda m: m.get("type") == "ledger_progress_posted")
        finally:
            await close_agents(poster)
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
    async with running_hub(SynapseHub(hub_id="syn-a", journal=store_a, max_progress=2)) as (_, uri):
        poster = await connect_agent("P", uri)
        try:
            for index in range(4):
                await poster.agent.post_progress("T", str(index))
            await poster.recorder.wait_for(
                lambda m: (
                    m.get("type") == "ledger_progress_posted"
                    and m.get("note", {}).get("text") == "3"
                )
            )
        finally:
            await close_agents(poster)
    store_a.close()

    store_b = EventStore(db)
    hub_b = SynapseHub(hub_id="syn-b", journal=store_b, max_progress=2)
    store_b.close()
    assert [n.text for n in hub_b.blackboard.progress] == ["2", "3"]


async def test_capped_hub_bounds_the_board_snapshot_and_says_so() -> None:
    async with running_hub(SynapseHub(board_task_cap=2)) as (_, uri):
        poster = await connect_agent("P", uri)
        try:
            for index in range(4):
                want = f"T{index}"
                await poster.agent.post_task(want, "t")

                def posted(m: dict[str, object], want: str = want) -> bool:
                    task = m.get("task")
                    return (
                        m.get("type") == "ledger_task_posted"
                        and isinstance(task, dict)
                        and task.get("task_id") == want
                    )

                await poster.recorder.wait_for(posted)
            await poster.agent.request_board()
            snap = await poster.recorder.wait_for(lambda m: m.get("type") == "board_snapshot")
            board = snap["board"]
            assert len(board["tasks"]) == 2
            assert board["total_tasks"] == 4
            assert board["truncated"] is True
            # every ready id survives the cap — ids are cheap
            assert set(board["ready"]) == {"T0", "T1", "T2", "T3"}
        finally:
            await close_agents(poster)


async def test_uncapped_hub_serves_the_full_board_without_bound_metadata() -> None:
    async with running_hub() as (_, uri):
        poster = await connect_agent("P", uri)
        try:
            await poster.agent.post_task("T1", "t")
            await poster.agent.request_board()
            snap = await poster.recorder.wait_for(lambda m: m.get("type") == "board_snapshot")
            assert "total_tasks" not in snap["board"]
            assert "truncated" not in snap["board"]
        finally:
            await close_agents(poster)
