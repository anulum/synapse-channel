# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — release retry evidence without disturbing a successor lease
"""Exercise retained release confirmation through the actual HTTP and native boundaries."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

import pytest

from hub_e2e_helpers import running_hub
from mcp_server_helpers import BridgeHandle
from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.protocol import SENDER_HUB, MessageType
from synapse_channel.core.release_confirmation import ReleaseIntent
from synapse_channel.mcp.bridge import SynapseHubBridge
from synapse_channel.mcp.http_agent import HttpHubAgent
from synapse_channel.mcp.release_confirmation import (
    PriorReleaseUnconfirmedError,
    confirm_prepared_release,
)
from synapse_channel.mcp.reply_exchange import Matcher, Sender
from test_mcp_http_operation_arguments import OperationSession
from test_mcp_http_operation_arguments import remote as remote


async def test_native_release_outside_http_keeps_its_original_fence_without_inventing_a_key() -> (
    None
):
    """The provisioned client still performs its native lifecycle outside an HTTP call."""
    async with running_hub(SynapseHub(require_fencing_epoch=True)) as (hub, uri):
        bridge = SynapseHubBridge(
            uri=uri, name="ALPHA/native", agent_factory=HttpHubAgent, request_timeout=2
        )
        handle = BridgeHandle(bridge, asyncio.create_task(bridge.agent.connect()))
        try:
            assert await bridge.agent.wait_until_ready(3)
            assert "claim granted" in await bridge.claim("ALPHA/native-task", task_only=True)
            request = bridge.agent.prepare_release("ALPHA/native-task")
            assert "idem_key" not in request
            assert request["epoch"] == hub.state.claims["ALPHA/native-task"].epoch
            assert "with receipt owner 'ALPHA/native'" in await bridge.release("ALPHA/native-task")
            assert "ALPHA/native-task" not in hub.state.claims
        finally:
            await handle.close()


async def test_old_release_retry_preserves_a_new_lease_and_its_client_fence(
    remote: Callable[[str], AbstractAsyncContextManager[OperationSession]],
) -> None:
    """An old receipt is an inquiry result, never a release of the current lease."""
    async with remote("synapse:read synapse:mutate") as runtime:
        actions: list[tuple[str, dict[str, Any]]] = [
            (
                "synapse_task_declare",
                {
                    "task_id": "ALPHA/reacquired",
                    "title": "Epoch custody",
                    "operation_id": "declare",
                },
            ),
            (
                "synapse_claim",
                {"task_id": "ALPHA/reacquired", "task_only": True, "operation_id": "first-claim"},
            ),
            ("synapse_release", {"task_id": "ALPHA/reacquired", "operation_id": "first-release"}),
            (
                "synapse_claim",
                {"task_id": "ALPHA/reacquired", "task_only": True, "operation_id": "next-claim"},
            ),
        ]
        for name, arguments in actions:
            result = await runtime.session.call_tool(name, arguments)
            assert not result.is_error
        epoch = runtime.hub.state.claims["ALPHA/reacquired"].epoch
        replay = await runtime.session.call_tool(
            "synapse_release", {"task_id": "ALPHA/reacquired", "operation_id": "first-release"}
        )
        assert not replay.is_error
        assert runtime.hub.state.claims["ALPHA/reacquired"].epoch == epoch
        completed = await runtime.session.call_tool(
            "synapse_release", {"task_id": "ALPHA/reacquired", "operation_id": "next-release"}
        )
        assert not completed.is_error
        assert "ALPHA/reacquired" not in runtime.hub.state.claims


async def test_uncertain_inquiry_and_changed_intent_preserve_a_newer_lease(
    remote: Callable[[str], AbstractAsyncContextManager[OperationSession]],
) -> None:
    """Actual lost confirmation or different evidence cannot disturb a successor fence."""
    async with remote("synapse:read synapse:mutate") as runtime:
        actions: list[tuple[str, dict[str, Any]]] = [
            (
                "synapse_task_declare",
                {
                    "task_id": "ALPHA/uncertain",
                    "title": "Outcome inquiry",
                    "operation_id": "declare",
                },
            ),
            (
                "synapse_claim",
                {"task_id": "ALPHA/uncertain", "task_only": True, "operation_id": "first-claim"},
            ),
            ("synapse_release", {"task_id": "ALPHA/uncertain", "operation_id": "first-release"}),
            (
                "synapse_claim",
                {"task_id": "ALPHA/uncertain", "task_only": True, "operation_id": "next-claim"},
            ),
        ]
        for name, arguments in actions:
            assert not (await runtime.session.call_tool(name, arguments)).is_error
        epoch = runtime.hub.state.claims["ALPHA/uncertain"].epoch
        dispatches = runtime.fault["release_dispatches"]
        runtime.fault["active"] = True
        lost = await runtime.session.call_tool(
            "synapse_release", {"task_id": "ALPHA/uncertain", "operation_id": "first-release"}
        )
        assert lost.is_error
        assert runtime.fault["confirmation_queries"] == 1
        assert runtime.fault["release_dispatches"] == dispatches
        assert runtime.hub.state.claims["ALPHA/uncertain"].epoch == epoch
        runtime.fault["active"] = False
        changed = await runtime.session.call_tool(
            "synapse_release",
            {
                "task_id": "ALPHA/uncertain",
                "evidence": ["different original intent"],
                "operation_id": "first-release",
            },
        )
        assert changed.is_error
        assert runtime.fault["release_dispatches"] == dispatches
        assert runtime.hub.state.claims["ALPHA/uncertain"].epoch == epoch
        old = await runtime.session.call_tool(
            "synapse_release", {"task_id": "ALPHA/uncertain", "operation_id": "first-release"}
        )
        assert not old.is_error
        assert runtime.fault["release_dispatches"] == dispatches
        current = await runtime.session.call_tool(
            "synapse_release", {"task_id": "ALPHA/uncertain", "operation_id": "next-release"}
        )
        assert not current.is_error
        assert "ALPHA/uncertain" not in runtime.hub.state.claims


async def test_first_release_after_restart_can_follow_an_unknown_inquiry(
    remote: Callable[[str], AbstractAsyncContextManager[OperationSession]],
) -> None:
    """No retained release receipt means continue the pinned first intent, never claim success."""
    for incarnation in range(2):
        async with remote("synapse:read synapse:mutate") as runtime:
            if not incarnation:
                actions: list[tuple[str, dict[str, Any]]] = [
                    (
                        "synapse_task_declare",
                        {
                            "task_id": "ALPHA/pending",
                            "title": "Pending lease",
                            "operation_id": "declare",
                        },
                    ),
                    (
                        "synapse_claim",
                        {"task_id": "ALPHA/pending", "task_only": True, "operation_id": "claim"},
                    ),
                ]
                for name, arguments in actions:
                    assert not (await runtime.session.call_tool(name, arguments)).is_error
                continue
            assert "ALPHA/pending" in runtime.hub.state.claims
            release = await runtime.session.call_tool(
                "synapse_release", {"task_id": "ALPHA/pending", "operation_id": "first-release"}
            )
            assert not release.is_error
            assert runtime.fault["confirmation_queries"] == 1
            assert runtime.fault["release_dispatches"] == 1
            assert "ALPHA/pending" not in runtime.hub.state.claims


async def test_a_lost_mutation_reply_retries_the_exact_original_intent(
    remote: Callable[[str], AbstractAsyncContextManager[OperationSession]],
) -> None:
    """A real dropped grant leaves uncertainty; its stable retry releases exactly once."""
    async with remote("synapse:read synapse:mutate") as runtime:
        actions: list[tuple[str, dict[str, Any]]] = [
            (
                "synapse_task_declare",
                {"task_id": "ALPHA/lost", "title": "Lost reply", "operation_id": "declare"},
            ),
            (
                "synapse_claim",
                {"task_id": "ALPHA/lost", "task_only": True, "operation_id": "claim"},
            ),
        ]
        for name, arguments in actions:
            assert not (await runtime.session.call_tool(name, arguments)).is_error
        arguments = {"task_id": "ALPHA/lost", "operation_id": "release"}
        runtime.fault["active"] = True
        assert (await runtime.session.call_tool("synapse_release", arguments)).is_error
        assert runtime.fault["release_seen"] is True
        assert "ALPHA/lost" not in runtime.hub.state.claims
        runtime.fault["active"] = False
        assert not (await runtime.session.call_tool("synapse_release", arguments)).is_error
        assert len(list(runtime.journal.iter_events(kinds=["release"]))) == 1


class _RecordingAgent(SynapseAgent):
    """A real client that records what it would send instead of using a connection."""

    def __init__(self) -> None:
        super().__init__("ALPHA/alice", verbose=False, persist_lease_epochs=False)
        self.hub_id = "hub-under-test"
        self.sent: list[dict[str, Any]] = []

    async def send_message(
        self,
        msg_type: str,
        *,
        target: str = "all",
        payload: str = "",
        sign_identity: bool = False,
        **extra: Any,
    ) -> None:
        """Keep the frame the inquiry would send."""
        self.sent.append({"type": msg_type, "target": target, **extra})


def _old_release_with_a_newer_lease() -> tuple[_RecordingAgent, dict[str, Any], ReleaseIntent]:
    """Prepare a keyed release of fence 3 while the client holds fence 5 of the same task."""
    agent = _RecordingAgent()
    request = agent.prepare_release("ALPHA/task", epoch=3, idem_key="first-release")
    agent.lease_epochs["ALPHA/task"] = 5
    return agent, request, ReleaseIntent.from_request(request)


def _confirmation(agent: _RecordingAgent, intent: ReleaseIntent, **changes: Any) -> dict[str, Any]:
    """Return the hub's answer to the recorded inquiry, with ``changes`` to its projection."""
    projection: dict[str, Any] = {
        "status": "confirmed",
        "task_id": intent.task_id,
        "owner": intent.owner,
        "release_operation_id": intent.operation_id,
        "request_digest": intent.request_digest,
        "receipt": {"task_id": intent.task_id, "owner": intent.owner, "released": True},
    }
    projection.update(changes)
    return {
        "sender": SENDER_HUB,
        "hub_id": agent.hub_id,
        "type": MessageType.STATE_SNAPSHOT,
        "target": agent.name,
        "request_id": agent.sent[-1]["request_id"],
        "release_confirmation": projection,
    }


