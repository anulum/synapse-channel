# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li — the serving half of cross-hub claim forwarding, over real sockets

from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from websockets.asyncio.client import ClientConnection, connect

from hub_e2e_helpers import read_until_type, running_hub, send_json
from synapse_channel.core.federation import FederationBundle, FederationPeer, ScopeGrant
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind, record_chat
from synapse_channel.core.multihub_claim_wire import (
    ClaimForwardRequest,
    ClaimForwardResult,
    decode_claim_forward_result,
    encode_claim_forward_request,
)
from synapse_channel.core.multihub_serving import MultiHubServingGrant, MultiHubServingPolicy
from synapse_channel.core.namespace_ownership import NamespaceOwnership
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType
from synapse_channel.core.state_models import TaskClaim
from synapse_channel.core.tls import (
    MTLSPeerTrustBundle,
    MTLSTrustedPeer,
    certificate_sha256_pin,
)

_RESULT = MessageType.MULTIHUB_CLAIM_REQUEST
_REPLY = MessageType.MULTIHUB_CLAIM_RESULT
_NAMESPACE = "SYNAPSE-CHANNEL"
_OWNER = "syn-a"
_DOMAIN = "domain-b"
_KEY = "SYNAPSE-CHANNEL:main:2026-06"


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
    pin: str, der: bytes, *, sender: str = "peer", claim_scope: bool = True
) -> MultiHubServingPolicy:
    """Build a serving policy trusting ``sender`` under ``pin``, reading ``der`` off any socket.

    The end-to-end harness runs over plaintext ``ws://``, so a real client certificate is not
    available; injecting the certificate source proves the handler wiring without a mutual-TLS
    handshake, which the ``test_multihub_serving`` unit tests cover separately. With
    ``claim_scope`` false the peering grants no ``claim`` scope, so forwarded claims are refused.
    """
    scope = (ScopeGrant(verb="claim", namespace=_NAMESPACE),) if claim_scope else ()
    return MultiHubServingPolicy(
        federation=FederationBundle(
            [
                FederationPeer(
                    domain_id=_DOMAIN,
                    namespaces=frozenset({_NAMESPACE}),
                    certificate_pins=frozenset({pin}),
                    signing_key_ids=frozenset({_KEY}),
                    scope_grants=scope,
                )
            ]
        ),
        mtls=MTLSPeerTrustBundle(
            peers={
                _DOMAIN: MTLSTrustedPeer(
                    peer_id=_DOMAIN,
                    certificate_pins=frozenset({pin}),
                    signing_key_ids=frozenset({_KEY}),
                    projects=frozenset({_NAMESPACE}),
                )
            }
        ),
        grants={
            sender: MultiHubServingGrant(
                domain_id=_DOMAIN, namespace=_NAMESPACE, signing_key_id=_KEY
            )
        },
        clock=lambda: 0.0,
        cert_source=lambda _websocket: der,
    )


def _owning_hub(
    *,
    policy: MultiHubServingPolicy | None,
    ownership: NamespaceOwnership | None,
    journal: EventStore | None = None,
) -> SynapseHub:
    """Return an owning hub configured with the given serving policy and ownership map."""
    return SynapseHub(
        hub_id=_OWNER,
        multihub_serving_policy=policy,
        namespace_ownership=ownership,
        journal=journal,
    )


def _corrupted_store(path: Path) -> EventStore:
    """Return a store whose one durable chat row no longer decodes, as a torn write leaves it."""
    store = EventStore(path)
    seq = record_chat(store, {"sender": "SYNAPSE-CHANNEL/x", "target": "all", "payload": "p"})
    store._conn.execute("UPDATE events SET payload = 'not-json' WHERE seq = ?", (seq,))
    store._conn.commit()
    return store


def _owns() -> NamespaceOwnership:
    """Return an ownership map under which this hub authoritatively owns the namespace."""
    return NamespaceOwnership(owners={_NAMESPACE: _OWNER}, local_hub_id=_OWNER)


async def _connect(uri: str, name: str) -> ClientConnection:
    """Open a raw client socket, drain the welcome, and register with a heartbeat."""
    websocket = await connect(uri)
    await read_until_type(websocket, "welcome")
    await send_json(websocket, sender=name, type="heartbeat")
    return websocket


async def _forward(uri: str, request: ClaimForwardRequest) -> ClaimForwardResult:
    """Forward one claim as a peer hub and decode the result reply."""
    async with await _connect(uri, "peer") as ws:
        await send_json(ws, sender="peer", type=_RESULT, **encode_claim_forward_request(request))
        message = await read_until_type(ws, _REPLY)
    return decode_claim_forward_result(message)


def _request(task_id: str = "t1", claimant: str = "SYNAPSE-CHANNEL/alice") -> ClaimForwardRequest:
    """Return a claim-forward request for the owned namespace."""
    return ClaimForwardRequest(
        namespace=_NAMESPACE,
        claimant=claimant,
        task_id=task_id,
        claim={"task_id": task_id, "note": "forwarded work"},
    )


