# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — a serving grant proven by an identity key, as behind a TLS-terminating proxy
"""A peer without a client certificate is served only under the identity key its grant names.

The hub runs over plaintext ``ws://`` with the production certificate reader, so no
connection carries a certificate: exactly what a hub behind a TLS-terminating proxy
sees. Its journal holds real events, so a served pull and a refused pull, which the
hub answers with an empty snapshot, are told apart.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from hub_e2e_helpers import running_hub
from synapse_channel.core.federation import (
    AUTHORISED,
    FederationBundle,
    FederationDenyReason,
    FederationPeer,
    ScopeGrant,
)
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.hub_clients import HubClientRegistry
from synapse_channel.core.identity_binding import load_identity_trust_bundle
from synapse_channel.core.identity_keys import generate_signing_key, public_key_b64
from synapse_channel.core.journal import EventKind
from synapse_channel.core.multihub_claim_transport import forward_claim
from synapse_channel.core.multihub_claim_wire import ClaimForwardRequest
from synapse_channel.core.multihub_federation import (
    ACL_DENIED,
    SIGNATURE_UNVERIFIED,
    authorise_multihub_identity_peer,
)
from synapse_channel.core.multihub_serving import (
    MultiHubServingGrant,
    MultiHubServingPolicy,
    no_peer_identity,
)
from synapse_channel.core.multihub_transport import MultiHubFetchError, network_fetcher
from synapse_channel.core.namespace_ownership import NamespaceOwnership
from synapse_channel.core.peer_identity import PeerRegistrationSigner
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.tls import MTLSPeerTrustBundle, MTLSTrustedPeer

DOMAIN = "domain-b"
NAMESPACE = "PROJECT"
SIGNING_KEY = "PROJECT:main:2026-09"
FOLLOWER = "fleet-a"
IDENTITY_KEY = "fleet-a-identity"
OTHER_KEY = "fleet-a-spare"
_PIN = "sha256:" + ("b" * 64)


def _federation(*, namespaces: frozenset[str] = frozenset({NAMESPACE})) -> FederationBundle:
    return FederationBundle(
        [
            FederationPeer(
                domain_id=DOMAIN,
                namespaces=namespaces,
                certificate_pins=frozenset({_PIN}),
                signing_key_ids=frozenset({SIGNING_KEY}),
                scope_grants=(ScopeGrant(verb="read", namespace=NAMESPACE),),
            )
        ]
    )


def _policy(*, identity_key_id: str | None = IDENTITY_KEY) -> MultiHubServingPolicy:
    return MultiHubServingPolicy(
        federation=_federation(),
        mtls=MTLSPeerTrustBundle(
            peers={
                DOMAIN: MTLSTrustedPeer(
                    peer_id=DOMAIN,
                    certificate_pins=frozenset({_PIN}),
                    signing_key_ids=frozenset({SIGNING_KEY}),
                    projects=frozenset({NAMESPACE}),
                )
            }
        ),
        grants={
            FOLLOWER: MultiHubServingGrant(
                domain_id=DOMAIN,
                namespace=NAMESPACE,
                signing_key_id=SIGNING_KEY,
                identity_key_id=identity_key_id,
            )
        },
        clock=lambda: 0.0,
    )


def _trust(tmp_path: Path, keys: list[dict[str, Any]]) -> Any:
    path = tmp_path / "identity-trust.json"
    path.write_text(json.dumps({"keys": keys}), encoding="utf-8")
    return load_identity_trust_bundle(path)


def _signers(tmp_path: Path) -> tuple[PeerRegistrationSigner, PeerRegistrationSigner, Any]:
    granted, spare = generate_signing_key(), generate_signing_key()
    trust = _trust(
        tmp_path,
        [
            {"key_id": IDENTITY_KEY, "public_key": public_key_b64(granted), "senders": [FOLLOWER]},
            {"key_id": OTHER_KEY, "public_key": public_key_b64(spare), "senders": [FOLLOWER]},
        ],
    )
    return (
        PeerRegistrationSigner(granted, IDENTITY_KEY),
        PeerRegistrationSigner(spare, OTHER_KEY),
        trust,
    )


def _seeded(path: Path) -> EventStore:
    store = EventStore(path)
    for index in (1, 2):
        store.append(
            EventKind.LEDGER_PROGRESS,
            {
                "task_id": "T1",
                "author": "PROJECT/seat",
                "kind": "note",
                "text": f"n{index}",
                "posted_at": float(index),
            },
            ts=float(index),
            durable=True,
        )
    return store


async def test_only_the_granted_identity_key_is_served_without_a_certificate(
    tmp_path: Path,
) -> None:
    granted, spare, trust = _signers(tmp_path)
    store = _seeded(tmp_path / "events.db")
    hub = SynapseHub(
        journal=store,
        identity_trust_bundle=trust,
        require_identity_binding=True,
        multihub_serving_policy=_policy(),
    )
    async with running_hub(hub) as (_hub_ref, uri):
        served = await network_fetcher(uri, local_id=FOLLOWER, signer=granted)(0)
        other_key = await network_fetcher(uri, local_id=FOLLOWER, signer=spare)(0)
        with pytest.raises(MultiHubFetchError):
            await network_fetcher(uri, local_id=FOLLOWER)(0)
    store.close()
    assert [event.seq for event in served] == [1, 2]
    assert list(other_key) == []  # admitted by the identity bundle, but not the grant's key


async def test_a_certificate_grant_still_serves_nothing_without_a_certificate(
    tmp_path: Path,
) -> None:
    granted, _spare, trust = _signers(tmp_path)
    store = _seeded(tmp_path / "events.db")
    hub = SynapseHub(
        journal=store,
        identity_trust_bundle=trust,
        require_identity_binding=True,
        multihub_serving_policy=_policy(identity_key_id=None),
    )
    async with running_hub(hub) as (_hub_ref, uri):
        events = await network_fetcher(uri, local_id=FOLLOWER, signer=granted)(0)
    store.close()
    assert list(events) == []


async def test_the_same_identity_grant_proves_a_forwarded_claim(tmp_path: Path) -> None:
    granted, spare, trust = _signers(tmp_path)
    hub = SynapseHub(
        hub_id="syn-owner",
        journal=EventStore(tmp_path / "events.db"),
        identity_trust_bundle=trust,
        require_identity_binding=True,
        multihub_serving_policy=_policy(),
        namespace_ownership=NamespaceOwnership(
            owners={NAMESPACE: "syn-owner"}, local_hub_id="syn-owner"
        ),
    )

    def request(task_id: str) -> ClaimForwardRequest:
        return ClaimForwardRequest(
            namespace=NAMESPACE,
            claimant="PROJECT/alice",
            task_id=task_id,
            claim={"task_id": task_id},
        )

    async with running_hub(hub) as (_hub_ref, uri):
        applied = await forward_claim(request("t1"), uri=uri, local_id=FOLLOWER, signer=granted)
        refused = await forward_claim(request("t2"), uri=uri, local_id=FOLLOWER, signer=spare)
    assert hub.journal is not None
    hub.journal.close()
    assert applied.granted is True
    assert hub.state.claims["t1"].owner == "PROJECT/alice"
    assert refused.granted is False and "t2" not in hub.state.claims


def test_a_hub_refuses_an_identity_grant_it_could_never_satisfy(tmp_path: Path) -> None:
    _granted, _spare, trust = _signers(tmp_path)
    with pytest.raises(ValueError, match="--require-identity-binding"):
        SynapseHub(multihub_serving_policy=_policy(), identity_trust_bundle=trust)
    with pytest.raises(ValueError, match="--require-identity-binding"):
        SynapseHub(multihub_serving_policy=_policy(), require_identity_binding=True)
    public = public_key_b64(generate_signing_key())
    for keys in (
        [{"key_id": "unrelated", "public_key": public, "senders": [FOLLOWER]}],
        [{"key_id": IDENTITY_KEY, "public_key": public, "senders": ["someone-else"]}],
        [{"key_id": IDENTITY_KEY, "public_key": public, "senders": [FOLLOWER], "revoked": True}],
    ):
        with pytest.raises(ValueError, match="is not enrolled, unrevoked, for that sender"):
            SynapseHub(
                multihub_serving_policy=_policy(),
                identity_trust_bundle=_trust(tmp_path, keys),
                require_identity_binding=True,
            )
    SynapseHub(multihub_serving_policy=_policy(identity_key_id=None))  # certificate grants only


def test_a_trust_on_first_use_proof_is_never_recorded(tmp_path: Path) -> None:
    hub = SynapseHub(identity_pin_path=tmp_path / "pins.json")
    frame = {"sender": FOLLOWER, "signature": {"key_id": IDENTITY_KEY}}
    socket = object()
    hub._record_identity_proof(FOLLOWER, frame, socket)
    hub.clients.socket_agent[socket] = FOLLOWER
    assert hub.clients.identity_proof(socket) is None


def test_a_proof_holds_only_while_its_socket_stays_bound_to_the_sender() -> None:
    registry = HubClientRegistry(
        max_clients=4,
        max_unauth_clients=None,
        max_connections_per_host=None,
        takeover_cooldown=0.0,
        clock=lambda: 0.0,
    )
    socket = object()
    registry.add_client(socket)
    registry.record_identity_proof(socket, FOLLOWER, IDENTITY_KEY)
    assert registry.identity_proof(socket) is None  # recorded before the name is bound
    registry.socket_agent[socket] = FOLLOWER
    registry.agent_sockets[FOLLOWER] = socket
    assert registry.identity_proof(socket) == (FOLLOWER, IDENTITY_KEY)
    registry.revoke_name(FOLLOWER)
    assert registry.identity_proof(socket) is None
    registry.socket_agent[socket] = FOLLOWER
    registry.drop_client(socket)
    registry.socket_agent[socket] = FOLLOWER
    assert registry.identity_proof(socket) is None
    assert no_peer_identity(socket) is None


def test_the_identity_composition_keeps_every_other_layer() -> None:
    def decide(**overrides: Any) -> Any:
        arguments: dict[str, Any] = {
            "federation": _federation(),
            "domain_id": DOMAIN,
            "namespace": NAMESPACE,
            "signing_key_id": SIGNING_KEY,
            "now": 0.0,
        }
        arguments.update(overrides)
        return authorise_multihub_identity_peer(**arguments)

    allowed = decide()
    assert allowed.allowed and allowed.reason == AUTHORISED
    assert allowed.scope == (ScopeGrant(verb="read", namespace=NAMESPACE),)
    assert decide(domain_id="unknown").reason == FederationDenyReason.UNKNOWN_DOMAIN
    assert decide(namespace="OTHER").reason == FederationDenyReason.NAMESPACE_NOT_GRANTED
    assert decide(signing_key_id="other").reason == FederationDenyReason.SIGNING_KEY_NOT_ACCEPTED
    assert decide(signature_ok=False).reason == SIGNATURE_UNVERIFIED
    assert decide(acl_ok=False).reason == ACL_DENIED
    # The certificate path is unchanged: a wrong pin is still refused there.
    pinned = _federation().authorise(
        DOMAIN, namespace=NAMESPACE, signing_key_id=SIGNING_KEY, certificate_pin="x", now=0.0
    )
    assert pinned.reason == FederationDenyReason.CERTIFICATE_PIN_NOT_ACCEPTED
