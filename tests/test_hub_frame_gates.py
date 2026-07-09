# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — unit tests for the hub's frame-authorisation gates

from __future__ import annotations

import time
from typing import Any, cast

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from synapse_channel.core.acl import CLAIM, AclPolicy, AclRule
from synapse_channel.core.hub_counters import HubCounters
from synapse_channel.core.hub_frame_gates import HubFrameGates
from synapse_channel.core.message_auth import (
    EventSignatureKey,
    EventSignatureTrustBundle,
    MessageAuthKey,
    MessageReplayCache,
    sign_event_frame,
    sign_frame,
)
from synapse_channel.core.multihub_claim_transport import (
    ClaimForwarder,
    ClaimForwardError,
    ClaimForwardPeer,
    ClaimForwardTimeoutError,
)
from synapse_channel.core.multihub_claim_wire import ClaimForwardResult
from synapse_channel.core.namespace_ownership import NamespaceOwnership
from synapse_channel.core.protocol import MessageType, build_envelope, system_message

_WS = object()  # opaque socket token; the gates only forward it to send_json


class _Recorder:
    """Records every denial the gates send through the injected send callback."""

    def __init__(self) -> None:
        self.sent: list[tuple[Any, dict[str, Any]]] = []

    async def send_json(self, websocket: Any, data: dict[str, Any]) -> None:
        self.sent.append((websocket, data))


class _FakeForwarder:
    """A claim forwarder returning a canned result or raising, and recording calls."""

    def __init__(
        self, *, result: ClaimForwardResult | None = None, error: Exception | None = None
    ) -> None:
        self._result = result
        self._error = error
        self.calls: list[tuple[str, str | None]] = []

    async def __call__(
        self, request: Any, *, uri: str, local_id: str, token: str | None = None
    ) -> ClaimForwardResult:
        self.calls.append((uri, local_id))
        if self._error is not None:
            raise self._error
        assert self._result is not None
        return self._result


def _system(payload: str, **extra: Any) -> dict[str, Any]:
    return system_message(payload, hub_id="syn-local", **extra)


def _never_forwards() -> ClaimForwarder:
    return cast(ClaimForwarder, _FakeForwarder(error=AssertionError("must not forward")))


def _gates(
    *,
    require_per_message_auth: bool = False,
    per_message_auth_keys: dict[str, MessageAuthKey] | None = None,
    message_replay: MessageReplayCache | None = None,
    signed_event_trust_bundle: EventSignatureTrustBundle | None = None,
    require_acl: bool = False,
    acl_policy: AclPolicy | None = None,
    namespace_ownership: NamespaceOwnership | None = None,
    observed_asserting_hubs: Any = None,
    claim_peers: dict[str, ClaimForwardPeer] | None = None,
    claim_forwarder: ClaimForwarder | None = None,
    counters: HubCounters | None = None,
    recorder: _Recorder | None = None,
) -> tuple[HubFrameGates, _Recorder]:
    rec = recorder or _Recorder()
    gates = HubFrameGates(
        require_per_message_auth=require_per_message_auth,
        per_message_auth_keys=per_message_auth_keys or {},
        message_replay=message_replay or MessageReplayCache(window_seconds=30.0, max_entries=16),
        signed_event_trust_bundle=signed_event_trust_bundle,
        require_acl=require_acl,
        acl_policy=acl_policy,
        namespace_ownership=namespace_ownership,
        observed_asserting_hubs=observed_asserting_hubs,
        claim_peers=claim_peers,
        claim_forwarder=claim_forwarder or _never_forwards(),
        counters=counters or HubCounters(),
        hub_id="syn-local",
        send_json=rec.send_json,
        system=_system,
    )
    return gates, rec


def _acl_policy() -> AclPolicy:
    return AclPolicy(
        [
            AclRule(CLAIM, "claim", "*", "P", "may hold P tasks"),
            AclRule(CLAIM, "path", "src/*", "P", "core may claim src"),
        ]
    )