async def test_a_confirmed_matching_receipt_is_returned_and_only_asked_for() -> None:
    """The inquiry is one read; its answer is the original receipt's text."""
    agent, request, intent = _old_release_with_a_newer_lease()

    async def answered(match: Matcher, send: Sender, timeout: float) -> dict[str, Any] | None:
        await send()
        frame = _confirmation(agent, intent)
        assert match(frame)
        assert timeout == 1.0
        return frame

    text = await confirm_prepared_release(agent, request, answered)
    assert text == "released 'ALPHA/task' with receipt owner 'ALPHA/alice'"
    assert [frame["type"] for frame in agent.sent] == [MessageType.STATE_REQUEST]
    assert agent.sent[0]["release_confirmation"] == intent.as_query()
    assert agent.lease_epochs["ALPHA/task"] == 5


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "unknown"},
        {"request_digest": "0" * 64},
        {"release_operation_id": "another-release"},
        {"owner": "ALPHA/bob"},
        {"receipt": {"task_id": "ALPHA/task", "owner": "ALPHA/alice", "released": False}},
        {"receipt": None},
    ],
)
async def test_an_answer_that_is_not_this_releases_confirmed_receipt_keeps_the_newer_lease(
    changes: dict[str, Any],
) -> None:
    """A status alone, or a receipt alone, is not a confirmation of this release."""
    agent, request, intent = _old_release_with_a_newer_lease()

    async def answered(match: Matcher, send: Sender, timeout: float) -> dict[str, Any] | None:
        await send()
        return _confirmation(agent, intent, **changes)

    with pytest.raises(PriorReleaseUnconfirmedError, match="current lease fence retained"):
        await confirm_prepared_release(agent, request, answered)
    assert agent.lease_epochs["ALPHA/task"] == 5


