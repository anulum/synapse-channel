# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — unit tests for cross-hub claim forwarding's origin half
"""Exercise :class:`ClaimForwarding` directly, including transport options and counters."""

from __future__ import annotations

from typing import Any, cast

from synapse_channel.core.hub_counters import HubCounters
from synapse_channel.core.multihub_claim_forwarding import ClaimForwarding
from synapse_channel.core.multihub_claim_transport import (
    ClaimForwarder,
    ClaimForwardError,
    ClaimForwardPeer,
    ClaimForwardTimeoutError,
)
from synapse_channel.core.multihub_claim_wire import ClaimForwardRequest, ClaimForwardResult
from synapse_channel.core.protocol import MessageType, system_message

_WS = object()  # opaque socket token; ClaimForwarding only forwards it to send_json


class _Recorder:
    """Records every frame relayed through the injected send callback."""

    def __init__(self) -> None:
        self.sent: list[tuple[Any, dict[str, Any]]] = []

    async def send_json(self, websocket: Any, data: dict[str, Any]) -> None:
        self.sent.append((websocket, data))


class _FakeForwarder:
    """A claim forwarder returning a canned result or raising, and recording exact arguments."""

    def __init__(
        self, *, result: ClaimForwardResult | None = None, error: Exception | None = None
    ) -> None:
        self._result = result
        self._error = error
        self.calls: list[tuple[ClaimForwardRequest, dict[str, Any]]] = []

    async def __call__(self, request: ClaimForwardRequest, **kwargs: Any) -> ClaimForwardResult:
        self.calls.append((request, dict(kwargs)))
        if self._error is not None:
            raise self._error
        assert self._result is not None
        return self._result


def _system(payload: str, **extra: Any) -> dict[str, Any]:
    return system_message(payload, hub_id="syn-local", **extra)


def _never_forwards() -> ClaimForwarder:
    return cast(ClaimForwarder, _FakeForwarder(error=AssertionError("must not forward")))


def _forwarding(
    *,
    claim_peers: dict[str, ClaimForwardPeer] | None = None,
    claim_forwarder: ClaimForwarder | None = None,
    counters: HubCounters | None = None,
    hub_id: str = "syn-local",
    recorder: _Recorder | None = None,
) -> tuple[ClaimForwarding, _Recorder, HubCounters]:
    rec = recorder or _Recorder()
    hub_counters = counters or HubCounters()
    forwarding = ClaimForwarding(
        claim_peers=claim_peers,
        claim_forwarder=claim_forwarder or _never_forwards(),
        counters=hub_counters,
        hub_id=hub_id,
        send_json=rec.send_json,
        system=_system,
    )
    return forwarding, rec, hub_counters


async def test_forward_without_peers_is_a_no_op() -> None:
    forwarding, rec, counters = _forwarding(claim_peers=None)
    assert await forwarding.forward("P/a", "P", "T1", {}, "syn-remote", _WS) is False
    assert rec.sent == []
    assert counters.forwarded_claims == 0


async def test_forward_without_a_route_for_the_owner() -> None:
    forwarding, rec, counters = _forwarding(
        claim_peers={"other": ClaimForwardPeer(uri="ws://other/")}
    )
    assert await forwarding.forward("P/a", "P", "T1", {}, "syn-remote", _WS) is False
    assert rec.sent == []
    assert counters.forwarded_claims == 0


async def test_forward_without_a_task_id() -> None:
    forwarding, rec, counters = _forwarding(
        claim_peers={"syn-remote": ClaimForwardPeer(uri="ws://remote/")}
    )
    assert await forwarding.forward("P/a", "P", "", {}, "syn-remote", _WS) is False
    assert rec.sent == []
    assert counters.forwarded_claims == 0