# -- verify_per_message_auth -------------------------------------------------


async def test_per_message_auth_off_admits_everything() -> None:
    gates, rec = _gates(require_per_message_auth=False)
    assert await gates.verify_per_message_auth("ALPHA", "claim", {}, _WS) is True
    assert rec.sent == []


async def test_per_message_auth_ignores_unsigned_message_types() -> None:
    gates, rec = _gates(require_per_message_auth=True)
    assert await gates.verify_per_message_auth("ALPHA", "chat", {}, _WS) is True
    assert rec.sent == []


async def test_per_message_auth_admits_a_valid_hmac_frame() -> None:
    key = MessageAuthKey(key_id="main", secret=b"shared-secret", senders=frozenset({"ALPHA"}))
    signed = sign_frame(
        build_envelope("ALPHA", "claim", target="System", task_id="T1", now=1.0),
        key=key,
        nonce="n1",
        sequence=1,
        timestamp=time.time(),
    )
    gates, rec = _gates(require_per_message_auth=True, per_message_auth_keys={"main": key})

    assert await gates.verify_per_message_auth("ALPHA", "claim", signed, _WS) is True
    assert rec.sent == []


async def test_per_message_auth_refuses_an_hmac_frame_for_the_wrong_sender() -> None:
    key = MessageAuthKey(key_id="main", secret=b"shared-secret", senders=frozenset({"ALPHA"}))
    signed = sign_frame(
        build_envelope("ALPHA", "claim", target="System", task_id="T1", now=1.0),
        key=key,
        nonce="n1",
        sequence=1,
        timestamp=time.time(),
    )
    gates, rec = _gates(require_per_message_auth=True, per_message_auth_keys={"main": key})

    assert await gates.verify_per_message_auth("BETA", "claim", signed, _WS) is False
    assert rec.sent[0][1]["type"] == MessageType.ERROR
    assert "per-message authentication failed" in rec.sent[0][1]["payload"]


async def test_per_message_auth_admits_a_valid_signed_event() -> None:
    private_key = Ed25519PrivateKey.generate()
    key = EventSignatureKey.from_private_key(
        key_id="K1",
        private_key=private_key,
        senders=frozenset({"ALPHA"}),
        projects=frozenset({"P"}),
    )
    signed = sign_event_frame(
        build_envelope("ALPHA", "claim", target="System", task_id="T1", project="P", now=1.0),
        key_id=key.key_id,
        private_key=private_key,
        nonce="e1",
        sequence=1,
        signed_at=time.time(),
    )
    bundle = EventSignatureTrustBundle(
        keys={key.key_id: key},
        replay_cache=MessageReplayCache(window_seconds=30.0, max_entries=16),
    )
    gates, rec = _gates(require_per_message_auth=True, signed_event_trust_bundle=bundle)

    assert await gates.verify_per_message_auth("ALPHA", "claim", signed, _WS) is True
    assert rec.sent == []


async def test_per_message_auth_refuses_a_tampered_signed_event() -> None:
    private_key = Ed25519PrivateKey.generate()
    key = EventSignatureKey.from_private_key(
        key_id="K1",
        private_key=private_key,
        senders=frozenset({"ALPHA"}),
        projects=frozenset({"P"}),
    )
    signed = sign_event_frame(
        build_envelope("ALPHA", "claim", target="System", task_id="T1", project="P", now=1.0),
        key_id=key.key_id,
        private_key=private_key,
        nonce="e1",
        sequence=1,
        signed_at=time.time(),
    )
    signed["task_id"] = "TAMPERED"  # invalidates the signature
    bundle = EventSignatureTrustBundle(
        keys={key.key_id: key},
        replay_cache=MessageReplayCache(window_seconds=30.0, max_entries=16),
    )
    gates, rec = _gates(require_per_message_auth=True, signed_event_trust_bundle=bundle)

    assert await gates.verify_per_message_auth("ALPHA", "claim", signed, _WS) is False
    assert rec.sent[0][1]["type"] == MessageType.ERROR


