# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — exact native pending work network contracts
"""Read legacy work through admitted clients and deny foreign or partial snapshots."""

from __future__ import annotations

import time
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from hub_e2e_helpers import AgentHandle, close_agents, connect_agent, running_hub
from synapse_channel.core.acl import MAILBOX, MESSAGE, AclPolicy, AclRule
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType
from test_pending_index import claim, task


async def pending_query(handle: AgentHandle, **changes: Any) -> dict[str, Any]:
    request_id = uuid.uuid4().hex
    query = {"version": 1, "identity": handle.agent.name, "hub_id": "", "limit": 1024} | changes
    await handle.agent.send_message(
        MessageType.HISTORY_REQUEST, target="System", request_id=request_id, pending_query=query
    )
    response = await handle.recorder.wait_for(lambda x: x.get("request_id") == request_id)
    assert response["target"] == handle.agent.name
    result: dict[str, Any] = response["pending_work"]
    return result


def seed_legacy(store: EventStore) -> None:
    store.append(EventKind.LEDGER_TASK, task().as_dict())
    store.append(EventKind.LEDGER_TASK, task("ALPHA/self", created_by="ALPHA/worker").as_dict())
    store.append(EventKind.LEDGER_TASK, task("ALPHA/done", status="done").as_dict())
    store.append(
        EventKind.LEDGER_TASK,
        task("BETA/foreign", project="BETA", suggested_owner="BETA/worker").as_dict(),
    )
    store.append(
        EventKind.HANDOFF,
        claim(claimed_at=time.time(), lease_expires_at=time.time() + 600).as_persisted_dict(),
    )


async def test_native_restart_retains_exact_historical_work_without_bodies(tmp_path: Path) -> None:
    first: dict[str, Any] | None = None
    for incarnation in range(2):
        store = EventStore(tmp_path / "hub.db")
        if not incarnation:
            seed_legacy(store)
        hub = SynapseHub(journal=store, hub_id="pending-hub", anti_rollback_checkpoint=False)
        try:
            async with running_hub(hub) as (_, uri):
                worker = await connect_agent("ALPHA/worker", uri)
                try:
                    result = await pending_query(worker)
                    assert result["available"] is True
                    assert [x["kind"] for x in result["items"]] == ["handoffs", "tasks"]
                    assert all(len(x["key"]) == 64 for x in result["items"])
                    assert "private" not in str(result) and "ALPHA/old" not in str(result)
                    if first is None:
                        first = result
                    else:
                        assert result == first
                    assert (await pending_query(worker, limit=1))["available"] is False
                finally:
                    await close_agents(worker)
        finally:
            store.close()


@pytest.mark.parametrize(
    "changes",
    [
        {"version": True},
        {"version": 2},
        {"identity": "BETA/worker"},
        {"identity": ""},
        {"hub_id": "foreign"},
        {"limit": True},
        {"limit": 0},
        {"limit": 1025},
        {"extra": "selector"},
    ],
)
async def test_invalid_or_foreign_native_pending_query_is_unavailable(
    tmp_path: Path,
    changes: dict[str, Any],
) -> None:
    store = EventStore(tmp_path / "hub.db")
    hub = SynapseHub(journal=store, hub_id="pending-hub", anti_rollback_checkpoint=False)
    try:
        async with running_hub(hub) as (_, uri):
            worker = await connect_agent("ALPHA/worker", uri)
            try:
                result = await pending_query(worker, **changes)
                assert result["available"] is False and result["items"] == []
                assert result["error"] == "pending work unavailable"
            finally:
                await close_agents(worker)
    finally:
        store.close()


@pytest.mark.parametrize("mode", ["no-journal", "mixed", "corrupt"])
async def test_incomplete_or_mixed_native_projection_cannot_look_empty(
    tmp_path: Path, mode: str
) -> None:
    store = EventStore(tmp_path / "hub.db")
    if mode == "corrupt":
        import sqlite3

        store.append("chat", {"payload": "private"})
        with closing(sqlite3.connect(tmp_path / "hub.db")) as database, database:
            database.execute("UPDATE events SET payload='not JSON'")
    hub = SynapseHub(
        journal=None if mode == "no-journal" else store, anti_rollback_checkpoint=False
    )
    try:
        async with running_hub(hub) as (_, uri):
            worker = await connect_agent("ALPHA/worker", uri)
            try:
                if mode == "mixed":
                    await worker.agent.send_message(
                        MessageType.HISTORY_REQUEST,
                        request_id="mixed",
                        pending_query={
                            "version": 1,
                            "identity": worker.agent.name,
                            "hub_id": "",
                            "limit": 1,
                        },
                        activity_query={},
                    )
                    response = await worker.recorder.wait_for(
                        lambda x: x.get("request_id") == "mixed"
                    )
                    assert response["pending_work"]["available"] is False
                else:
                    assert (await pending_query(worker))["available"] is False
            finally:
                await close_agents(worker)
    finally:
        store.close()


async def test_pending_work_requires_only_exact_mailbox_permission(tmp_path: Path) -> None:
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
                assert (await pending_query(worker))["available"] is True
                await worker.agent.request_history()
                assert (
                    "acl"
                    in str(
                        await worker.recorder.wait_for(lambda x: x.get("type") == "error")
                    ).lower()
                )
                await outsider.agent.send_message(
                    MessageType.HISTORY_REQUEST,
                    pending_query={
                        "version": 1,
                        "identity": "ALPHA/worker",
                        "hub_id": "",
                        "limit": 1,
                    },
                )
                denied = await outsider.recorder.wait_for(lambda x: x.get("type") == "error")
                assert "acl" in str(denied).lower() and "pending_work" not in denied
            finally:
                await close_agents(worker, outsider)
    finally:
        store.close()