async def test_grants_a_forwarded_claim_for_an_owned_namespace(tmp_path: Path) -> None:
    pin, der = _write_peer_cert(tmp_path)
    hub = _owning_hub(policy=_serving_policy(pin, der), ownership=_owns())
    async with running_hub(hub) as (_, uri):
        result = await _forward(uri, _request())
    assert result.granted is True
    assert result.owner_hub_id == _OWNER
    assert result.grant is not None
    assert result.grant["owner"] == "SYNAPSE-CHANNEL/alice"
    # The lease now authoritatively exists on the owning hub.
    assert hub.state.claims["t1"].owner == "SYNAPSE-CHANNEL/alice"


async def test_relayed_grant_echoes_the_requested_task(tmp_path: Path) -> None:
    pin, der = _write_peer_cert(tmp_path)
    hub = _owning_hub(policy=_serving_policy(pin, der), ownership=_owns())
    async with running_hub(hub) as (_, uri):
        result = await _forward(uri, _request(task_id="build-7"))
    assert result.task_id == "build-7"
    assert result.namespace == _NAMESPACE


def _age(hub: SynapseHub, task_id: str, seconds: float) -> TaskClaim:
    """Move a live lease ``seconds`` into the past, still live, and return it."""
    held = hub.state.claims[task_id]
    aged = replace(
        held,
        claimed_at=held.claimed_at - seconds,
        lease_expires_at=held.lease_expires_at - seconds,
    )
    hub.state.claims[task_id] = aged
    return aged


async def test_a_forwarded_re_claim_renews_the_lease_like_a_direct_one(tmp_path: Path) -> None:
    """A repeat from the lease holder renews it: a fresh epoch and a later expiry, journalled."""
    pin, der = _write_peer_cert(tmp_path)
    journal = EventStore(tmp_path / "owner.db")
    hub = _owning_hub(policy=_serving_policy(pin, der), ownership=_owns(), journal=journal)
    async with running_hub(hub) as (_, uri):
        first = await _forward(uri, _request())
        aged = _age(hub, "t1", 5.0)
        second = await _forward(uri, _request())

    assert first.granted is True
    assert second.granted is True
    assert second.grant is not None
    renewed = hub.state.claims["t1"]
    assert renewed.owner == "SYNAPSE-CHANNEL/alice"
    assert renewed.via_peer == "peer"
    assert renewed.epoch > aged.epoch
    assert renewed.lease_expires_at > aged.lease_expires_at
    assert second.grant["epoch"] == renewed.epoch
    assert hub.counters.claims_granted == 2
    assert len([row for row in journal.read_all() if row.kind == EventKind.CLAIM]) == 2


async def test_a_forwarded_re_claim_is_refused_once_the_peer_loses_its_claim_scope(
    tmp_path: Path,
) -> None:
    """The serving check runs before a renewal, so a peer without the claim scope renews nothing."""
    pin, der = _write_peer_cert(tmp_path)
    hub = _owning_hub(policy=_serving_policy(pin, der), ownership=_owns())
    async with running_hub(hub) as (_, uri):
        first = await _forward(uri, _request())
        held = hub.state.claims["t1"]
        hub.multihub_serving_policy = _serving_policy(pin, der, claim_scope=False)
        second = await _forward(uri, _request())

    assert first.granted is True
    assert second.granted is False
    assert hub.state.claims["t1"] == held
    assert hub.counters.claims_granted == 1


async def test_a_forwarded_re_claim_is_refused_once_another_hub_owns_the_namespace(
    tmp_path: Path,
) -> None:
    """A renewal needs this hub to own the namespace still; a moved namespace renews nothing."""
    pin, der = _write_peer_cert(tmp_path)
    hub = _owning_hub(policy=_serving_policy(pin, der), ownership=_owns())
    async with running_hub(hub) as (_, uri):
        first = await _forward(uri, _request())
        held = hub.state.claims["t1"]
        hub.namespace_ownership = NamespaceOwnership(
            owners={_NAMESPACE: "syn-b"}, local_hub_id=_OWNER
        )
        second = await _forward(uri, _request())

    assert first.granted is True
    assert second.granted is False
    assert hub.state.claims["t1"] == held
    assert hub.counters.claims_granted == 1


async def test_stale_forwarded_claim_retry_reapplies_the_grant(tmp_path: Path) -> None:
    pin, der = _write_peer_cert(tmp_path)
    hub = _owning_hub(policy=_serving_policy(pin, der), ownership=_owns())
    async with running_hub(hub) as (_, uri):
        first = await _forward(uri, _request())
        hub.state.claims["t1"].lease_expires_at = 0.0
        second = await _forward(uri, _request())

    assert first.granted is True
    assert second.granted is True
    assert first.grant is not None
    assert second.grant is not None
    assert second.grant["epoch"] != first.grant["epoch"]
    assert hub.counters.claims_granted == 2


