# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real transport durable inbox tests
"""Exercise journal inbox reads through admitted WebSocket connections."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest

from hub_e2e_helpers import AgentHandle, close_agents, connect_agent, running_hub
from synapse_channel.core.acl import MAILBOX, MESSAGE, AclPolicy, AclRule
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.persistence import EventStore
from test_cli_lock_legacy_runtime import legacy_release_profile as legacy_release_profile


async def query(
    handle: AgentHandle,
    *,
    identity: Any = None,
    since: Any = 0,
    hub_id: Any = "",
    limit: Any = 50,
    **fields: Any,
) -> dict[str, Any]:
    request_id = uuid.uuid4().hex
    await handle.agent.send_message(
        "history_request",
        target="System",
        request_id=request_id,
        inbox_query={
            "version": 1,
            "identity": identity or handle.agent.name,
            "since_seq": since,
            "hub_id": hub_id,
            "limit": limit,
            **fields,
        },
    )
    result = await handle.recorder.wait_for(
        lambda frame: (
            frame.get("type") == "history_snapshot" and frame.get("request_id") == request_id
        ),
        timeout=10,
    )
    page: dict[str, Any] = result["inbox_page"]
    return page


async def fence(handle: AgentHandle) -> None:
    handle.recorder.messages.clear()
    await handle.agent.request_who()
    await handle.recorder.wait_for(lambda frame: frame.get("type") == "who_snapshot", timeout=10)


async def test_exact_recipients_private_channels_and_own_messages(tmp_path: Path) -> None:
    journal = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(SynapseHub(journal=journal)) as (_, uri):
            publisher = await connect_agent("P/publisher", uri)
            reader = await connect_agent("P/reader", uri)
            try:
                for target, payload in [
                    ("P/reader", "exact"),
                    ("P/foreign,P/reader", "comma"),
                    ("P/*", "wildcard"),
                    ("all", "broadcast"),
                    ("P/foreign", "foreign"),
                ]:
                    await publisher.agent.chat(payload, target=target)
                await reader.agent.chat("own", target="all")
                await publisher.agent.send_message(
                    "channel_create", channel="private", label="Private"
                )
                await publisher.recorder.wait_for(
                    lambda m: m.get("type") == "channel_result" and m.get("ok") is True
                )
                await publisher.agent.chat("private secret", channel="private")
                await fence(publisher)
                await fence(reader)
                page = await query(reader)
                assert page["available"] is True
                assert [m["payload"] for m in page["messages"]] == [
                    "exact",
                    "comma",
                    "wildcard",
                    "broadcast",
                ]
                assert page["has_more"] is False
                again = await query(reader, since=page["cursor"], hub_id=page["hub_id"])
                assert again["messages"] == []
                foreign = await query(reader, identity="P/foreign")
                assert foreign["available"] is False
                assert foreign["messages"] == []
                assert "not owned" in foreign["error"]
                wrong = await query(reader, hub_id="another-hub")
                assert wrong["available"] is False
                assert "different hub" in wrong["error"]
            finally:
                await close_agents(publisher, reader)
    finally:
        journal.close()


async def test_long_gap_scans_foreign_rows_and_retains_old_unread(tmp_path: Path) -> None:
    journal = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(SynapseHub(journal=journal)) as (_, uri):
            publisher = await connect_agent("P/publisher", uri)
            try:
                await publisher.agent.chat("old unread", target="P/reader")
                for number in range(1001):
                    await publisher.agent.chat(f"foreign {number}", target="Q/other")
                await publisher.agent.chat("new unread", target="P/reader")
                await fence(publisher)
                reader = await connect_agent("P/reader", uri)
                try:
                    first = await query(reader)
                    assert [m["payload"] for m in first["messages"]] == ["old unread"]
                    assert first["has_more"] is True
                    second = await query(reader, since=first["cursor"], hub_id=first["hub_id"])
                    assert [m["payload"] for m in second["messages"]] == ["new unread"]
                    assert second["cursor"] > first["cursor"]
                    assert second["has_more"] is False
                finally:
                    await close_agents(reader)
            finally:
                await close_agents(publisher)
    finally:
        journal.close()


@pytest.mark.parametrize(
    "fields",
    [
        {"version": True},
        {"version": 2},
        {"identity": 4},
        {"identity": " "},
        {"since_seq": True},
        {"since_seq": -1},
        {"since_seq": 2**63},
        {"limit": True},
        {"limit": 0},
        {"limit": 101},
        {"hub_id": None},
        {"since_seq": 1, "hub_id": ""},
    ],
)
async def test_invalid_query_refuses_without_closing_connection(
    tmp_path: Path, fields: dict[str, Any]
) -> None:
    journal = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(SynapseHub(journal=journal)) as (_, uri):
            reader = await connect_agent("P/reader", uri)
            try:
                invalid = await query(reader, **fields)
                assert invalid["available"] is False
                assert invalid["messages"] == []
                assert (await query(reader))["available"] is True
            finally:
                await close_agents(reader)
    finally:
        journal.close()


async def test_missing_journal_is_explicitly_unavailable() -> None:
    async with running_hub() as (_, uri):
        reader = await connect_agent("P/reader", uri)
        try:
            page = await query(reader)
            assert page["available"] is False
            assert "requires a journal" in page["error"]
        finally:
            await close_agents(reader)


async def test_mailbox_grant_does_not_grant_global_history(tmp_path: Path) -> None:
    journal = EventStore(tmp_path / "hub.db")
    policy = AclPolicy(
        [
            AclRule(MAILBOX, "agent", "P/reader", namespace="P"),
            AclRule(MESSAGE, "agent", "*", namespace="P"),
        ]
    )
    try:
        async with running_hub(
            SynapseHub(journal=journal, acl_policy=policy, require_acl=True)
        ) as (_, uri):
            publisher = await connect_agent("P/publisher", uri)
            reader = await connect_agent("P/reader", uri)
            try:
                await publisher.agent.chat("mailbox only", target="P/reader")
                await fence(publisher)
                assert (await query(reader))["available"] is True
                await reader.agent.request_history()
                denial = await reader.recorder.wait_for(lambda m: m.get("type") == "error")
                assert "acl" in str(denial).lower()
                denied = await connect_agent("Q/denied", uri)
                try:
                    await denied.agent.send_message(
                        "history_request",
                        inbox_query={
                            "version": 1,
                            "identity": "Q/denied",
                            "since_seq": 0,
                            "hub_id": "",
                            "limit": 50,
                        },
                    )
                    await denied.recorder.wait_for(lambda m: m.get("type") == "error")
                    assert not any(m.get("inbox_page") for m in denied.recorder.messages)
                finally:
                    await close_agents(denied)
            finally:
                await close_agents(publisher, reader)
    finally:
        journal.close()


async def test_wire_refusals_cover_missing_object_and_cursor_beyond_journal(tmp_path: Path) -> None:
    journal = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(SynapseHub(journal=journal)) as (_, uri):
            reader = await connect_agent("P/reader", uri)
            try:
                request_id = uuid.uuid4().hex
                await reader.agent.send_message(
                    "history_request", request_id=request_id, inbox_query=[]
                )
                reply = await reader.recorder.wait_for(lambda m: m.get("request_id") == request_id)
                assert reply["inbox_page"]["available"] is False
                beyond = await query(reader, since=2**62, hub_id=reader.agent.hub_id)
                assert beyond["available"] is False
                assert beyond["error"] == "inbox cursor exceeds the retained journal"
            finally:
                await close_agents(reader)
    finally:
        journal.close()


async def test_encoded_page_budget_preserves_next_row_and_refuses_oversized_message(
    tmp_path: Path,
) -> None:
    journal = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(SynapseHub(journal=journal, max_msg_bytes=9 * 1024 * 1024)) as (
            _,
            uri,
        ):
            publisher = await connect_agent("P/publisher", uri)
            try:
                for prefix in ["first", "second"]:
                    await publisher.agent.chat(prefix + "🧠" * 350000, target="P/reader")
                await publisher.agent.chat("🧠" * 650000, target="P/oversized")
                await fence(publisher)
                reader = await connect_agent("P/reader", uri)
                oversized = await connect_agent("P/oversized", uri)
                try:
                    first = await query(reader)
                    assert len(first["messages"]) == 1
                    assert first["messages"][0]["payload"].startswith("first")
                    assert first["has_more"] is True
                    second = await query(reader, since=first["cursor"], hub_id=first["hub_id"])
                    assert len(second["messages"]) == 1
                    assert second["messages"][0]["payload"].startswith("second")
                    assert second["has_more"] is False
                    large = await query(oversized)
                    assert large["available"] is False
                    assert large["messages"] == []
                    assert large["error"] == "inbox message exceeds the response size limit"
                finally:
                    await close_agents(reader, oversized)
            finally:
                await close_agents(publisher)
    finally:
        journal.close()


async def test_recognized_sidecar_reads_only_its_owner_inbox(tmp_path: Path) -> None:
    from synapse_channel.core.agent_liveness import waiter_sidecar_names

    journal = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(SynapseHub(journal=journal)) as (_, uri):
            publisher = await connect_agent("P/publisher", uri)
            sidecar = await connect_agent(waiter_sidecar_names("P/reader")[0], uri)
            try:
                await publisher.agent.chat("owner body", target="P/reader")
                await fence(publisher)
                page = await query(sidecar, identity="P/reader")
                assert page["available"] is True
                assert [m["payload"] for m in page["messages"]] == ["owner body"]
                assert (await query(sidecar, identity="P/foreign"))["available"] is False
            finally:
                await close_agents(publisher, sidecar)
    finally:
        journal.close()
