# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real WebSocket tests for client ledger envelopes

from __future__ import annotations

from client_helpers import connected_recording_agent, wait_for_recorded_count
from synapse_channel.core.task_causality import TaskCausalParent


async def test_post_task_sends_full_and_minimal_envelopes() -> None:
    parent = TaskCausalParent("hub-a", 3, "a" * 64)
    async with connected_recording_agent("A") as (agent, messages):
        await agent.post_task(
            "  T1 ",
            "Parser",
            description="d",
            depends_on=["T0"],
            suggested_owner="FAST",
            causal_parent=parent,
            idem_key="declare-1",
        )
        await wait_for_recorded_count(messages, 2)
        full = messages[-1]
        await agent.post_task("T2", "Bare")
        await wait_for_recorded_count(messages, 3)
        minimal = messages[-1]

    assert full["type"] == "ledger_task"
    assert full["task_id"] == "T1"
    assert full["title"] == "Parser"
    assert full["depends_on"] == ["T0"]
    assert full["suggested_owner"] == "FAST"
    assert full["idem_key"] == "declare-1"
    assert full["causal_parent"] == parent.to_dict()
    assert "description" not in minimal
    assert "depends_on" not in minimal
    assert "suggested_owner" not in minimal
    assert "idem_key" not in minimal
    assert "causal_parent" not in minimal


async def test_update_ledger_task_sends_status_and_owner() -> None:
    parent = TaskCausalParent("hub-a", 4, "b" * 64)
    async with connected_recording_agent("A") as (agent, messages):
        await agent.update_ledger_task(
            "T1",
            status="done",
            suggested_owner="",
            causal_parent=parent,
            idem_key="update-1",
        )
        await wait_for_recorded_count(messages, 2)
        sent = messages[-1]
        await agent.update_ledger_task("T1")
        await wait_for_recorded_count(messages, 3)
        bare = messages[-1]

    assert sent["type"] == "ledger_task_update"
    assert sent["status"] == "done"
    assert sent["suggested_owner"] == ""
    assert sent["idem_key"] == "update-1"
    assert sent["causal_parent"] == parent.to_dict()
    assert "status" not in bare
    assert "suggested_owner" not in bare
    assert "idem_key" not in bare
    assert "causal_parent" not in bare


async def test_post_progress_sends_kind_and_text() -> None:
    async with connected_recording_agent("A") as (agent, messages):
        await agent.post_progress(
            "  T1 ", "blocked on review", kind="blocked", idem_key="progress-1"
        )
        await wait_for_recorded_count(messages, 2)
        sent = messages[-1]

    assert sent["type"] == "ledger_progress"
    assert sent["task_id"] == "T1"
    assert sent["kind"] == "blocked"
    assert sent["payload"] == "blocked on review"
    assert sent["idem_key"] == "progress-1"


async def test_request_board_sends_board_request() -> None:
    async with connected_recording_agent("A") as (agent, messages):
        await agent.request_board()
        await wait_for_recorded_count(messages, 2)
        sent = messages[-1]

    assert sent["type"] == "board_request"


async def test_advertise_sends_full_and_minimal_cards() -> None:
    async with connected_recording_agent("A") as (agent, messages):
        await agent.advertise(
            description="quick",
            skills=["ollama"],
            task_classes=["chat"],
            model="m",
            meta={"k": 1},
            contracts=[
                {
                    "task_class": "chat",
                    "input_schema": {"type": "object"},
                    "output_schema": {"type": "string"},
                }
            ],
        )
        await wait_for_recorded_count(messages, 2)
        full = messages[-1]
        await agent.advertise()
        await wait_for_recorded_count(messages, 3)
        minimal = messages[-1]

    assert full["type"] == "advertise"
    assert full["task_classes"] == ["chat"]
    assert full["model"] == "m"
    assert full["meta"] == {"k": 1}
    assert full["contracts"] == [
        {
            "task_class": "chat",
            "input_schema": {"type": "object"},
            "output_schema": {"type": "string"},
        }
    ]
    assert "skills" not in minimal
    assert "task_classes" not in minimal
    assert "model" not in minimal
    assert "contracts" not in minimal


async def test_request_manifest_sends_manifest_request() -> None:
    async with connected_recording_agent("A") as (agent, messages):
        await agent.request_manifest()
        await wait_for_recorded_count(messages, 2)
        sent = messages[-1]

    assert sent["type"] == "manifest_request"