async def test_refuses_a_second_claim_on_a_held_task(tmp_path: Path) -> None:
    pin, der = _write_peer_cert(tmp_path)
    hub = _owning_hub(policy=_serving_policy(pin, der), ownership=_owns())
    async with running_hub(hub) as (_, uri):
        first = await _forward(uri, _request(claimant="SYNAPSE-CHANNEL/alice"))
        second = await _forward(uri, _request(claimant="SYNAPSE-CHANNEL/bob"))
    assert first.granted is True
    assert second.granted is False
    assert second.grant is None
    # The first claimant still holds the lease.
    assert hub.state.claims["t1"].owner == "SYNAPSE-CHANNEL/alice"


async def test_refuses_a_claim_when_no_serving_policy_is_configured() -> None:
    hub = _owning_hub(policy=None, ownership=_owns())
    async with running_hub(hub) as (_, uri):
        result = await _forward(uri, _request())
    assert result.granted is False
    assert result.detail == "peer not authorised to forward claims"
    assert "t1" not in hub.state.claims


async def test_refuses_a_claim_from_an_untrusted_certificate(tmp_path: Path) -> None:
    pin, _trusted = _write_peer_cert(tmp_path)
    _other_pin, stranger_der = _write_peer_cert(tmp_path / "other")
    # The peer is pinned to ``pin`` but the live socket presents a different certificate.
    hub = _owning_hub(policy=_serving_policy(pin, stranger_der), ownership=_owns())
    async with running_hub(hub) as (_, uri):
        result = await _forward(uri, _request())
    assert result.granted is False
    assert result.detail == "peer not authorised to forward claims"


async def test_refuses_a_claim_when_the_hub_governs_no_namespace(tmp_path: Path) -> None:
    pin, der = _write_peer_cert(tmp_path)
    hub = _owning_hub(policy=_serving_policy(pin, der), ownership=None)
    async with running_hub(hub) as (_, uri):
        result = await _forward(uri, _request())
    assert result.granted is False
    assert result.detail == f"this hub does not own namespace {_NAMESPACE!r}"


async def test_refuses_a_claim_for_a_namespace_owned_by_another_hub(tmp_path: Path) -> None:
    pin, der = _write_peer_cert(tmp_path)
    remote = NamespaceOwnership(owners={_NAMESPACE: "syn-elsewhere"}, local_hub_id=_OWNER)
    hub = _owning_hub(policy=_serving_policy(pin, der), ownership=remote)
    async with running_hub(hub) as (_, uri):
        result = await _forward(uri, _request())
    assert result.granted is False
    assert result.detail == f"this hub does not own namespace {_NAMESPACE!r}"


async def test_a_malformed_claim_request_is_answered_with_an_error(tmp_path: Path) -> None:
    pin, der = _write_peer_cert(tmp_path)
    hub = _owning_hub(policy=_serving_policy(pin, der), ownership=_owns())
    async with running_hub(hub) as (_, uri):
        async with await _connect(uri, "peer") as ws:
            # No ``claim`` body: the codec rejects it before any grant is attempted.
            await send_json(
                ws,
                sender="peer",
                type=_RESULT,
                namespace=_NAMESPACE,
                claimant="SYNAPSE-CHANNEL/alice",
                task_id="t1",
            )
            message = await read_until_type(ws, MessageType.ERROR)
    assert "Malformed multi-hub claim request" in message["payload"]
    assert "t1" not in hub.state.claims


async def test_a_hub_needing_journal_recovery_grants_no_forwarded_claim(tmp_path: Path) -> None:
    """A forwarded claim is a mutation: refused while replay has quarantined corrupt rows."""
    pin, der = _write_peer_cert(tmp_path)
    store = _corrupted_store(tmp_path / "owner.db")
    try:
        hub = _owning_hub(policy=_serving_policy(pin, der), ownership=_owns(), journal=store)
        assert hub.journal_corrupt_rows
        async with running_hub(hub) as (_, uri):
            result = await _forward(uri, _request())
        assert result.granted is False
        assert result.detail == "durable journal recovery is required"
        assert "t1" not in hub.state.claims
    finally:
        store.close()


async def test_an_unauthorised_peer_is_not_told_the_recovery_state(tmp_path: Path) -> None:
    """The peer check comes first, so a stranger learns nothing about the journal."""
    pin, der = _write_peer_cert(tmp_path)
    store = _corrupted_store(tmp_path / "owner.db")
    try:
        policy = _serving_policy(pin, der, sender="someone-else")
        hub = _owning_hub(policy=policy, ownership=_owns(), journal=store)
        async with running_hub(hub) as (_, uri):
            result = await _forward(uri, _request())
        assert result.granted is False
        assert result.detail == "peer not authorised to forward claims"
    finally:
        store.close()