async def test_forward_relays_a_grant() -> None:
    grant = ClaimForwardResult(
        granted=True,
        task_id="T1",
        namespace="P",
        owner_hub_id="syn-remote",
        detail="granted upstream",
        grant={"task_id": "T1", "lease_expires_at": 9.0},
    )
    forwarder = _FakeForwarder(result=grant)
    forwarding, rec, counters = _forwarding(
        claim_peers={"syn-remote": ClaimForwardPeer(uri="ws://remote/", token="tok")},
        claim_forwarder=cast(ClaimForwarder, forwarder),
    )

    result = await forwarding.forward("P/a", "P", "T1", {}, "syn-remote", _WS)

    assert result is True
    frame = rec.sent[0][1]
    assert frame["type"] == MessageType.CLAIM_GRANTED
    assert frame["task_id"] == "T1"
    assert frame["payload"] == "granted upstream"
    assert frame["lease_expires_at"] == 9.0
    assert counters.forwarded_claims == 1
    assert counters.forwarded_claims_granted == 1
    assert counters.forwarded_claims_denied == 0
    assert counters.forwarded_claim_timeouts == 0


async def test_forward_relays_a_denial() -> None:
    denied = ClaimForwardResult(
        granted=False, task_id="T1", namespace="P", owner_hub_id="syn-remote"
    )
    forwarder = _FakeForwarder(result=denied)
    forwarding, rec, counters = _forwarding(
        claim_peers={"syn-remote": ClaimForwardPeer(uri="ws://remote/")},
        claim_forwarder=cast(ClaimForwarder, forwarder),
    )

    result = await forwarding.forward("P/a", "P", "T1", {}, "syn-remote", _WS)

    assert result is True
    frame = rec.sent[0][1]
    assert frame["type"] == MessageType.CLAIM_DENIED
    assert frame["task_id"] == "T1"
    assert frame["namespace"] == "P"
    assert frame["owner_hub_id"] == "syn-remote"
    assert counters.forwarded_claims == 1
    assert counters.forwarded_claims_denied == 1
    assert counters.forwarded_claims_granted == 0
    assert counters.forwarded_claim_timeouts == 0


async def test_forward_treats_a_grantless_grant_as_a_denial() -> None:
    # granted is True but no lease fields accompanied it — relayed as a denial.
    grantless = ClaimForwardResult(
        granted=True, task_id="T1", namespace="P", owner_hub_id="syn-remote", grant=None
    )
    forwarder = _FakeForwarder(result=grantless)
    forwarding, rec, counters = _forwarding(
        claim_peers={"syn-remote": ClaimForwardPeer(uri="ws://remote/")},
        claim_forwarder=cast(ClaimForwarder, forwarder),
    )

    assert await forwarding.forward("P/a", "P", "T1", {}, "syn-remote", _WS) is True
    assert rec.sent[0][1]["type"] == MessageType.CLAIM_DENIED
    assert counters.forwarded_claims == 1
    assert counters.forwarded_claims_denied == 1
    assert counters.forwarded_claims_granted == 0
    assert counters.forwarded_claim_timeouts == 0


async def test_forward_treats_a_denied_result_with_a_grant_mapping_as_a_denial() -> None:
    # granted is False even though a non-null grant mapping accompanied it —
    # must still relay CLAIM_DENIED and count forwarded_claims_denied.
    denied_with_grant = ClaimForwardResult(
        granted=False,
        task_id="T1",
        namespace="P",
        owner_hub_id="syn-remote",
        detail="explicit denial despite grant dict",
        grant={"owner": "P/a", "task_id": "T1"},
    )
    forwarder = _FakeForwarder(result=denied_with_grant)
    forwarding, rec, counters = _forwarding(
        claim_peers={"syn-remote": ClaimForwardPeer(uri="ws://remote/")},
        claim_forwarder=cast(ClaimForwarder, forwarder),
    )

    assert await forwarding.forward("P/a", "P", "T1", {}, "syn-remote", _WS) is True
    assert len(rec.sent) == 1
    frame = rec.sent[0][1]
    assert frame["type"] == MessageType.CLAIM_DENIED
    assert frame["task_id"] == "T1"
    assert frame["namespace"] == "P"
    assert frame["owner_hub_id"] == "syn-remote"
    assert "owner" not in frame
    assert counters.forwarded_claims == 1
    assert counters.forwarded_claims_denied == 1
    assert counters.forwarded_claims_granted == 0
    assert counters.forwarded_claim_timeouts == 0