async def test_per_message_auth_refuses_a_signature_without_a_trust_bundle() -> None:
    # A signature is present but no bundle is configured, so the signed-event path is
    # not taken and the frame is treated as missing authentication.
    gates, rec = _gates(require_per_message_auth=True)
    data = {"type": "claim", "signature": {"key_id": "K1", "value": "x"}}

    assert await gates.verify_per_message_auth("ALPHA", "claim", data, _WS) is False
    assert rec.sent[0][1]["verification_result"] == "missing"


async def test_per_message_auth_refuses_a_frame_with_no_credential() -> None:
    gates, rec = _gates(require_per_message_auth=True)

    assert await gates.verify_per_message_auth("ALPHA", "claim", {"type": "claim"}, _WS) is False
    assert rec.sent[0][1]["verification_result"] == "missing"


# -- authorise_acl -----------------------------------------------------------


async def test_acl_off_admits_everything() -> None:
    gates, rec = _gates(require_acl=False, acl_policy=_acl_policy())
    assert await gates.authorise_acl("Q/a", "claim", {"task_id": "T1"}, _WS) is True
    assert rec.sent == []


async def test_acl_on_without_a_policy_admits_everything() -> None:
    gates, rec = _gates(require_acl=True, acl_policy=None)
    assert await gates.authorise_acl("Q/a", "claim", {"task_id": "T1"}, _WS) is True
    assert rec.sent == []


async def test_acl_admits_an_allowed_frame() -> None:
    gates, rec = _gates(require_acl=True, acl_policy=_acl_policy())
    assert await gates.authorise_acl("P/a", "claim", {"task_id": "T1"}, _WS) is True
    assert rec.sent == []


async def test_acl_refuses_a_denied_frame() -> None:
    gates, rec = _gates(require_acl=True, acl_policy=_acl_policy())

    assert await gates.authorise_acl("Q/a", "claim", {"task_id": "T1"}, _WS) is False
    _, frame = rec.sent[0]
    assert frame["type"] == MessageType.ERROR
    assert frame["acl_decision"] == "would_deny"
    assert "access denied" in frame["payload"]


# -- authorise_claim_ownership -----------------------------------------------


async def test_claim_ownership_no_map_grants_locally() -> None:
    gates, rec = _gates(namespace_ownership=None)
    assert await gates.authorise_claim_ownership("P/a", MessageType.CLAIM, {}, _WS) is True
    assert rec.sent == []


async def test_claim_ownership_ignores_non_claim_frames() -> None:
    ownership = NamespaceOwnership({"P": "syn-remote"}, "syn-local")
    gates, rec = _gates(namespace_ownership=ownership)
    assert await gates.authorise_claim_ownership("P/a", "chat", {}, _WS) is True
    assert rec.sent == []


async def test_claim_ownership_grants_a_locally_owned_namespace() -> None:
    ownership = NamespaceOwnership({"P": "syn-local"}, "syn-local")
    gates, rec = _gates(namespace_ownership=ownership)
    assert (
        await gates.authorise_claim_ownership("P/a", MessageType.CLAIM, {"task_id": "T1"}, _WS)
        is True
    )
    assert rec.sent == []


async def test_claim_ownership_forwards_a_remote_owned_namespace() -> None:
    ownership = NamespaceOwnership({"P": "syn-remote"}, "syn-local")
    grant = ClaimForwardResult(
        granted=True,
        task_id="T1",
        namespace="P",
        owner_hub_id="syn-remote",
        grant={"task_id": "T1", "lease_expires_at": 9.0},
    )
    forwarder = _FakeForwarder(result=grant)
    gates, rec = _gates(
        namespace_ownership=ownership,
        claim_peers={"syn-remote": ClaimForwardPeer(uri="ws://remote/")},
        claim_forwarder=cast(ClaimForwarder, forwarder),
    )

    result = await gates.authorise_claim_ownership("P/a", MessageType.CLAIM, {"task_id": "T1"}, _WS)

    assert result is False  # handled here — the local grant path must not also run
    assert forwarder.calls == [("ws://remote/", "syn-local")]
    assert rec.sent[0][1]["type"] == MessageType.CLAIM_GRANTED


