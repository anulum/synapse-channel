# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li — serving half of cross-hub dead-letter forwarding (the receiver)

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from websockets.asyncio.client import ClientConnection, connect

from hub_e2e_helpers import read_until_type, running_hub, send_json
from synapse_channel.core.dead_letter_forwarding import FORWARDING_FIELD, forwarding_notice
from synapse_channel.core.dead_letter_forwarding_transport import forward_dead_letter
from synapse_channel.core.federation import FederationBundle, FederationPeer, ScopeGrant
from synapse_channel.core.handlers.dead_letter_forwarding import handle_dead_letter_forwarding
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind
from synapse_channel.core.multihub_serving import MultiHubServingGrant, MultiHubServingPolicy
from synapse_channel.core.namespace_ownership import NamespaceOwnership
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType
from synapse_channel.core.tls import MTLSPeerTrustBundle, MTLSTrustedPeer, certificate_sha256_pin

_TYPE = MessageType.DEAD_LETTER_FORWARDING
_NAMESPACE = "OWNED"
_TARGET = "OWNED/reader"
_OWNER = "syn-owner"
_ORIGIN = "syn-edge"
_DOMAIN = "domain-edge"
_KEY = "OWNED:main:2026-06"


def _pointer(target: str = _TARGET, count: int = 2) -> dict[str, Any]:
    """The honesty-bound pointer an origin hub forwards for a blackholed target."""
    return forwarding_notice(target, count, origin_hub_id=_ORIGIN, owner_hub_id=_OWNER)


# --- serving over real sockets -----------------------------------------------------------


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
            "/CN=peer-edge",
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


