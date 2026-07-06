# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li — an origin hub forwards a dead-letter blackhole signal to the owning peer

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from websockets.asyncio.client import ClientConnection, connect

from hub_e2e_helpers import read_until_type, running_hub
from synapse_channel.core.dead_letter_forwarding import DeadLetterForwardError
from synapse_channel.core.handlers.messaging import _forward_dead_letter_to_peer
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind
from synapse_channel.core.namespace_ownership import NamespaceOwnership
from synapse_channel.core.operator_relay_transport import OperatorRelayPeer
from synapse_channel.core.persistence import EventStore

_EDGE = "syn-edge"
_OWNER = "syn-owner"
_NAMESPACE = "OWNED"
_TARGET = "OWNED/reader"


class _FakeForwarder:
    """A stand-in dead-letter forwarder recording its calls, optionally raising."""

    def __init__(self, *, error: Exception | None = None) -> None:
        self._error = error
        self.calls: list[tuple[dict[str, Any], str, str, str | None]] = []

    async def __call__(
        self, notice: dict[str, Any], *, uri: str, local_id: str, token: str | None = None
    ) -> None:
        self.calls.append((notice, uri, local_id, token))
        if self._error is not None:
            raise self._error


def _hub(
    store: EventStore,
    *,
    forwarder: _FakeForwarder | None = None,
    peers: bool = True,
    ownership: NamespaceOwnership | None = None,
) -> SynapseHub:
    """Return an origin hub whose escalation forwards a remote-owned target to its peer."""
    relay_peers = {_OWNER: OperatorRelayPeer(uri="ws://owner/", token="tok")} if peers else None
    return SynapseHub(
        hub_id=_EDGE,
        journal=store,
        dead_letter_escalation_threshold=1,
        namespace_ownership=(
            ownership
            if ownership is not None
            else NamespaceOwnership(owners={_NAMESPACE: _OWNER}, local_hub_id=_EDGE)
        ),
        relay_peers=relay_peers,
        dead_letter_forwarder=forwarder,
    )


async def _dead_letter(websocket: ClientConnection, *, target: str) -> None:
    """Send one directed chat to a name with no live connection, draining its receipt.

    The delivery receipt is emitted after the chat broadcast, the escalation, and the forward, so
    draining it guarantees the forward has completed before the test inspects the store.
    """
    await websocket.send(
        json.dumps(
            {
                "sender": "ALPHA",
                "type": "chat",
                "target": target,
                "payload": "secret-body",
                "receipt_requested": True,
            }
        )
    )
    await read_until_type(websocket, "delivery_receipt")


def _forwardings(store: EventStore) -> list[dict[str, object]]:
    """Return the dead-letter forwarding audit payloads in the log, in order."""
    return [e.payload for e in store.read_all() if e.kind == EventKind.DEAD_LETTER_FORWARDING]


_EXPECTED = {"target": _TARGET, "count": 1, "origin_hub_id": _EDGE, "owner_hub_id": _OWNER}
# The wire pointer handed to the forwarder is the bare four fields; the origin-side audit adds the
# 'out' direction so it reconciles with the owning hub's inbound record of the same forward.
_EXPECTED_AUDIT = {**_EXPECTED, "direction": "out"}


