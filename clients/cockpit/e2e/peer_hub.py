# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — an observed peer hub for the browser gate's peer-scope journey
"""Serve a second real hub that the browser gate's dashboard observes.

The peer-scope journey shows an observed hub as an advisory scope. When it sets
``SYNAPSE_COCKPIT_E2E_OBSERVED_PEER=1``, the harness serves this hub: a real
:class:`~synapse_channel.core.hub.SynapseHub` whose journal holds a small plan
and one claim, and which lets the dashboard's observer identity pull its log.

The browser gate runs on plaintext loopback, so the serving policy is handed the
peer's certificate the way the Python suite hands it
(``tests/hub_e2e_helpers.authorised_multihub_serving_policy``); a production hub
reads it from the live mutual-TLS connection instead.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.x509.oid import NameOID

from synapse_channel.core.federation import FederationBundle, FederationPeer, ScopeGrant
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import record_claim, record_ledger_task
from synapse_channel.core.ledger import LedgerTask
from synapse_channel.core.multihub_serving import MultiHubServingGrant, MultiHubServingPolicy
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.state_models import TaskClaim
from synapse_channel.core.tls import (
    MTLSPeerTrustBundle,
    MTLSTrustedPeer,
    certificate_sha256_pin_from_der,
)
from synapse_channel.observed_peers import ObservedPeerSpec

PEER_HUB_ID = "east-harbour"
"""The observed hub's id, as the scope selector and the band name it."""

PEER_TASKS = (
    ("EAST-1", "Index the east harbour's berths"),
    ("EAST-2", "Publish the east tide table"),
)
"""The observed hub's plan: task id and title."""

PEER_CLAIM = ("east/api", "EAST-1", ("src/berths.py",))
"""The claim the observed hub holds: owner, task id and paths."""

_DOMAIN = "cockpit-e2e-observer-domain"
_NAMESPACE = "SYNAPSE-CHANNEL"
_KEY_ID = "cockpit-e2e-observer-key"


def _certificate_der() -> bytes:
    """Return a fresh self-signed certificate, the peer certificate the policy trusts."""
    key = ed25519.Ed25519PrivateKey.generate()
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "synapse-cockpit-e2e-observer")])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=12))
        .sign(key, algorithm=None)
    )
    return certificate.public_bytes(serialization.Encoding.DER)


def _serving_policy(observer: str) -> MultiHubServingPolicy:
    """Return a serving policy that lets ``observer`` pull the peer hub's log."""
    der = _certificate_der()
    pin = certificate_sha256_pin_from_der(der)
    federation = FederationBundle(
        [
            FederationPeer(
                domain_id=_DOMAIN,
                namespaces=frozenset({_NAMESPACE}),
                certificate_pins=frozenset({pin}),
                signing_key_ids=frozenset({_KEY_ID}),
                scope_grants=(ScopeGrant("read", _NAMESPACE),),
            )
        ]
    )
    trusted = MTLSTrustedPeer(
        peer_id=_DOMAIN,
        certificate_pins=frozenset({pin}),
        signing_key_ids=frozenset({_KEY_ID}),
        projects=frozenset({_NAMESPACE}),
    )
    grant = MultiHubServingGrant(domain_id=_DOMAIN, namespace=_NAMESPACE, signing_key_id=_KEY_ID)
    return MultiHubServingPolicy(
        federation=federation,
        mtls=MTLSPeerTrustBundle(peers={_DOMAIN: trusted}),
        grants={observer: grant},
        clock=lambda: 0.0,
        cert_source=lambda _websocket: der,
    )


def _seed(store: EventStore) -> None:
    """Journal the peer hub's plan and its one claim, as the hub's own handlers write them."""
    now = time.time()
    for task_id, title in PEER_TASKS:
        task = LedgerTask(
            task_id=task_id,
            title=title,
            created_at=now,
            updated_at=now,
            project="east",
            created_by="east/planner",
        )
        record_ledger_task(store, task)
    owner, task_id, paths = PEER_CLAIM
    claim = TaskClaim(
        task_id=task_id,
        owner=owner,
        note="",
        claimed_at=now,
        lease_expires_at=now + 3600.0,
        paths=paths,
    )
    record_claim(store, claim)


@contextlib.asynccontextmanager
async def observed_peer_hub(
    scratch: Path, host: str, observer: str
) -> AsyncIterator[tuple[ObservedPeerSpec, ...]]:
    """Serve the peer hub when the journey asks for one, and yield the dashboard's observed peers.

    Parameters
    ----------
    scratch : pathlib.Path
        The harness's scratch folder; the peer's journal is written there.
    host : str
        The loopback address the peer hub binds.
    observer : str
        The identity the dashboard pulls a peer's log as (``<dashboard name>-observed``).
    """
    if os.environ.get("SYNAPSE_COCKPIT_E2E_OBSERVED_PEER") != "1":
        yield ()
        return
    store = EventStore(scratch / "peer.db")
    _seed(store)
    hub = SynapseHub(
        hub_id=PEER_HUB_ID, journal=store, multihub_serving_policy=_serving_policy(observer)
    )
    task = asyncio.create_task(hub.serve(host, 0))
    try:
        bound_host, port = await hub.wait_until_serving()
        yield (ObservedPeerSpec(PEER_HUB_ID, f"ws://{bound_host}:{port}"),)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        store.close()
