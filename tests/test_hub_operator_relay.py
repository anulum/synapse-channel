# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li — serving half of a cross-hub operator relay, over real sockets

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from websockets.asyncio.client import ClientConnection, connect

from hub_e2e_helpers import read_until_type, running_hub, send_json
from synapse_channel.core.federation import FederationBundle, FederationPeer, ScopeGrant
from synapse_channel.core.handlers import operator_relay as relay_handlers
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind, record_claim
from synapse_channel.core.multihub_serving import MultiHubServingGrant, MultiHubServingPolicy
from synapse_channel.core.namespace_ownership import NamespaceOwnership
from synapse_channel.core.operator_relay_wire import (
    RelayActionRequest,
    RelayActionResult,
    decode_relay_result,
    encode_relay_request,
)
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType
from synapse_channel.core.tls import (
    MTLSPeerTrustBundle,
    MTLSTrustedPeer,
    certificate_sha256_pin,
)

_REQUEST = MessageType.OPERATOR_RELAY_REQUEST
_REPLY = MessageType.OPERATOR_RELAY_RESULT
_NAMESPACE = "SYNAPSE-CHANNEL"
_ACTING = "syn-a"
_DOMAIN = "domain-b"
_KEY = "SYNAPSE-CHANNEL:main:2026-06"
_HOLDER = "SYNAPSE-CHANNEL/holder"