def _serving_policy(pin: str, der: bytes, *, sender: str = "peer") -> MultiHubServingPolicy:
    """Build a serving policy trusting ``sender`` as the forwarding peer for the namespace."""
    return MultiHubServingPolicy(
        federation=FederationBundle(
            [
                FederationPeer(
                    domain_id=_DOMAIN,
                    namespaces=frozenset({_NAMESPACE}),
                    certificate_pins=frozenset({pin}),
                    signing_key_ids=frozenset({_KEY}),
                    scope_grants=(ScopeGrant(verb="release", namespace=_NAMESPACE),),
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


def _owns() -> NamespaceOwnership:
    """An ownership map under which the owning hub authoritatively owns the namespace."""
    return NamespaceOwnership(owners={_NAMESPACE: _OWNER}, local_hub_id=_OWNER)


async def _connect(uri: str, name: str) -> ClientConnection:
    """Open a raw client socket, drain the welcome, and register with a heartbeat."""
    websocket = await connect(uri)
    await read_until_type(websocket, "welcome")
    await send_json(websocket, sender=name, type="heartbeat")
    return websocket


async def test_an_authorised_forwarding_is_recorded_and_broadcast(tmp_path: Path) -> None:
    pin, der = _write_peer_cert(tmp_path)
    store = EventStore(tmp_path / "events.db")
    hub = SynapseHub(
        hub_id=_OWNER,
        multihub_serving_policy=_serving_policy(pin, der),
        namespace_ownership=_owns(),
        journal=store,
    )
    async with running_hub(hub) as (_, uri):
        async with await _connect(uri, "watcher") as watcher:
            async with await _connect(uri, "peer") as peer:
                await send_json(peer, sender="peer", type=_TYPE, **{FORWARDING_FIELD: _pointer()})
                broadcast = await read_until_type(watcher, _TYPE)
    # The owning hub's operators learn of the gap the peer reported.
    assert broadcast["forwarding_target"] == _TARGET
    assert broadcast["forwarding_count"] == 2
    assert broadcast["origin_hub_id"] == _ORIGIN
    assert _TARGET in broadcast["payload"]
    # The inbound side of the two-hub audit trail: direction 'in', naming the verified peer.
    audit = [e.payload for e in store.read_all() if e.kind == EventKind.DEAD_LETTER_FORWARDING]
    store.close()
    assert audit == [
        {
            "target": _TARGET,
            "count": 2,
            "origin_hub_id": _ORIGIN,
            "owner_hub_id": _OWNER,
            "direction": "in",
            "peer": "peer",
        }
    ]


async def test_the_real_transport_reaches_a_running_owner_hub(tmp_path: Path) -> None:
    # The default connector over a real socket: forward_dead_letter carries the pointer to a
    # running owner hub, which authorises, journals, and broadcasts it — proving the whole wire path
    # end to end without a mutual-TLS handshake.
    pin, der = _write_peer_cert(tmp_path)
    store = EventStore(tmp_path / "events.db")
    hub = SynapseHub(
        hub_id=_OWNER,
        multihub_serving_policy=_serving_policy(pin, der, sender=_ORIGIN),
        namespace_ownership=_owns(),
        journal=store,
    )
    async with running_hub(hub) as (_, uri):
        async with await _connect(uri, "watcher") as watcher:
            await forward_dead_letter(
                _pointer(), uri=uri, local_id=_ORIGIN
            )  # real default connector
            broadcast = await read_until_type(watcher, _TYPE)
    assert broadcast["forwarding_target"] == _TARGET
    assert broadcast["origin_hub_id"] == _ORIGIN
    audit = [e.payload for e in store.read_all() if e.kind == EventKind.DEAD_LETTER_FORWARDING]
    store.close()
    assert audit[0]["direction"] == "in"
    assert audit[0]["peer"] == _ORIGIN


async def test_a_forwarding_from_an_unauthorised_peer_is_dropped(tmp_path: Path) -> None:
    pin, der = _write_peer_cert(tmp_path)
    store = EventStore(tmp_path / "events.db")
    # The policy trusts "peer"; an unknown sender is not authorised to forward.
    hub = SynapseHub(
        hub_id=_OWNER,
        multihub_serving_policy=_serving_policy(pin, der, sender="peer"),
        namespace_ownership=_owns(),
        journal=store,
    )
    async with running_hub(hub) as (_, uri):
        async with await _connect(uri, "watcher") as watcher:
            async with await _connect(uri, "impostor") as impostor:
                await send_json(
                    impostor, sender="impostor", type=_TYPE, **{FORWARDING_FIELD: _pointer()}
                )
                # A benign chat after the dropped forward is the sync point: once it is broadcast,
                # the forward ahead of it on the same socket has already been handled (and dropped).
                await send_json(impostor, sender="impostor", type="chat", payload="ping")
                await read_until_type(watcher, "chat")
    assert [e for e in store.read_all() if e.kind == EventKind.DEAD_LETTER_FORWARDING] == []
    store.close()


# --- fine-grained gate branches, exercised directly --------------------------------------


class _Policy:
    """A stand-in serving policy that authorises (or refuses) every peer uniformly."""

    def __init__(self, *, allowed: bool) -> None:
        self._allowed = allowed

    def authorise(self, *, sender: str, websocket: Any) -> Any:
        return SimpleNamespace(allowed=self._allowed)


def _frame(pointer: dict[str, Any] | None = None) -> dict[str, Any]:
    body = _pointer() if pointer is None else pointer
    return {"type": _TYPE, "sender": "peer", FORWARDING_FIELD: body}


def _recording_hub(
    *,
    policy: Any,
    ownership: NamespaceOwnership | None,
    journal: EventStore | None = None,
) -> tuple[SynapseHub, list[dict[str, Any]]]:
    """Return a hub whose broadcasts are captured instead of sent, and the capture list."""
    hub = SynapseHub(
        hub_id=_OWNER,
        multihub_serving_policy=policy,
        namespace_ownership=ownership,
        journal=journal,
    )
    broadcasts: list[dict[str, Any]] = []

    async def _capture(message: dict[str, Any]) -> None:
        broadcasts.append(message)

    hub._broadcast = _capture  # type: ignore[method-assign]
    return hub, broadcasts


async def test_a_hub_with_no_serving_policy_drops_the_forwarding(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    hub, broadcasts = _recording_hub(policy=None, ownership=_owns(), journal=store)
    await handle_dead_letter_forwarding(hub, "peer", _frame(), websocket=object())
    assert broadcasts == []
    assert [e for e in store.read_all() if e.kind == EventKind.DEAD_LETTER_FORWARDING] == []
    store.close()


async def test_a_refused_peer_is_dropped() -> None:
    hub, broadcasts = _recording_hub(policy=_Policy(allowed=False), ownership=_owns())
    await handle_dead_letter_forwarding(hub, "peer", _frame(), websocket=object())
    assert broadcasts == []


async def test_a_pointer_for_an_unowned_namespace_is_dropped() -> None:
    # The peer is authorised, but this hub is not the owner of the target's namespace.
    elsewhere = NamespaceOwnership(owners={_NAMESPACE: "another-hub"}, local_hub_id=_OWNER)
    hub, broadcasts = _recording_hub(policy=_Policy(allowed=True), ownership=elsewhere)
    await handle_dead_letter_forwarding(hub, "peer", _frame(), websocket=object())
    assert broadcasts == []


async def test_a_forwarding_with_no_ownership_map_is_dropped() -> None:
    hub, broadcasts = _recording_hub(policy=_Policy(allowed=True), ownership=None)
    await handle_dead_letter_forwarding(hub, "peer", _frame(), websocket=object())
    assert broadcasts == []


async def test_a_malformed_frame_is_dropped() -> None:
    hub, broadcasts = _recording_hub(policy=_Policy(allowed=True), ownership=_owns())
    await handle_dead_letter_forwarding(hub, "peer", {"type": _TYPE}, websocket=object())
    assert broadcasts == []


async def test_without_a_journal_the_operators_are_still_told() -> None:
    # No durable log to write the inbound audit to, but the owning hub still tells its operators.
    hub, broadcasts = _recording_hub(policy=_Policy(allowed=True), ownership=_owns(), journal=None)
    assert hub.journal is None
    await handle_dead_letter_forwarding(hub, "peer", _frame(), websocket=object())
    assert len(broadcasts) == 1
    assert broadcasts[0]["forwarding_target"] == _TARGET
