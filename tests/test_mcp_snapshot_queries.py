# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — MCP snapshot-query facade tests

from __future__ import annotations

import json
from collections.abc import Sequence

from synapse_channel.core.protocol import MessageType
from synapse_channel.mcp.snapshot_queries import Matcher, McpSnapshotQueries, Sender


class _RecordingAgent:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def request_board(self) -> None:
        self.calls.append("board")

    async def request_state(self) -> None:
        self.calls.append("state")

    async def request_manifest(self) -> None:
        self.calls.append("manifest")


class _ScriptedAwaiter:
    def __init__(self, replies: Sequence[dict[str, object] | None]) -> None:
        self.replies = list(replies)

    async def __call__(self, match: Matcher, send: Sender) -> dict[str, object] | None:
        await send()
        reply = self.replies.pop(0)
        assert reply is None or match(reply)
        return reply


def _queries(
    replies: Sequence[dict[str, object] | None],
) -> tuple[McpSnapshotQueries, _RecordingAgent]:
    agent = _RecordingAgent()
    return McpSnapshotQueries(agent, _ScriptedAwaiter(replies)), agent


async def test_single_snapshot_queries_preserve_json_and_request_order() -> None:
    queries, agent = _queries(
        [
            {"type": MessageType.BOARD_SNAPSHOT, "board": {"ready": ["T1"]}},
            {"type": MessageType.STATE_SNAPSHOT, "snapshot": {"active_claims": []}},
            {"type": MessageType.MANIFEST_SNAPSHOT, "manifest": [{"agent": "A"}]},
        ]
    )

    assert json.loads(await queries.board()) == {"ready": ["T1"]}
    assert json.loads(await queries.state()) == {"active_claims": []}
    assert json.loads(await queries.manifest()) == [{"agent": "A"}]
    assert agent.calls == ["board", "state", "manifest"]


async def test_single_snapshot_queries_preserve_timeout_contracts() -> None:
    queries, agent = _queries([None, None, None])

    assert await queries.board() == "the hub did not return the board"
    assert await queries.state() == "the hub did not return its state"
    assert await queries.manifest() == "the hub did not return the manifest"
    assert agent.calls == ["board", "state", "manifest"]


async def test_directory_combines_snapshots_and_normalises_bad_collections() -> None:
    queries, agent = _queries(
        [
            {
                "type": MessageType.MANIFEST_SNAPSHOT,
                "manifest": [{"agent": "A", "task_classes": ["chat"]}],
            },
            {
                "type": MessageType.STATE_SNAPSHOT,
                "snapshot": {"resources": [{"agent": "A", "kind": "llm", "name": "model"}]},
            },
            {"type": MessageType.MANIFEST_SNAPSHOT, "manifest": {}},
            {"type": MessageType.STATE_SNAPSHOT, "snapshot": {"resources": {}}},
        ]
    )

    populated = json.loads(await queries.directory())
    empty = json.loads(await queries.directory())

    assert {entry["id"] for entry in populated["entries"]} == {
        "agent:A",
        "resource:A:llm:model",
    }
    assert empty["entries"] == []
    assert agent.calls == ["manifest", "state", "manifest", "state"]


async def test_directory_reports_each_missing_snapshot() -> None:
    no_manifest, manifest_agent = _queries([None])
    no_state, state_agent = _queries(
        [{"type": MessageType.MANIFEST_SNAPSHOT, "manifest": []}, None]
    )

    assert await no_manifest.directory() == "the hub did not return the capability directory"
    assert await no_state.directory() == "the hub did not return the capability directory"
    assert manifest_agent.calls == ["manifest"]
    assert state_agent.calls == ["manifest", "state"]


async def test_task_resource_preserves_found_malformed_and_timeout_results() -> None:
    queries, agent = _queries(
        [
            {
                "type": MessageType.BOARD_SNAPSHOT,
                "board": {"tasks": [{"task_id": "T1", "status": "open"}]},
            },
            {"type": MessageType.BOARD_SNAPSHOT, "board": []},
            None,
        ]
    )

    found = json.loads(await queries.task_resource(" T1 "))
    malformed = json.loads(await queries.task_resource("T1"))

    assert found["found"] is True
    assert found["task"]["status"] == "open"
    assert malformed["found"] is False
    assert await queries.task_resource("T1") == "the hub did not return MCP task resource snapshots"
    assert agent.calls == ["board", "board", "board"]


async def test_agent_resource_preserves_snapshots_and_missing_reply_boundaries() -> None:
    queries, agent = _queries(
        [
            {"type": MessageType.MANIFEST_SNAPSHOT, "manifest": [{"agent": "A"}]},
            {
                "type": MessageType.STATE_SNAPSHOT,
                "snapshot": {"resources": [{"agent": "A", "kind": "llm"}]},
            },
            {"type": MessageType.MANIFEST_SNAPSHOT, "manifest": {}},
            {"type": MessageType.STATE_SNAPSHOT, "snapshot": "bad"},
            None,
            {"type": MessageType.MANIFEST_SNAPSHOT, "manifest": []},
            None,
        ]
    )

    found = json.loads(await queries.agent_resource(" A "))
    malformed = json.loads(await queries.agent_resource("A"))

    assert found["found"] is True
    assert found["capability_card"] == {"agent": "A"}
    assert found["resources"] == [{"agent": "A", "kind": "llm"}]
    assert malformed["found"] is False
    assert (
        await queries.agent_resource("A") == "the hub did not return MCP agent resource snapshots"
    )
    assert (
        await queries.agent_resource("A") == "the hub did not return MCP agent resource snapshots"
    )
    assert agent.calls == [
        "manifest",
        "state",
        "manifest",
        "state",
        "manifest",
        "manifest",
        "state",
    ]


async def test_resource_kind_preserves_filter_malformed_and_timeout_results() -> None:
    queries, agent = _queries(
        [
            {
                "type": MessageType.STATE_SNAPSHOT,
                "snapshot": {
                    "resources": [
                        {"agent": "A", "kind": "llm", "name": "model"},
                        {"agent": "A", "kind": "fs", "name": "workspace"},
                    ]
                },
            },
            {"type": MessageType.STATE_SNAPSHOT, "snapshot": {"resources": {}}},
            None,
        ]
    )

    populated = json.loads(await queries.resource_kind_resource(" llm "))
    malformed = json.loads(await queries.resource_kind_resource("llm"))

    assert populated["resources"] == [{"agent": "A", "kind": "llm", "name": "model"}]
    assert malformed["resources"] == []
    assert (
        await queries.resource_kind_resource("llm")
        == "the hub did not return MCP resource-kind snapshots"
    )
    assert agent.calls == ["state", "state", "state"]