def _write_peer_cert(tmp_path: Path) -> tuple[str, bytes]:
    """Write a self-signed peer certificate; return its pin and live DER bytes."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    certfile = tmp_path / "peer-cert.pem"
    keyfile = tmp_path / "peer-key.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            "/CN=peer-b",
            "-keyout",
            str(keyfile),
            "-out",
            str(certfile),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    pin = certificate_sha256_pin(certfile)
    der = x509.load_pem_x509_certificate(certfile.read_bytes()).public_bytes(
        serialization.Encoding.DER
    )
    return pin, der


def _serving_policy(
    pin: str,
    der: bytes,
    *,
    sender: str = "peer",
    aliases: tuple[str, ...] = (),
    domain: str = _DOMAIN,
    signing_key: str = _KEY,
) -> MultiHubServingPolicy:
    """Build a serving policy trusting ``sender`` to relay a *release* into the namespace."""
    return MultiHubServingPolicy(
        federation=FederationBundle(
            [
                FederationPeer(
                    domain_id=domain,
                    namespaces=frozenset({_NAMESPACE}),
                    certificate_pins=frozenset({pin}),
                    signing_key_ids=frozenset({signing_key}),
                    scope_grants=(ScopeGrant(verb="release", namespace=_NAMESPACE),),
                )
            ]
        ),
        mtls=MTLSPeerTrustBundle(
            peers={
                domain: MTLSTrustedPeer(
                    peer_id=domain,
                    certificate_pins=frozenset({pin}),
                    signing_key_ids=frozenset({signing_key}),
                    projects=frozenset({_NAMESPACE}),
                )
            }
        ),
        grants={
            alias: MultiHubServingGrant(
                domain_id=domain, namespace=_NAMESPACE, signing_key_id=signing_key
            )
            for alias in (sender, *aliases)
        },
        clock=lambda: 0.0,
        cert_source=lambda _websocket: der,
    )


def _serving_policy_with_distinct_approver(pin: str, der: bytes) -> MultiHubServingPolicy:
    """Trust two independently keyed federation principals for the same release scope."""
    approver_domain = "domain-c"
    approver_key = "SYNAPSE-CHANNEL:approver:2026-07"
    peers = [
        FederationPeer(
            domain_id=_DOMAIN,
            namespaces=frozenset({_NAMESPACE}),
            certificate_pins=frozenset({pin}),
            signing_key_ids=frozenset({_KEY}),
            scope_grants=(ScopeGrant(verb="release", namespace=_NAMESPACE),),
        ),
        FederationPeer(
            domain_id=approver_domain,
            namespaces=frozenset({_NAMESPACE}),
            certificate_pins=frozenset({pin}),
            signing_key_ids=frozenset({approver_key}),
            scope_grants=(ScopeGrant(verb="release", namespace=_NAMESPACE),),
        ),
    ]
    return MultiHubServingPolicy(
        federation=FederationBundle(peers),
        mtls=MTLSPeerTrustBundle(
            peers={
                _DOMAIN: MTLSTrustedPeer(
                    peer_id=_DOMAIN,
                    certificate_pins=frozenset({pin}),
                    signing_key_ids=frozenset({_KEY}),
                    projects=frozenset({_NAMESPACE}),
                ),
                approver_domain: MTLSTrustedPeer(
                    peer_id=approver_domain,
                    certificate_pins=frozenset({pin}),
                    signing_key_ids=frozenset({approver_key}),
                    projects=frozenset({_NAMESPACE}),
                ),
            }
        ),
        grants={
            "peer": MultiHubServingGrant(_DOMAIN, _NAMESPACE, _KEY),
            "peer-approver": MultiHubServingGrant(approver_domain, _NAMESPACE, approver_key),
        },
        clock=lambda: 0.0,
        cert_source=lambda _websocket: der,
    )


def _acting_hub(
    *,
    policy: MultiHubServingPolicy | None,
    ownership: NamespaceOwnership | None,
    journal: EventStore | None = None,
    require_relay_reason: bool = False,
    require_two_person_relay: bool = False,
) -> SynapseHub:
    """Return a hub configured with the given serving policy, ownership map, and journal."""
    return SynapseHub(
        hub_id=_ACTING,
        multihub_serving_policy=policy,
        namespace_ownership=ownership,
        journal=journal,
        require_relay_reason=require_relay_reason,
        require_two_person_relay=require_two_person_relay,
    )


def _owns() -> NamespaceOwnership:
    """Return an ownership map under which this hub authoritatively owns the namespace."""
    return NamespaceOwnership(owners={_NAMESPACE: _ACTING}, local_hub_id=_ACTING)


def _request(
    action: str = "release",
    task_id: str = "t1",
    *,
    reason: str = "",
    break_glass: bool = False,
    operator: str = "ops-admin",
    idem_key: str = "",
) -> RelayActionRequest:
    return RelayActionRequest(
        action=action,
        namespace=_NAMESPACE,
        task_id=task_id,
        operator=operator,
        origin_hub_id=_DOMAIN,
        reason=reason,
        break_glass=break_glass,
        idem_key=idem_key,
    )


def _request_frame(request: RelayActionRequest, *, sender: str) -> dict[str, object]:
    """Return the handler frame used by the keyed-operation digest."""
    return {"sender": sender, "type": _REQUEST, **encode_relay_request(request)}


async def _connect(uri: str, name: str) -> ClientConnection:
    """Open a raw client socket, drain the welcome, and register with a heartbeat."""
    websocket = await connect(uri)
    await read_until_type(websocket, "welcome")
    await send_json(websocket, sender=name, type="heartbeat")
    return websocket


async def _relay(
    uri: str, request: RelayActionRequest, *, sender: str = "peer"
) -> RelayActionResult:
    """Relay one action as a peer hub and decode the result reply."""
    return decode_relay_result(await _relay_frame(uri, request, sender=sender))


async def _relay_frame(
    uri: str,
    request: RelayActionRequest,
    *,
    sender: str = "peer",
    reply_type: str = _REPLY,
) -> dict[str, object]:
    """Relay one action and return the exact response frame."""
    async with await _connect(uri, sender) as ws:
        await send_json(ws, sender=sender, type=_REQUEST, **encode_relay_request(request))
        return await read_until_type(ws, reply_type)


async def test_applies_a_relayed_release_and_audits_it(tmp_path: Path) -> None:
    pin, der = _write_peer_cert(tmp_path)
    journal = EventStore(tmp_path / "events.db")
    hub = _acting_hub(policy=_serving_policy(pin, der), ownership=_owns(), journal=journal)
    hub.state.claim(_HOLDER, "t1")
    async with running_hub(hub) as (_, uri):
        request = _request(reason="lease wedged by a crashed agent", break_glass=True)
        result = await _relay(uri, request)
    assert result.applied is True
    assert result.owner_hub_id == _ACTING
    assert "was held by" in result.detail
    assert "t1" not in hub.state.claims
    # Journalled twice: a release keeps state reconstruction correct, an operator_relay
    # event records the cross-hub provenance the release alone never carries.
    events = journal.read_all()
    assert [event.kind for event in events] == [EventKind.RELEASE, EventKind.OPERATOR_RELAY]
    assert events[1].seq == events[0].seq + 1
    assert events[1].ts == events[0].ts
    audit = events[1].payload
    assert audit["action"] == "release"
    assert audit["direction"] == "in"  # the applying (owning) side of the two-hub trail
    assert audit["peer"] == "peer"
    assert audit["operator"] == "ops-admin"
    assert audit["origin_hub_id"] == _DOMAIN
    assert audit["reason"] == "lease wedged by a crashed agent"
    assert audit["break_glass"] is True
    assert audit["previous_owner"] == _HOLDER
    assert audit["applied"] is True


async def test_keyed_relay_replays_exact_verdict_after_restart_and_conflicts_fail_closed(
    tmp_path: Path,
) -> None:
    pin, der = _write_peer_cert(tmp_path)
    db = tmp_path / "keyed-events.db"
    journal = EventStore(db)
    hub = _acting_hub(policy=_serving_policy(pin, der), ownership=_owns(), journal=journal)
    hub.state.claim(_HOLDER, "t1")
    request = _request(reason="lease wedged", idem_key="relay-attempt-7")
    async with running_hub(hub) as (_, uri):
        first = await _relay_frame(uri, request)
    assert "t1" not in hub.state.claims
    assert [event.kind for event in journal.read_all()] == [
        EventKind.RELEASE,
        EventKind.OPERATOR_RELAY,
        EventKind.IDEMPOTENCY,
    ]
    assert len(journal.read_operations()) == 1
    assert journal.pending_operation_outbox_count() == 0
    journal.close()

    reopened = EventStore(db)
    restarted = _acting_hub(policy=_serving_policy(pin, der), ownership=_owns(), journal=reopened)
    async with running_hub(restarted) as (_, uri):
        replayed = await _relay_frame(uri, request)
        conflict = await _relay_frame(
            uri,
            _request(reason="changed payload", idem_key="relay-attempt-7"),
            reply_type=MessageType.ERROR,
        )
    assert replayed == first
    assert conflict["error_code"] == "idempotency_conflict"
    assert "relay-attempt-7" not in str(conflict)
    assert len(reopened.read_all()) == 3
    reopened.close()


async def test_keyed_relay_reauthorises_before_replaying_a_committed_verdict(
    tmp_path: Path,
) -> None:
    pin, der = _write_peer_cert(tmp_path)
    db = tmp_path / "reauthorise.db"
    journal = EventStore(db)
    hub = _acting_hub(policy=_serving_policy(pin, der), ownership=_owns(), journal=journal)
    hub.state.claim(_HOLDER, "t1")
    request = _request(reason="lease wedged", idem_key="relay-attempt-7")
    async with running_hub(hub) as (_, uri):
        applied = await _relay(uri, request)
    assert applied.applied is True
    journal.close()

    _stranger_pin, stranger_der = _write_peer_cert(tmp_path / "stranger")
    reopened = EventStore(db)
    restarted = _acting_hub(
        policy=_serving_policy(pin, stranger_der),
        ownership=_owns(),
        journal=reopened,
    )
    async with running_hub(restarted) as (_, uri):
        refused = await _relay(uri, request)
    assert refused.applied is False
    assert refused.detail == "peer_not_authorised"
    assert len(reopened.read_all()) == 3
    reopened.close()


async def test_keyed_relay_does_not_cross_an_authorized_principal_change(
    tmp_path: Path,
) -> None:
    pin, der = _write_peer_cert(tmp_path)
    db = tmp_path / "principal-change.db"
    journal = EventStore(db)
    hub = _acting_hub(policy=_serving_policy(pin, der), ownership=_owns(), journal=journal)
    hub.state.claim(_HOLDER, "t1")
    request = _request(reason="lease wedged", idem_key="relay-attempt-7")
    async with running_hub(hub) as (_, uri):
        applied = await _relay(uri, request)
    assert applied.applied is True
    journal.close()

    reopened = EventStore(db)
    restarted = _acting_hub(
        policy=_serving_policy(
            pin,
            der,
            domain="domain-c",
            signing_key="SYNAPSE-CHANNEL:principal-c:2026-07",
        ),
        ownership=_owns(),
        journal=reopened,
    )
    async with running_hub(restarted) as (_, uri):
        not_replayed = await _relay(uri, request)
    assert not_replayed.applied is False
    assert "not currently claimed" in not_replayed.detail
    assert len(reopened.read_operations()) == 1
    assert len(reopened.read_all()) == 3
    reopened.close()


async def test_relay_journal_failure_restores_claim_and_commits_no_partial_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal = EventStore(tmp_path / "events.db")
    hub = _acting_hub(policy=None, ownership=None, journal=journal)
    assert hub.state.claim(_HOLDER, "t1")[0]
    before = hub.state.claims["t1"]
    before_snapshot = before.as_persisted_dict()

    def fail_batch(_store: EventStore, _task_id: str, _provenance: object) -> None:
        raise OSError("relay journal unavailable")

    monkeypatch.setattr(relay_handlers, "record_operator_release", fail_batch)
    with pytest.raises(OSError, match="relay journal unavailable"):
        await relay_handlers._apply_release_async(hub, "peer", _request())

    assert hub.state.claims["t1"] is before
    assert hub.state.claims["t1"].as_persisted_dict() == before_snapshot
    assert journal.read_all() == []
    journal.close()


async def test_refuses_a_relay_without_a_reason_when_the_hub_requires_one(tmp_path: Path) -> None:
    pin, der = _write_peer_cert(tmp_path)
    hub = _acting_hub(
        policy=_serving_policy(pin, der), ownership=_owns(), require_relay_reason=True
    )
    hub.state.claim(_HOLDER, "t1")
    async with running_hub(hub) as (_, uri):
        refused = await _relay(uri, _request())  # no reason
        applied = await _relay(uri, _request(reason="freeing a wedged release"))
    assert refused.applied is False
    assert refused.detail == "reason_required"
    assert applied.applied is True  # the same relay with a reason is authorised
    assert "t1" not in hub.state.claims


async def test_notifies_the_hubs_own_agents_that_the_lease_was_revoked(tmp_path: Path) -> None:
    pin, der = _write_peer_cert(tmp_path)
    hub = _acting_hub(policy=_serving_policy(pin, der), ownership=_owns())
    hub.state.claim(_HOLDER, "t1")
    async with running_hub(hub) as (_, uri):
        async with await _connect(uri, "watcher") as watcher:
            await _relay(uri, _request())
            revoked = await read_until_type(watcher, MessageType.RELEASE_GRANTED)
    assert revoked["task_id"] == "t1"
    assert "released by operator relay" in revoked["payload"]


@pytest.mark.parametrize("idem_key", ["", "no-op-relay-attempt"])
async def test_an_authorised_release_of_an_unclaimed_task_is_a_no_op(
    tmp_path: Path, idem_key: str
) -> None:
    pin, der = _write_peer_cert(tmp_path)
    journal = EventStore(tmp_path / "events.db")
    hub = _acting_hub(policy=_serving_policy(pin, der), ownership=_owns(), journal=journal)
    async with running_hub(hub) as (_, uri):
        result = await _relay(uri, _request(task_id="never-claimed", idem_key=idem_key))
    assert result.applied is False
    assert "not currently claimed" in result.detail
    # A no-op mutates nothing, so it journals nothing.
    assert [e.kind for e in journal.read_all()] == []


async def test_two_person_path_records_then_applies_without_a_journal() -> None:
    hub = _acting_hub(policy=None, ownership=None)
    assert hub.state.claim(_HOLDER, "t1")[0]
    first = await relay_handlers._apply_with_two_person_async(
        hub,
        "peer",
        _request(operator="alice"),
        "federation-peer:first",
    )
    second = await relay_handlers._apply_with_two_person_async(
        hub,
        "peer-approver",
        _request(operator="bob"),
        "federation-peer:second",
    )
    assert first.pending is True
    assert second.applied is True
    assert "t1" not in hub.state.claims


async def test_refuses_a_relay_when_no_serving_policy_is_configured() -> None:
    hub = _acting_hub(policy=None, ownership=_owns())
    hub.state.claim(_HOLDER, "t1")
    async with running_hub(hub) as (_, uri):
        result = await _relay(uri, _request())
    assert result.applied is False
    assert result.detail == "peer_not_authorised"
    # The lease is untouched.
    assert hub.state.claims["t1"].owner == _HOLDER


async def test_refuses_a_relay_from_an_untrusted_certificate(tmp_path: Path) -> None:
    pin, _trusted = _write_peer_cert(tmp_path)
    _other_pin, stranger_der = _write_peer_cert(tmp_path / "other")
    hub = _acting_hub(policy=_serving_policy(pin, stranger_der), ownership=_owns())
    hub.state.claim(_HOLDER, "t1")
    async with running_hub(hub) as (_, uri):
        result = await _relay(uri, _request())
    assert result.applied is False
    assert result.detail == "peer_not_authorised"
    assert hub.state.claims["t1"].owner == _HOLDER


async def test_refuses_a_relay_when_this_hub_cannot_prove_it_owns_the_namespace(
    tmp_path: Path,
) -> None:
    # With no ownership map the origin-routing gate steps aside, and the serving handler still
    # refuses fail-closed: a hub that cannot prove it authoritatively owns the namespace never
    # applies a relayed release. (A remote-owned namespace is instead intercepted by the gate
    # and forwarded or refused there — see test_hub_operator_relay_forwarding.)
    pin, der = _write_peer_cert(tmp_path)
    hub = _acting_hub(policy=_serving_policy(pin, der), ownership=None)
    hub.state.claim(_HOLDER, "t1")
    async with running_hub(hub) as (_, uri):
        result = await _relay(uri, _request())
    assert result.applied is False
    assert result.detail == "namespace_not_owned"
    assert hub.state.claims["t1"].owner == _HOLDER  # the lease is untouched


async def test_refuses_an_unregistered_action(tmp_path: Path) -> None:
    pin, der = _write_peer_cert(tmp_path)
    hub = _acting_hub(policy=_serving_policy(pin, der), ownership=_owns())
    async with running_hub(hub) as (_, uri):
        result = await _relay(uri, _request(action="delete-everything"))
    assert result.applied is False
    assert result.detail == "unknown_action"


async def test_a_malformed_relay_request_is_answered_with_an_error(tmp_path: Path) -> None:
    pin, der = _write_peer_cert(tmp_path)
    hub = _acting_hub(policy=_serving_policy(pin, der), ownership=_owns())
    async with running_hub(hub) as (_, uri):
        async with await _connect(uri, "peer") as ws:
            # No ``operator`` field: the codec rejects it before authorisation runs.
            await send_json(
                ws,
                sender="peer",
                type=_REQUEST,
                action="release",
                namespace=_NAMESPACE,
                task_id="t1",
                origin_hub_id=_DOMAIN,
            )
            message = await read_until_type(ws, MessageType.ERROR)
    assert "Malformed operator relay request" in message["payload"]


async def test_two_person_relay_records_pending_then_applies_on_a_second_operator(
    tmp_path: Path,
) -> None:
    pin, der = _write_peer_cert(tmp_path)
    journal = EventStore(tmp_path / "events.db")
    hub = _acting_hub(
        policy=_serving_policy_with_distinct_approver(pin, der),
        ownership=_owns(),
        journal=journal,
        require_two_person_relay=True,
    )
    hub.state.claim(_HOLDER, "t1")
    async with running_hub(hub) as (_, uri):
        first = await _relay(uri, _request(reason="wedged", operator="alice"))
        # The first operator's authorised relay is recorded, not applied: the lease is untouched.
        assert first.applied is False
        assert first.pending is True
        assert "awaiting approval by a second operator" in first.detail
        assert hub.state.claims["t1"].owner == _HOLDER

        second = await _relay(
            uri,
            _request(reason="confirmed", operator="bob"),
            sender="peer-approver",
        )
        assert second.applied is True
        assert second.pending is False
    assert "t1" not in hub.state.claims  # the second, different operator carried it out

    audits = [e.payload for e in journal.read_all() if e.kind == EventKind.OPERATOR_RELAY]
    pending, applied = audits[0], audits[1]
    assert pending["status"] == "pending"
    assert pending["applied"] is False
    assert pending["requester"] == "alice"
    assert pending["requester_principal"].startswith("federation-peer:")
    assert applied["status"] == "applied"
    assert applied["applied"] is True
    assert applied["operator"] == "bob"
    assert applied["approver"] == "bob"  # the approving second operator is recorded
    assert applied["requester_principal"] == pending["requester_principal"]
    assert applied["approver_principal"].startswith("federation-peer:")
    assert applied["approver_principal"] != applied["requester_principal"]


async def test_keyed_two_person_release_commits_once_and_replays_after_restart(
    tmp_path: Path,
) -> None:
    pin, der = _write_peer_cert(tmp_path)
    db = tmp_path / "keyed-two-person.db"
    journal = EventStore(db)
    hub = _acting_hub(
        policy=_serving_policy_with_distinct_approver(pin, der),
        ownership=_owns(),
        journal=journal,
        require_two_person_relay=True,
    )
    hub.state.claim(_HOLDER, "t1")
    first_request = _request(reason="wedged", operator="alice", idem_key="relay-requester-1")
    second_request = _request(reason="confirmed", operator="bob", idem_key="relay-approver-1")
    async with running_hub(hub) as (_, uri):
        first = await _relay_frame(uri, first_request)
        second = await _relay_frame(uri, second_request, sender="peer-approver")
    assert first["pending"] is True
    assert second["applied"] is True
    assert "t1" not in hub.state.claims
    assert hub.relay_approvals.pending_count == 0
    assert [event.kind for event in journal.read_all()] == [
        EventKind.OPERATOR_RELAY,
        EventKind.IDEMPOTENCY,
        EventKind.RELEASE,
        EventKind.OPERATOR_RELAY,
        EventKind.IDEMPOTENCY,
    ]
    assert len(journal.read_operations()) == 2
    assert journal.pending_operation_outbox_count() == 0
    journal.close()

    reopened = EventStore(db)
    restarted = _acting_hub(
        policy=_serving_policy_with_distinct_approver(pin, der),
        ownership=_owns(),
        journal=reopened,
        require_two_person_relay=True,
    )
    async with running_hub(restarted) as (_, uri):
        replayed = await _relay_frame(uri, second_request, sender="peer-approver")
    assert replayed == second
    assert restarted.relay_approvals.pending_count == 0
    assert len(reopened.read_all()) == 5
    reopened.close()


async def test_keyed_pending_replays_exactly_and_can_complete_after_restart(
    tmp_path: Path,
) -> None:
    pin, der = _write_peer_cert(tmp_path)
    db = tmp_path / "pending-restart.db"
    journal = EventStore(db)
    hub = _acting_hub(
        policy=_serving_policy_with_distinct_approver(pin, der),
        ownership=_owns(),
        journal=journal,
        require_two_person_relay=True,
    )
    assert hub.state.claim(_HOLDER, "t1")[0]
    record_claim(journal, hub.state.claims["t1"])
    first_request = _request(reason="wedged", operator="alice", idem_key="requester-1")
    second_request = _request(reason="confirmed", operator="bob", idem_key="approver-1")
    async with running_hub(hub) as (_, uri):
        first = await _relay_frame(uri, first_request)
    assert first["pending"] is True
    journal.close()

    reopened = EventStore(db)
    restarted = _acting_hub(
        policy=_serving_policy_with_distinct_approver(pin, der),
        ownership=_owns(),
        journal=reopened,
        require_two_person_relay=True,
    )
    assert restarted.relay_approvals.pending_count == 1
    before_retry = len(reopened.read_all())
    async with running_hub(restarted) as (_, uri):
        replayed = await _relay_frame(uri, first_request)
        applied = await _relay_frame(uri, second_request, sender="peer-approver")
    assert replayed == first
    assert applied["applied"] is True
    assert "t1" not in restarted.state.claims
    assert restarted.relay_approvals.pending_count == 0
    assert len(reopened.read_all()) == before_retry + 3
    reopened.close()


async def test_keyed_pending_audit_failure_does_not_publish_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal = EventStore(tmp_path / "pending-failure.db")
    hub = _acting_hub(policy=None, ownership=None, journal=journal)
    request = _request(operator="alice", idem_key="relay-requester-1")

    def fail_audit(**_kwargs: object) -> object:
        raise OSError("pending audit unavailable")

    monkeypatch.setattr(journal, "commit_operation", fail_audit)
    with pytest.raises(OSError, match="pending audit unavailable"):
        await relay_handlers._apply_with_two_person_atomic_async(
            hub,
            "peer",
            request,
            "federation-peer:first",
            _request_frame(request, sender="peer"),
        )
    assert hub.relay_approvals.pending_count == 0
    assert journal.read_all() == []
    journal.close()


async def test_unkeyed_pending_audit_failure_does_not_publish_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal = EventStore(tmp_path / "unkeyed-pending-failure.db")
    hub = _acting_hub(policy=None, ownership=None, journal=journal)
    request = _request(operator="alice")

    def fail_audit(_store: EventStore, _payload: object) -> None:
        raise OSError("pending audit unavailable")

    monkeypatch.setattr(relay_handlers, "record_operator_relay", fail_audit)
    with pytest.raises(OSError, match="pending audit unavailable"):
        await relay_handlers._apply_with_two_person_async(
            hub,
            "peer",
            request,
            "federation-peer:first",
        )
    assert hub.relay_approvals.pending_count == 0
    assert journal.read_all() == []
    journal.close()


async def test_keyed_approval_commit_failure_retains_pending_lease_and_quorum(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal = EventStore(tmp_path / "approval-failure.db")
    hub = _acting_hub(policy=None, ownership=None, journal=journal)
    assert hub.state.claim(_HOLDER, "t1")[0]
    first = _request(operator="alice", idem_key="relay-requester-1")
    first_execution = await relay_handlers._apply_with_two_person_atomic_async(
        hub,
        "peer",
        first,
        "federation-peer:first",
        _request_frame(first, sender="peer"),
    )
    assert first_execution is not None and first_execution.outcome == "inserted"
    assert hub.relay_approvals.pending_count == 1

    def fail_commit(**_kwargs: object) -> object:
        raise OSError("approval commit unavailable")

    monkeypatch.setattr(journal, "commit_operation", fail_commit)
    second = _request(operator="bob", idem_key="relay-approver-1")
    with pytest.raises(OSError, match="approval commit unavailable"):
        await relay_handlers._apply_with_two_person_atomic_async(
            hub,
            "peer-approver",
            second,
            "federation-peer:second",
            _request_frame(second, sender="peer-approver"),
        )
    assert hub.relay_approvals.pending_count == 1
    assert hub.state.claims["t1"].owner == _HOLDER
    assert [event.kind for event in journal.read_all()] == [
        EventKind.OPERATOR_RELAY,
        EventKind.IDEMPOTENCY,
    ]
    journal.close()


async def test_keyed_completed_quorum_audits_an_unclaimed_noop(tmp_path: Path) -> None:
    journal = EventStore(tmp_path / "approved-noop.db")
    hub = _acting_hub(policy=None, ownership=None, journal=journal)
    first = _request(task_id="absent", operator="alice", idem_key="relay-requester-1")
    second = _request(task_id="absent", operator="bob", idem_key="relay-approver-1")
    await relay_handlers._apply_with_two_person_atomic_async(
        hub,
        "peer",
        first,
        "federation-peer:first",
        _request_frame(first, sender="peer"),
    )
    execution = await relay_handlers._apply_with_two_person_atomic_async(
        hub,
        "peer-approver",
        second,
        "federation-peer:second",
        _request_frame(second, sender="peer-approver"),
    )
    assert execution is not None and execution.outcome == "inserted"
    assert execution.mutation.result.applied is False
    assert execution.mutation.result.pending is False
    assert hub.relay_approvals.pending_count == 0
    audits = [
        event.payload for event in journal.read_all() if event.kind == EventKind.OPERATOR_RELAY
    ]
    assert [audit["status"] for audit in audits] == ["pending", "not_applied"]
    assert audits[-1]["detail"] == execution.mutation.result.detail
    assert len(journal.read_operations()) == 2
    journal.close()


async def test_two_person_relay_pending_without_a_journal_does_not_audit(tmp_path: Path) -> None:
    # A hub with no journal still records the pending request in memory and answers pending,
    # it simply writes no audit event (there is nowhere to write it).
    pin, der = _write_peer_cert(tmp_path)
    hub = _acting_hub(
        policy=_serving_policy(pin, der),
        ownership=_owns(),
        journal=None,
        require_two_person_relay=True,
    )
    hub.state.claim(_HOLDER, "t1")
    async with running_hub(hub) as (_, uri):
        first = await _relay(uri, _request(operator="alice"))
    assert first.pending is True
    assert hub.state.claims["t1"].owner == _HOLDER
    assert hub.relay_approvals.pending_count == 1


async def test_two_person_relay_refuses_alias_approval_from_the_same_principal(
    tmp_path: Path,
) -> None:
    pin, der = _write_peer_cert(tmp_path)
    journal = EventStore(tmp_path / "events.db")
    hub = _acting_hub(
        policy=_serving_policy(pin, der, aliases=("peer-alias",)),
        ownership=_owns(),
        journal=journal,
        require_two_person_relay=True,
    )
    hub.state.claim(_HOLDER, "t1")
    async with running_hub(hub) as (_, uri):
        first = await _relay(uri, _request(reason="wedged", operator="alice"))
        repeat = await _relay(
            uri,
            _request(reason="again", operator="bob"),
            sender="peer-alias",
        )
    # A different label and sender alias backed by the same verified trust material cannot
    # complete the quorum: the lease stays held.
    assert first.pending is True
    assert repeat.pending is True
    assert "awaiting a distinct principal" in repeat.detail
    assert hub.state.claims["t1"].owner == _HOLDER
    # Nothing was released, so no release event was journalled.
    events = journal.read_all()
    assert EventKind.RELEASE not in [event.kind for event in events]
    assert [event.payload["detail"] for event in events] == [first.detail, repeat.detail]