async def test_a_remote_owned_target_is_forwarded_to_its_peer_and_audited(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    forwarder = _FakeForwarder()
    async with running_hub(_hub(store, forwarder=forwarder)) as (_, uri):
        async with connect(uri) as websocket:
            await read_until_type(websocket, "welcome")
            await _dead_letter(websocket, target=_TARGET)

    forwardings = _forwardings(store)
    store.close()
    assert forwardings == [_EXPECTED_AUDIT]
    assert forwarder.calls == [(_EXPECTED, "ws://owner/", _EDGE, "tok")]
    # The honesty bound end to end: the pointer names the target, never its body.
    assert "secret-body" not in json.dumps(forwardings)


async def test_a_locally_owned_target_is_not_forwarded(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    forwarder = _FakeForwarder()
    local = NamespaceOwnership(owners={_NAMESPACE: _EDGE}, local_hub_id=_EDGE)
    async with running_hub(_hub(store, forwarder=forwarder, ownership=local)) as (_, uri):
        async with connect(uri) as websocket:
            await read_until_type(websocket, "welcome")
            await _dead_letter(websocket, target=_TARGET)

    assert _forwardings(store) == []
    store.close()
    assert forwarder.calls == []


async def test_without_a_relay_route_nothing_is_forwarded(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    forwarder = _FakeForwarder()
    async with running_hub(_hub(store, forwarder=forwarder, peers=False)) as (_, uri):
        async with connect(uri) as websocket:
            await read_until_type(websocket, "welcome")
            await _dead_letter(websocket, target=_TARGET)

    assert _forwardings(store) == []
    store.close()
    assert forwarder.calls == []


async def test_a_target_with_no_namespace_is_not_forwarded(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    forwarder = _FakeForwarder()
    async with running_hub(_hub(store, forwarder=forwarder)) as (_, uri):
        async with connect(uri) as websocket:
            await read_until_type(websocket, "welcome")
            await _dead_letter(websocket, target="MISSING")  # no "/" → no namespace to own

    assert _forwardings(store) == []
    store.close()
    assert forwarder.calls == []


async def test_a_failed_forward_still_records_the_durable_audit(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    forwarder = _FakeForwarder(error=DeadLetterForwardError("peer unreachable"))
    async with running_hub(_hub(store, forwarder=forwarder)) as (_, uri):
        async with connect(uri) as websocket:
            await read_until_type(websocket, "welcome")
            await _dead_letter(websocket, target=_TARGET)  # forward raises; escalation must not

    assert _forwardings(store) == [_EXPECTED_AUDIT]  # recorded but not delivered
    store.close()
    assert len(forwarder.calls) == 1


async def test_without_a_forwarder_the_intent_is_recorded_not_transmitted(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    async with running_hub(_hub(store, forwarder=None)) as (_, uri):
        async with connect(uri) as websocket:
            await read_until_type(websocket, "welcome")
            await _dead_letter(websocket, target=_TARGET)

    assert _forwardings(store) == [_EXPECTED_AUDIT]  # audit written; no transmission attempted
    store.close()


async def test_forward_is_a_noop_without_an_ownership_map() -> None:
    # A hub with relay routes but no ownership map cannot resolve an owner, so it forwards
    # nothing rather than guessing. Called directly — no escalation needs to fire.
    hub = SynapseHub(
        hub_id=_EDGE, relay_peers={_OWNER: OperatorRelayPeer(uri="ws://owner/", token=None)}
    )
    assert hub.namespace_ownership is None
    await _forward_dead_letter_to_peer(hub, target=_TARGET, count=1)  # returns without raising


async def test_forward_consults_the_observed_asserting_hubs_feed(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    forwarder = _FakeForwarder()
    seen: list[str] = []

    def asserting(namespace: str) -> list[str]:
        seen.append(namespace)
        return []  # no conflicting assertion, so ownership still resolves to the peer

    hub = SynapseHub(
        hub_id=_EDGE,
        journal=store,
        namespace_ownership=NamespaceOwnership(owners={_NAMESPACE: _OWNER}, local_hub_id=_EDGE),
        relay_peers={_OWNER: OperatorRelayPeer(uri="ws://owner/", token="tok")},
        dead_letter_forwarder=forwarder,
        observed_asserting_hubs=asserting,
    )
    await _forward_dead_letter_to_peer(hub, target=_TARGET, count=1)
    store.close()
    assert seen == [_NAMESPACE]  # the partition feed was consulted for the target's namespace
    assert forwarder.calls == [(_EXPECTED, "ws://owner/", _EDGE, "tok")]


async def test_forward_without_a_journal_still_transmits() -> None:
    # No durable log to write the audit to, but the pointer is still handed to the peer.
    forwarder = _FakeForwarder()
    hub = SynapseHub(
        hub_id=_EDGE,
        namespace_ownership=NamespaceOwnership(owners={_NAMESPACE: _OWNER}, local_hub_id=_EDGE),
        relay_peers={_OWNER: OperatorRelayPeer(uri="ws://owner/", token="tok")},
        dead_letter_forwarder=forwarder,
    )
    assert hub.journal is None
    await _forward_dead_letter_to_peer(hub, target=_TARGET, count=1)
    assert forwarder.calls == [(_EXPECTED, "ws://owner/", _EDGE, "tok")]