async def test_forward_returns_false_on_a_forward_error() -> None:
    forwarder = _FakeForwarder(error=ClaimForwardError("owner unreachable"))
    forwarding, rec, counters = _forwarding(
        claim_peers={"syn-remote": ClaimForwardPeer(uri="ws://remote/")},
        claim_forwarder=cast(ClaimForwarder, forwarder),
    )

    assert await forwarding.forward("P/a", "P", "T1", {}, "syn-remote", _WS) is False
    assert rec.sent == []
    assert counters.forwarded_claims == 1
    assert counters.forwarded_claims_granted == 0
    assert counters.forwarded_claims_denied == 0
    assert counters.forwarded_claim_timeouts == 0


async def test_forward_reports_timeout_and_refuses_in_place() -> None:
    forwarder = _FakeForwarder(error=ClaimForwardTimeoutError("owner timed out"))
    forwarding, rec, counters = _forwarding(
        claim_peers={"syn-remote": ClaimForwardPeer(uri="ws://remote/")},
        claim_forwarder=cast(ClaimForwarder, forwarder),
    )

    handled = await forwarding.forward("P/a", "P", "T1", {}, "syn-remote", _WS)

    assert handled is True
    frame = rec.sent[0][1]
    assert frame["type"] == MessageType.CLAIM_DENIED
    assert frame["forward_error"] == "timeout"
    assert frame["ownership"] == "remote"
    assert frame["owner_hub_id"] == "syn-remote"
    assert frame["namespace"] == "P"
    assert frame["task_id"] == "T1"
    assert "did not answer" in frame["payload"]
    assert counters.forwarded_claims == 1
    assert counters.forwarded_claim_timeouts == 1
    assert counters.forwarded_claims_granted == 0
    assert counters.forwarded_claims_denied == 0


async def test_forward_passes_connector_signer_local_id_and_token_when_configured() -> None:
    sentinel_connector = cast(Any, object())
    sentinel_signer = cast(Any, object())
    grant = ClaimForwardResult(
        granted=True,
        task_id="T1",
        namespace="P",
        owner_hub_id="syn-remote",
        grant={"task_id": "T1"},
    )
    forwarder = _FakeForwarder(result=grant)
    forwarding, _rec, _counters = _forwarding(
        claim_peers={
            "syn-remote": ClaimForwardPeer(
                uri="wss://remote/",
                token="peer-secret",
                connector=sentinel_connector,
                signer=sentinel_signer,
            )
        },
        claim_forwarder=cast(ClaimForwarder, forwarder),
        hub_id="syn-origin-42",
    )

    assert await forwarding.forward("P/a", "P", "T1", {"task_id": "T1"}, "syn-remote", _WS) is True
    assert len(forwarder.calls) == 1
    request, kwargs = forwarder.calls[0]
    assert request == ClaimForwardRequest(
        namespace="P", claimant="P/a", task_id="T1", claim={"task_id": "T1"}
    )
    assert kwargs == {
        "uri": "wss://remote/",
        "local_id": "syn-origin-42",
        "token": "peer-secret",
        "connector": sentinel_connector,
        "signer": sentinel_signer,
    }


async def test_forward_omits_connector_and_signer_when_absent() -> None:
    grant = ClaimForwardResult(
        granted=True,
        task_id="T1",
        namespace="P",
        owner_hub_id="syn-remote",
        grant={"task_id": "T1"},
    )
    forwarder = _FakeForwarder(result=grant)
    forwarding, _rec, _counters = _forwarding(
        claim_peers={"syn-remote": ClaimForwardPeer(uri="ws://remote/", token=None)},
        claim_forwarder=cast(ClaimForwarder, forwarder),
        hub_id="syn-origin-7",
    )

    assert await forwarding.forward("P/a", "P", "T1", {}, "syn-remote", _WS) is True
    assert len(forwarder.calls) == 1
    _request, kwargs = forwarder.calls[0]
    assert kwargs == {
        "uri": "ws://remote/",
        "local_id": "syn-origin-7",
        "token": None,
    }
    assert "connector" not in kwargs
    assert "signer" not in kwargs