async def test_claim_ownership_refuses_when_the_forward_fails() -> None:
    ownership = NamespaceOwnership({"P": "syn-remote"}, "syn-local")
    forwarder = _FakeForwarder(error=ClaimForwardError("owner unreachable"))
    gates, rec = _gates(
        namespace_ownership=ownership,
        claim_peers={"syn-remote": ClaimForwardPeer(uri="ws://remote/")},
        claim_forwarder=cast(ClaimForwarder, forwarder),
    )

    result = await gates.authorise_claim_ownership("P/a", MessageType.CLAIM, {"task_id": "T1"}, _WS)

    assert result is False
    frame = rec.sent[0][1]
    assert frame["type"] == MessageType.CLAIM_DENIED
    assert "does not own namespace" in frame["payload"]


async def test_claim_ownership_refuses_an_ungoverned_namespace() -> None:
    ownership = NamespaceOwnership({"Q": "syn-remote"}, "syn-local")  # P is unowned
    gates, rec = _gates(namespace_ownership=ownership)

    result = await gates.authorise_claim_ownership("P/a", MessageType.CLAIM, {"task_id": "T1"}, _WS)

    assert result is False
    assert rec.sent[0][1]["ownership"] == "ungoverned"


async def test_claim_ownership_refuses_a_partitioned_namespace() -> None:
    # This hub owns P, but a peer is observed asserting P too — a partition, so every
    # grant is refused until it is re-established.
    ownership = NamespaceOwnership({"P": "syn-local"}, "syn-local")
    gates, rec = _gates(
        namespace_ownership=ownership,
        observed_asserting_hubs=lambda namespace: ["syn-other"],
    )

    result = await gates.authorise_claim_ownership("P/a", MessageType.CLAIM, {"task_id": "T1"}, _WS)

    assert result is False
    assert rec.sent[0][1]["ownership"] == "partitioned"


# -- observed_asserting_hubs -------------------------------------------------


def test_observed_asserting_hubs_is_empty_without_a_feed() -> None:
    gates, _ = _gates(observed_asserting_hubs=None)
    assert gates.observed_asserting_hubs("P") == ()


def test_observed_asserting_hubs_reads_the_feed() -> None:
    gates, _ = _gates(observed_asserting_hubs=lambda namespace: [f"{namespace}-h1", "h2"])
    assert gates.observed_asserting_hubs("P") == ("P-h1", "h2")


# -- forward_remote_claim ----------------------------------------------------


async def test_forward_remote_claim_without_peers_is_a_no_op() -> None:
    gates, rec = _gates(claim_peers=None)
    assert await gates.forward_remote_claim("P/a", "P", "T1", {}, "syn-remote", _WS) is False
    assert rec.sent == []


async def test_forward_remote_claim_without_a_route_for_the_owner() -> None:
    gates, rec = _gates(claim_peers={"other": ClaimForwardPeer(uri="ws://other/")})
    assert await gates.forward_remote_claim("P/a", "P", "T1", {}, "syn-remote", _WS) is False
    assert rec.sent == []


async def test_forward_remote_claim_without_a_task_id() -> None:
    gates, rec = _gates(claim_peers={"syn-remote": ClaimForwardPeer(uri="ws://remote/")})
    assert await gates.forward_remote_claim("P/a", "P", "", {}, "syn-remote", _WS) is False
    assert rec.sent == []