@pytest.mark.parametrize(
    "field,value",
    [
        ("request_id", "another-request"),
        ("sender", "ALPHA/bob"),
        ("hub_id", "another-hub"),
        ("target", "ALPHA/bob"),
        ("type", MessageType.STATE_REQUEST),
    ],
)
async def test_only_the_hubs_answer_to_this_inquiry_is_taken(field: str, value: str) -> None:
    """A frame of another request, sender, hub, target or type is not the answer."""
    agent, request, intent = _old_release_with_a_newer_lease()
    seen: list[bool] = []

    async def answered(match: Matcher, send: Sender, timeout: float) -> dict[str, Any] | None:
        await send()
        frame = _confirmation(agent, intent)
        seen.append(match({**frame, field: value}))
        seen.append(match(frame))
        return frame

    assert await confirm_prepared_release(agent, request, answered) is not None
    assert seen == [False, True]


async def test_a_release_without_a_key_or_of_the_held_lease_is_not_asked_about() -> None:
    """Only a keyed release that no longer names the held lease needs the inquiry."""
    asked: list[float] = []

    async def counted(match: Matcher, send: Sender, timeout: float) -> dict[str, Any] | None:
        asked.append(timeout)
        return None

    agent = _RecordingAgent()
    agent.lease_epochs["ALPHA/task"] = 5
    without_key = agent.prepare_release("ALPHA/task", epoch=3)
    assert await confirm_prepared_release(agent, without_key, counted) is None
    held = agent.prepare_release("ALPHA/task", idem_key="current-release")
    assert held["epoch"] == 5
    assert await confirm_prepared_release(agent, held, counted) is None
    assert asked == [] and agent.sent == []

    released_elsewhere = agent.prepare_release("ALPHA/task", epoch=3, idem_key="first-release")
    del agent.lease_epochs["ALPHA/task"]
    assert await confirm_prepared_release(agent, released_elsewhere, counted) is None
    assert asked == [1.0]
