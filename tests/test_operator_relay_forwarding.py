# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — dedicated tests for the origin half of the operator relay

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import pytest

from synapse_channel.core import operator_relay_forwarding as orf
from synapse_channel.core.journal import RELAY_DIRECTION_OUT
from synapse_channel.core.operator_relay_routing import RelayRoute, RelayRouteKind
from synapse_channel.core.operator_relay_transport import (
    OperatorRelayPeer,
    RelayTransportError,
)
from synapse_channel.core.operator_relay_wire import (
    RelayActionRequest,
    RelayActionResult,
    RelayWireError,
)
from synapse_channel.core.protocol import MessageType

if TYPE_CHECKING:
    from collections.abc import Iterable

    from synapse_channel.core.namespace_ownership import NamespaceOwnership
    from synapse_channel.core.persistence import EventStore

_REQUEST = RelayActionRequest(
    action="force_release",
    namespace="lab-a/shared",
    task_id="t1",
    operator="alice",
    origin_hub_id="",
    reason="stuck",
    break_glass=False,
)


class _Decision:
    def __init__(self, owner_hub_id: str | None) -> None:
        self.owner_hub_id = owner_hub_id


class _Ownership:
    def __init__(self, owner_hub_id: str | None = "syn-owner") -> None:
        self._owner = owner_hub_id
        self.resolve_calls: list[tuple[str, tuple[str, ...]]] = []

    def resolve(self, namespace: str, *, asserting_hubs: Iterable[str]) -> Any:
        self.resolve_calls.append((namespace, tuple(asserting_hubs)))
        return _Decision(self._owner)


class _Recorder:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.relayed: list[dict[str, Any]] = []

    def system(self, text: str, **fields: Any) -> dict[str, Any]:
        return {"text": text, **fields}

    async def send_json(self, websocket: Any, payload: dict[str, Any]) -> None:
        self.sent.append(payload)


def _build(
    *,
    ownership: _Ownership | None,
    forwarder: Any,
    journal: object | None = None,
    observed: Any = None,
    recorder: _Recorder,
) -> orf.OperatorRelayForwarding:
    return orf.OperatorRelayForwarding(
        namespace_ownership=cast("NamespaceOwnership | None", ownership),
        relay_peers=None,
        relay_forwarder=forwarder,
        observed_asserting_hubs=observed,
        hub_id="syn-origin",
        journal=cast("EventStore | None", journal),
        send_json=recorder.send_json,
        system=recorder.system,
    )


async def _noop_forwarder(request: RelayActionRequest, **kwargs: Any) -> RelayActionResult:
    raise AssertionError("forwarder should not be reached in this test")


class TestProceedPaths:
    """Frames the gate lets through to the local serving handler."""

    async def test_non_relay_message_proceeds(self) -> None:
        rec = _Recorder()
        gate = _build(ownership=_Ownership(), forwarder=_noop_forwarder, recorder=rec)
        proceed = await gate.route("alice", "chat", {"namespace": "lab-a/shared"}, object())
        assert proceed is True
        assert rec.sent == []

    async def test_no_ownership_map_proceeds(self) -> None:
        rec = _Recorder()
        gate = _build(ownership=None, forwarder=_noop_forwarder, recorder=rec)
        proceed = await gate.route(
            "alice", MessageType.OPERATOR_RELAY_REQUEST, {"namespace": "lab-a/shared"}, object()
        )
        assert proceed is True

    async def test_empty_namespace_proceeds(self) -> None:
        rec = _Recorder()
        gate = _build(ownership=_Ownership(), forwarder=_noop_forwarder, recorder=rec)
        proceed = await gate.route(
            "alice", MessageType.OPERATOR_RELAY_REQUEST, {"namespace": "  "}, object()
        )
        assert proceed is True

    async def test_locally_owned_relay_proceeds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            orf,
            "route_operator_relay",
            lambda decision, *, relay_peers: RelayRoute(RelayRouteKind.APPLY_LOCAL),
        )
        rec = _Recorder()
        own = _Ownership()
        gate = _build(ownership=own, forwarder=_noop_forwarder, recorder=rec)
        proceed = await gate.route(
            "alice", MessageType.OPERATOR_RELAY_REQUEST, {"namespace": "lab-a/shared"}, object()
        )
        assert proceed is True
        assert rec.sent == []
        assert own.resolve_calls == [("lab-a/shared", ())]


