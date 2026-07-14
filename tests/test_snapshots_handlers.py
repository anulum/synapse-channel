# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — dedicated tests for the read-only snapshot handlers

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, Any, cast

from synapse_channel.core.handlers import snapshots
from synapse_channel.core.protocol import MessageType
from synapse_channel.core.wake_capability import WAKE_DIRECT, WAKE_UNKNOWN

if TYPE_CHECKING:
    from synapse_channel.core.hub import SynapseHub


class _Snapshotter:
    def __init__(self, value: Any) -> None:
        self._value = value

    def snapshot(self, *args: Any, **kwargs: Any) -> Any:
        return self._value


class _Pending:
    def __init__(self, value: Any) -> None:
        self._value = value

    def pending(self) -> Any:
        return self._value


class _Manifester:
    def __init__(self, value: Any) -> None:
        self._value = value

    def manifest(self) -> Any:
        return self._value


class _FakeHub:
    """A read-only SynapseHub stand-in exposing the snapshot data sources."""

    def __init__(
        self,
        *,
        liveness: dict[str, Any] | None = None,
        wake: dict[str, str] | None = None,
        chat: list[dict[str, Any]] | None = None,
        board_task_cap: int | None = None,
    ) -> None:
        self.state = _Snapshotter({"leases": []})
        self.dead_letters = _Snapshotter(["dl"])
        self.relay_approvals = _Pending(["approval"])
        self._liveness = liveness or {}
        self._wake = wake or {}
        self.agent_roles: dict[str, Iterable[str]] = {"a": ("lead",)}
        self.connected_clients = [object(), object()]
        self.config_epoch = 7
        self.mailbox_pending = _Snapshotter({"a": 1})
        self.chat_history = chat if chat is not None else []
        self.blackboard = _Snapshotter({"tasks": []})
        self.board_task_cap = board_task_cap
        self.capabilities = _Manifester({"tools": []})
        self.sent: list[dict[str, Any]] = []

    def roster_liveness(self) -> dict[str, Any]:
        return self._liveness

    def online_agents(self) -> list[str]:
        return list(self._wake) or ["a"]

    def wake_capability_of(self, name: str) -> str:
        return self._wake.get(name, WAKE_UNKNOWN)

    def roles_of(self, name: str) -> tuple[str, ...]:
        return tuple(self.agent_roles.get(name, ()))

    def _system(self, text: str, **fields: Any) -> dict[str, Any]:
        return {"text": text, **fields}

    async def _send_json(self, websocket: Any, payload: dict[str, Any]) -> None:
        self.sent.append(payload)


def _as_hub(hub: _FakeHub) -> SynapseHub:
    """Present the structural fake as a concrete hub without a type: ignore."""
    return cast("SynapseHub", hub)


async def _drive(
    handler: Callable[..., Any], hub: _FakeHub, data: dict[str, Any]
) -> dict[str, Any]:
    await handler(_as_hub(hub), "alice", data, object())
    return hub.sent[0]


class TestStateAndBoardAndManifest:
    async def test_state_snapshot_merges_dead_letters_and_approvals(self) -> None:
        hub = _FakeHub()
        payload = await _drive(snapshots.handle_state_request, hub, {})
        assert payload["msg_type"] == MessageType.STATE_SNAPSHOT
        assert payload["target"] == "alice"
        assert payload["snapshot"]["leases"] == []
        assert payload["snapshot"]["dead_letters"] == ["dl"]
        assert payload["snapshot"]["pending_relay_approvals"] == ["approval"]

    async def test_board_snapshot_uses_task_cap(self) -> None:
        hub = _FakeHub(board_task_cap=5)
        payload = await _drive(snapshots.handle_board_request, hub, {})
        assert payload["msg_type"] == MessageType.BOARD_SNAPSHOT
        assert payload["board"] == {"tasks": []}

    async def test_manifest_snapshot(self) -> None:
        hub = _FakeHub()
        payload = await _drive(snapshots.handle_manifest_request, hub, {})
        assert payload["msg_type"] == MessageType.MANIFEST_SNAPSHOT
        assert payload["manifest"] == {"tools": []}


class TestWhoRequest:
    async def test_liveness_and_wake_capabilities_are_annotated(self) -> None:
        hub = _FakeHub(
            liveness={"a": {"reacted": True}}, wake={"a": WAKE_DIRECT, "b": WAKE_UNKNOWN}
        )
        payload = await _drive(snapshots.handle_who_request, hub, {})
        assert payload["msg_type"] == MessageType.WHO_SNAPSHOT
        assert payload["agent_liveness"] == {"a": {"reacted": True}}
        # Only the non-unknown capability is surfaced.
        assert payload["wake_capabilities"] == {"a": WAKE_DIRECT}
        assert payload["connected_clients"] == 2
        assert payload["config_epoch"] == 7
        assert payload["agent_roles"] == {"a": ["lead"]}
        assert "hub_version" in payload

    async def test_open_hub_omits_liveness_and_wake_fields(self) -> None:
        # No liveness tracking and every agent unknown -> both extras are omitted.
        hub = _FakeHub(liveness={}, wake={"a": WAKE_UNKNOWN, "b": WAKE_UNKNOWN})
        payload = await _drive(snapshots.handle_who_request, hub, {})
        assert "agent_liveness" not in payload
        assert "wake_capabilities" not in payload


class TestHistoryRequest:
    async def test_absent_limit_returns_all(self) -> None:
        hub = _FakeHub(chat=[{"msg_id": 1}, {"msg_id": 2}, {"msg_id": 3}])
        payload = await _drive(snapshots.handle_history_request, hub, {})
        assert payload["msg_type"] == MessageType.HISTORY_SNAPSHOT
        assert payload["requested_limit"] == "all"
        assert len(payload["history"]) == 3

    async def test_numeric_limit_tails_history(self) -> None:
        hub = _FakeHub(chat=[{"msg_id": 1}, {"msg_id": 2}, {"msg_id": 3}])
        payload = await _drive(snapshots.handle_history_request, hub, {"limit": 2})
        assert payload["requested_limit"] == 2
        assert payload["history"] == [{"msg_id": 2}, {"msg_id": 3}]

    async def test_zero_limit_is_floored_to_one(self) -> None:
        hub = _FakeHub(chat=[{"msg_id": 1}, {"msg_id": 2}])
        payload = await _drive(snapshots.handle_history_request, hub, {"limit": 0})
        assert payload["requested_limit"] == 1
        assert payload["history"] == [{"msg_id": 2}]


class TestResumeRequest:
    async def test_resume_returns_messages_after_the_cursor(self) -> None:
        hub = _FakeHub(chat=[{"msg_id": 1}, {"msg_id": 2}, {"msg_id": 3}])
        payload = await _drive(snapshots.handle_resume_request, hub, {"since": 1})
        assert payload["msg_type"] == MessageType.RESUME_SNAPSHOT
        assert payload["since"] == 1
        assert payload["messages"] == [{"msg_id": 2}, {"msg_id": 3}]

    async def test_absent_cursor_resumes_from_start(self) -> None:
        hub = _FakeHub(chat=[{"msg_id": 1}, {"msg_id": 2}])
        payload = await _drive(snapshots.handle_resume_request, hub, {})
        assert payload["since"] == 0
        assert payload["messages"] == [{"msg_id": 1}, {"msg_id": 2}]
