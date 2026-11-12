# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — exact native pending work network contracts
"""Exercise the native owned-work reader and its distinct exact-owner ACL."""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any

import pytest

from hub_e2e_helpers import AgentHandle, close_agents, connect_agent, running_hub
from synapse_channel.core.acl import MAILBOX, OWNED_WORK, AclPolicy, AclRule
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind
from synapse_channel.core.owned_work import OwnedQuery
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType
from test_pending_index import claim, task
from test_pending_work_network import pending_query


async def owned_query(
    handle: AgentHandle, query: OwnedQuery | None = None, **selectors: Any
) -> dict[str, Any]:
    handle.recorder.messages.clear()
    request_id = uuid.uuid4().hex
    query = query or OwnedQuery(handle.agent.name)
    await handle.agent.send_message(
        MessageType.HISTORY_REQUEST,
        target="System",
        request_id=request_id,
        owned_work_query=query.encode(),
        **selectors,
    )
    return await handle.recorder.wait_for(
        lambda frame: frame.get("request_id") == request_id or frame.get("type") == "error"
    )


@pytest.mark.parametrize("permission", [MAILBOX, OWNED_WORK])
async def test_body_read_requires_owned_work_permission_not_metadata_permission(
    tmp_path: Path, permission: str
) -> None:
    store = EventStore(tmp_path / "hub.db")
    store.append(EventKind.LEDGER_TASK, task().as_dict())
    hub = SynapseHub(
        journal=store,
        acl_policy=AclPolicy([AclRule(permission, "agent", "ALPHA/worker", namespace="ALPHA")]),
        require_acl=True,
    )
    try:
        async with running_hub(hub) as (_, uri):
            worker = await connect_agent("ALPHA/worker", uri)
            try:
                response = await owned_query(worker)
                if permission == MAILBOX:
                    assert response["type"] == "error" and "acl" in str(response).lower()
                    assert (await pending_query(worker))["available"] is True
                else:
                    assert response["owned_work"]["available"] is True
                    assert len(response["owned_work"]["items"]) == 1
                    foreign = await owned_query(worker, OwnedQuery("ALPHA/other"))
                    assert foreign["type"] == "error"
                    assert (await owned_query(worker, inbox_query={}))["owned_work"][
                        "available"
                    ] is False
            finally:
                await close_agents(worker)
    finally:
        store.close()


async def test_native_restart_and_reassignment_refuse_old_generations(tmp_path: Path) -> None:
    previous: dict[str, Any] | None = None
    for incarnation in range(2):
        store = EventStore(tmp_path / "hub.db")
        if not incarnation:
            store.append(EventKind.LEDGER_TASK, task().as_dict())
            store.append(
                EventKind.HANDOFF,
                claim(
                    claimed_at=time.time(), lease_expires_at=time.time() + 600
                ).as_persisted_dict(),
            )
        hub = SynapseHub(journal=store, hub_id="owned-hub", anti_rollback_checkpoint=False)
        try:
            async with running_hub(hub) as (_, uri):
                worker = await connect_agent("ALPHA/worker", uri)
                try:
                    current = (await owned_query(worker))["owned_work"]
                    assert len(current["items"]) == 2
                    if previous:
                        stale = OwnedQuery(
                            "ALPHA/worker", hub_id="owned-hub", revision=previous["revision"]
                        )
                        assert (await owned_query(worker, stale))["owned_work"][
                            "available"
                        ] is False
                    previous = current
                    item = next(x for x in current["items"] if x["kind"] == "tasks")
                    query = OwnedQuery(
                        "ALPHA/worker",
                        hub_id="owned-hub",
                        mode="read",
                        key=item["key"],
                        item_version=item["item_version"],
                    )
                    assert (
                        "private task"
                        in (await owned_query(worker, query))["owned_work"]["content"]
                    )
                    assert hub.blackboard.update_task("ALPHA/old", suggested_owner="ALPHA/other")[0]
                    assert (await owned_query(worker, query))["owned_work"]["available"] is False
                    # Native in-memory mutation above is deliberately not journalled.
                finally:
                    await close_agents(worker)
        finally:
            store.close()


@pytest.mark.parametrize("mode", ["no-journal", "mixed", "foreign", "corrupt"])
async def test_incomplete_or_foreign_native_work_read_never_becomes_empty(
    tmp_path: Path, mode: str
) -> None:
    store = EventStore(tmp_path / "hub.db")
    if mode == "corrupt":
        import sqlite3

        store.append(EventKind.LEDGER_TASK, task().as_dict())
        with sqlite3.connect(tmp_path / "hub.db") as database:
            database.execute("UPDATE events SET payload='not JSON'")
    hub = SynapseHub(journal=None if mode == "no-journal" else store)
    try:
        async with running_hub(hub) as (_, uri):
            worker = await connect_agent("ALPHA/worker", uri)
            try:
                query = OwnedQuery("ALPHA/other" if mode == "foreign" else "ALPHA/worker")
                selectors: dict[str, Any] = {"pending_query": {}} if mode == "mixed" else {}
                assert (await owned_query(worker, query, **selectors))["owned_work"][
                    "available"
                ] is False
            finally:
                await close_agents(worker)
    finally:
        store.close()