class TestHandledPaths:
    """Frames the gate handles itself (forward, refuse, or reject)."""

    async def test_malformed_request_is_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        peer = OperatorRelayPeer(uri="wss://owner.test", token="tok")
        monkeypatch.setattr(
            orf,
            "route_operator_relay",
            lambda d, *, relay_peers: RelayRoute(RelayRouteKind.FORWARD, peer=peer),
        )

        def _boom(data: dict[str, Any]) -> RelayActionRequest:
            raise RelayWireError("bad frame")

        monkeypatch.setattr(orf, "decode_relay_request", _boom)
        rec = _Recorder()
        gate = _build(ownership=_Ownership(), forwarder=_noop_forwarder, recorder=rec)
        proceed = await gate.route(
            "alice", MessageType.OPERATOR_RELAY_REQUEST, {"namespace": "lab-a/shared"}, object()
        )
        assert proceed is False
        assert rec.sent[0]["msg_type"] == MessageType.ERROR
        assert "Malformed operator relay request" in rec.sent[0]["text"]

    async def test_forward_relays_owner_verdict_and_audits(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        peer = OperatorRelayPeer(uri="wss://owner.test", token="tok")
        monkeypatch.setattr(
            orf,
            "route_operator_relay",
            lambda d, *, relay_peers: RelayRoute(RelayRouteKind.FORWARD, peer=peer),
        )
        monkeypatch.setattr(orf, "decode_relay_request", lambda data: _REQUEST)
        audits: list[dict[str, Any]] = []
        monkeypatch.setattr(
            orf, "record_operator_relay", lambda journal, payload: audits.append(payload)
        )
        verdict = RelayActionResult(
            applied=True,
            action="force_release",
            namespace="lab-a/shared",
            task_id="t1",
            owner_hub_id="syn-owner",
            detail="released",
        )
        forwarded: dict[str, Any] = {}

        async def _forwarder(request: RelayActionRequest, **kwargs: Any) -> RelayActionResult:
            forwarded["request"] = request
            forwarded["kwargs"] = kwargs
            return verdict

        rec = _Recorder()
        gate = _build(
            ownership=_Ownership(),
            forwarder=_forwarder,
            journal=object(),
            observed=lambda ns: ["syn-observer"],
            recorder=rec,
        )
        proceed = await gate.route(
            "alice", MessageType.OPERATOR_RELAY_REQUEST, {"namespace": "lab-a/shared"}, object()
        )
        assert proceed is False
        # The forwarded request carries this hub's id as its origin.
        assert forwarded["request"].origin_hub_id == "syn-origin"
        assert forwarded["kwargs"]["uri"] == "wss://owner.test"
        assert forwarded["kwargs"]["token"] == "tok"
        assert rec.sent[0]["msg_type"] == MessageType.OPERATOR_RELAY_RESULT
        assert audits[0]["direction"] == RELAY_DIRECTION_OUT
        assert audits[0]["applied"] is True

    async def test_forward_transport_failure_fails_closed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        peer = OperatorRelayPeer(uri="wss://owner.test")
        monkeypatch.setattr(
            orf,
            "route_operator_relay",
            lambda d, *, relay_peers: RelayRoute(RelayRouteKind.FORWARD, peer=peer),
        )
        monkeypatch.setattr(orf, "decode_relay_request", lambda data: _REQUEST)
        audits: list[dict[str, Any]] = []
        monkeypatch.setattr(
            orf, "record_operator_relay", lambda journal, payload: audits.append(payload)
        )

        async def _forwarder(request: RelayActionRequest, **kwargs: Any) -> RelayActionResult:
            raise RelayTransportError("owner unreachable")

        rec = _Recorder()
        gate = _build(ownership=_Ownership(), forwarder=_forwarder, journal=object(), recorder=rec)
        proceed = await gate.route(
            "alice", MessageType.OPERATOR_RELAY_REQUEST, {"namespace": "lab-a/shared"}, object()
        )
        assert proceed is False
        assert rec.sent[0]["msg_type"] == MessageType.OPERATOR_RELAY_RESULT
        assert rec.sent[0]["detail"] == orf._FORWARD_FAILED
        assert audits[0]["applied"] is False

    async def test_forward_without_journal_skips_audit(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        peer = OperatorRelayPeer(uri="wss://owner.test")
        monkeypatch.setattr(
            orf,
            "route_operator_relay",
            lambda d, *, relay_peers: RelayRoute(RelayRouteKind.FORWARD, peer=peer),
        )
        monkeypatch.setattr(orf, "decode_relay_request", lambda data: _REQUEST)
        audits: list[dict[str, Any]] = []
        monkeypatch.setattr(
            orf, "record_operator_relay", lambda journal, payload: audits.append(payload)
        )
        verdict = RelayActionResult(
            applied=True,
            action="force_release",
            namespace="lab-a/shared",
            task_id="t1",
            owner_hub_id="syn-owner",
        )

        async def _forwarder(request: RelayActionRequest, **kwargs: Any) -> RelayActionResult:
            return verdict

        rec = _Recorder()
        gate = _build(ownership=_Ownership(), forwarder=_forwarder, journal=None, recorder=rec)
        await gate.route(
            "alice", MessageType.OPERATOR_RELAY_REQUEST, {"namespace": "lab-a/shared"}, object()
        )
        assert audits == []

    async def test_unrouted_owner_is_refused_with_reason(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            orf,
            "route_operator_relay",
            lambda d, *, relay_peers: RelayRoute(RelayRouteKind.REFUSE, reason="no relay route"),
        )
        monkeypatch.setattr(orf, "decode_relay_request", lambda data: _REQUEST)
        rec = _Recorder()
        own = _Ownership(owner_hub_id="syn-owner")
        gate = _build(ownership=own, forwarder=_noop_forwarder, recorder=rec)
        proceed = await gate.route(
            "alice", MessageType.OPERATOR_RELAY_REQUEST, {"namespace": "lab-a/shared"}, object()
        )
        assert proceed is False
        assert rec.sent[0]["msg_type"] == MessageType.OPERATOR_RELAY_RESULT
        assert rec.sent[0]["detail"] == "no relay route"
        assert rec.sent[0]["applied"] is False