async def test_forward_remote_claim_relays_a_grant() -> None:
    grant = ClaimForwardResult(
        granted=True,
        task_id="T1",
        namespace="P",
        owner_hub_id="syn-remote",
        detail="granted upstream",
        grant={"task_id": "T1", "lease_expires_at": 9.0},
    )
    forwarder = _FakeForwarder(result=grant)
    counters = HubCounters()
    gates, rec = _gates(
        claim_peers={"syn-remote": ClaimForwardPeer(uri="ws://remote/", token="tok")},
        claim_forwarder=cast(ClaimForwarder, forwarder),
        counters=counters,
    )

    result = await gates.forward_remote_claim("P/a", "P", "T1", {}, "syn-remote", _WS)

    assert result is True
    frame = rec.sent[0][1]
    assert frame["type"] == MessageType.CLAIM_GRANTED
    assert frame["task_id"] == "T1"
    assert counters.forwarded_claims == 1
    assert counters.forwarded_claims_granted == 1


async def test_forward_remote_claim_relays_a_denial() -> None:
    denied = ClaimForwardResult(
        granted=False, task_id="T1", namespace="P", owner_hub_id="syn-remote"
    )
    forwarder = _FakeForwarder(result=denied)
    counters = HubCounters()
    gates, rec = _gates(
        claim_peers={"syn-remote": ClaimForwardPeer(uri="ws://remote/")},
        claim_forwarder=cast(ClaimForwarder, forwarder),
        counters=counters,
    )

    result = await gates.forward_remote_claim("P/a", "P", "T1", {}, "syn-remote", _WS)

    assert result is True
    assert rec.sent[0][1]["type"] == MessageType.CLAIM_DENIED
    assert counters.forwarded_claims == 1
    assert counters.forwarded_claims_denied == 1


async def test_forward_remote_claim_treats_a_grantless_grant_as_a_denial() -> None:
    # granted is True but no lease fields accompanied it — relayed as a denial.
    grantless = ClaimForwardResult(
        granted=True, task_id="T1", namespace="P", owner_hub_id="syn-remote", grant=None
    )
    forwarder = _FakeForwarder(result=grantless)
    gates, rec = _gates(
        claim_peers={"syn-remote": ClaimForwardPeer(uri="ws://remote/")},
        claim_forwarder=cast(ClaimForwarder, forwarder),
    )

    assert await gates.forward_remote_claim("P/a", "P", "T1", {}, "syn-remote", _WS) is True
    assert rec.sent[0][1]["type"] == MessageType.CLAIM_DENIED


async def test_forward_remote_claim_returns_false_on_a_forward_error() -> None:
    forwarder = _FakeForwarder(error=ClaimForwardError("owner unreachable"))
    counters = HubCounters()
    gates, rec = _gates(
        claim_peers={"syn-remote": ClaimForwardPeer(uri="ws://remote/")},
        claim_forwarder=cast(ClaimForwarder, forwarder),
        counters=counters,
    )

    assert await gates.forward_remote_claim("P/a", "P", "T1", {}, "syn-remote", _WS) is False
    assert rec.sent == []
    assert counters.forwarded_claims == 1
    assert counters.forwarded_claim_timeouts == 0


async def test_forward_remote_claim_reports_timeout_and_refuses_in_place() -> None:
    forwarder = _FakeForwarder(error=ClaimForwardTimeoutError("owner timed out"))
    counters = HubCounters()
    gates, rec = _gates(
        claim_peers={"syn-remote": ClaimForwardPeer(uri="ws://remote/")},
        claim_forwarder=cast(ClaimForwarder, forwarder),
        counters=counters,
    )

    handled = await gates.forward_remote_claim("P/a", "P", "T1", {}, "syn-remote", _WS)

    assert handled is True
    frame = rec.sent[0][1]
    assert frame["type"] == MessageType.CLAIM_DENIED
    assert frame["forward_error"] == "timeout"
    assert "did not answer" in frame["payload"]
    assert counters.forwarded_claims == 1
    assert counters.forwarded_claim_timeouts == 1
