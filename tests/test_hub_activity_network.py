# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — native directed activity restart and authorisation journeys
"""Read chat, assignment and handoff pointers through genuine admitted clients."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest

from hub_e2e_helpers import AgentHandle, close_agents, connect_agent, running_hub
from synapse_channel.core.acl import MAILBOX, MESSAGE, AclPolicy, AclRule
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.notification_events import NOTIFICATION_FIELD
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType


async def query(
    handle: AgentHandle,
    changes: dict[str, object] | None = None,
    *,
    extras: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Request an independently correlated metadata page from the actual hub."""
    request_id = uuid.uuid4().hex
    fields: dict[str, object] = {
        "version": 1,
        "identity": handle.agent.name,
        "hub_id": "",
        "since_seq": 0,
        "limit": 100,
    }
    fields.update(changes or {})
    await handle.agent.send_message(
        MessageType.HISTORY_REQUEST,
        target="System",
        request_id=request_id,
        activity_query=fields,
        **(extras or {}),
    )
    frame = await handle.recorder.wait_for(
        lambda item: item.get("request_id") == request_id and "activity_page" in item
    )
    page: dict[str, Any] = frame["activity_page"]
    return page


async def fence(handle: AgentHandle) -> None:
    """Wait for a real reply after all earlier writes on this connection."""
    handle.recorder.messages.clear()
    await handle.agent.request_board()
    await handle.recorder.wait_for(lambda item: item.get("type") == MessageType.BOARD_SNAPSHOT)


@pytest.mark.parametrize("keyed", [False, True])
async def test_all_directed_families_survive_actual_hub_restart(
    tmp_path: Path, keyed: bool
) -> None:
    first: dict[str, Any] | None = None
    for incarnation in range(2):
        store = EventStore(tmp_path / "hub.db")
        hub = SynapseHub(journal=store, hub_id="activity-hub", anti_rollback_checkpoint=False)
        try:
            async with running_hub(hub) as (_, uri):
                operator = await connect_agent("ALPHA/operator", uri)
                worker = await connect_agent("ALPHA/worker", uri)
                try:
                    if not incarnation:
                        await operator.agent.chat(
                            "private text must not leave the inbox", target="ALPHA/worker"
                        )
                        await operator.agent.chat("routine broadcast", target="all")
                        await worker.agent.chat("own echo", target="ALPHA/worker")
                        await fence(worker)
                        key = "assign-once" if keyed else None
                        await operator.agent.post_task(
                            "ALPHA/plan",
                            "private plan text",
                            suggested_owner="ALPHA/worker",
                            project="ALPHA",
                            idem_key=key,
                        )
                        await fence(operator)
                        if keyed:
                            await operator.agent.post_task(
                                "ALPHA/plan",
                                "private plan text",
                                suggested_owner="ALPHA/worker",
                                project="ALPHA",
                                idem_key=key,
                            )
                            await fence(operator)
                        await worker.agent.update_ledger_task("ALPHA/plan", status="in_progress")
                        await worker.agent.post_task(
                            "ALPHA/self", "self assignment", suggested_owner="ALPHA/worker"
                        )
                        await fence(worker)
                        await operator.agent.update_ledger_task(
                            "ALPHA/plan", suggested_owner="ALPHA/worker"
                        )
                        await operator.agent.post_task(
                            "BETA/foreign",
                            "foreign project",
                            suggested_owner="ALPHA/worker",
                            project="BETA",
                        )
                        await operator.agent.send_message(
                            MessageType.LEDGER_TASK,
                            target="System",
                            task_id="ALPHA/forged",
                            title="unassigned",
                            _notification={
                                "version": 1,
                                "kind": "tasks",
                                "actor": "ALPHA/operator",
                                "recipient": "ALPHA/worker",
                            },
                        )
                        await operator.agent.claim("ALPHA/lease", ttl_seconds=120)
                        await operator.recorder.wait_for(
                            lambda item: (
                                item.get("type") == MessageType.CLAIM_GRANTED
                                and item.get("task_id") == "ALPHA/lease"
                            )
                        )
                        await operator.agent.handoff(
                            "ALPHA/lease",
                            "ALPHA/worker",
                            note="private checkpoint",
                            idem_key="handoff-once" if keyed else None,
                        )
                        handoff = await worker.recorder.wait_for(
                            lambda item: item.get("type") == MessageType.HANDOFF_GRANTED
                        )
                        assert handoff[NOTIFICATION_FIELD]["recipient"] == "ALPHA/worker"
                        await fence(operator)
                    assert hub.blackboard.tasks["ALPHA/plan"].suggested_owner == "ALPHA/worker"
                    assert hub.state.claims["ALPHA/lease"].owner == "ALPHA/worker"
                    page = await query(worker)
                    assert page["available"] is True
                    assert [item["kind"] for item in page["items"]] == [
                        "messages",
                        "tasks",
                        "handoffs",
                    ]
                    assert all(set(item) == {"seq", "kind"} for item in page["items"])
                    assert "private" not in str(page)
                    assert await query(worker) == page
                    if first is None:
                        first = page
                    assert page == first
                    empty = await query(
                        worker, {"hub_id": page["hub_id"], "since_seq": page["cursor"]}
                    )
                    assert empty["items"] == [] and empty["available"] is True
                    rejected: list[dict[str, object]] = [
                        {"identity": "ALPHA/operator"},
                        {"hub_id": "foreign"},
                        {"since_seq": 2**63 - 1, "hub_id": "activity-hub"},
                        {"version": True},
                        {"limit": True},
                    ]
                    for changes in rejected:
                        denied = await query(worker, changes)
                        assert denied["available"] is False
                        assert denied["error"] == "activity page unavailable"
                    ambiguous = await query(worker, extras={"inbox_query": {}})
                    assert ambiguous["available"] is False
                finally:
                    await close_agents(operator, worker)
        finally:
            store.close()


async def test_missing_journal_never_advertises_activity_availability() -> None:
    async with running_hub() as (_, uri):
        worker = await connect_agent("ALPHA/worker", uri)
        try:
            assert (await query(worker))["available"] is False
        finally:
            await close_agents(worker)


async def test_activity_storage_failure_has_fixed_private_reply(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "hub.db")
    hub = SynapseHub(journal=store, anti_rollback_checkpoint=False)
    try:
        async with running_hub(hub) as (_, uri):
            worker = await connect_agent("ALPHA/worker", uri)
            try:
                store.close()
                page = await query(worker)
                assert page["available"] is False
                assert page["error"] == "activity page unavailable"
                assert page["items"] == []
                assert "sqlite" not in str(page).lower()
            finally:
                await close_agents(worker)
    finally:
        store.close()


@pytest.mark.parametrize("request_id", [None, "", "x" * 129])
async def test_invalid_correlation_never_echoes_unbounded_input(request_id: object) -> None:
    async with running_hub() as (_, uri):
        worker = await connect_agent("ALPHA/worker", uri)
        try:
            await worker.agent.send_message(
                MessageType.HISTORY_REQUEST,
                request_id=request_id,
                activity_query={
                    "version": 1,
                    "identity": "ALPHA/worker",
                    "hub_id": "",
                    "since_seq": 0,
                    "limit": 10,
                },
            )
            frame = await worker.recorder.wait_for(lambda item: "activity_page" in item)
            assert "request_id" not in frame
        finally:
            await close_agents(worker)


async def test_activity_uses_exact_mailbox_acl_without_global_recall(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "hub.db")
    policy = AclPolicy(
        [
            AclRule(MAILBOX, "agent", "ALPHA/worker", namespace="ALPHA"),
            AclRule(MESSAGE, "agent", "*", namespace="ALPHA"),
        ]
    )
    hub = SynapseHub(journal=store, acl_policy=policy, require_acl=True)
    try:
        async with running_hub(hub) as (_, uri):
            worker = await connect_agent("ALPHA/worker", uri)
            outsider = await connect_agent("BETA/outsider", uri)
            try:
                assert (await query(worker))["available"] is True
                await worker.agent.request_history()
                denied = await worker.recorder.wait_for(
                    lambda item: item.get("type") == MessageType.ERROR
                )
                assert "acl" in str(denied).lower()
                await outsider.agent.send_message(
                    MessageType.HISTORY_REQUEST,
                    activity_query={
                        "version": 1,
                        "identity": "ALPHA/worker",
                        "hub_id": "",
                        "since_seq": 0,
                        "limit": 10,
                    },
                )
                denied = await outsider.recorder.wait_for(
                    lambda item: item.get("type") == MessageType.ERROR
                )
                assert "acl" in str(denied).lower() and "activity_page" not in denied
            finally:
                await close_agents(worker, outsider)
    finally:
        store.close()
