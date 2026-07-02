# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li — the serving half of the federation-bundle exchange, over real sockets

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from websockets.asyncio.client import ClientConnection, connect

from hub_e2e_helpers import read_until_type, running_hub, send_json
from synapse_channel.core.federation import FederationPeer, ScopeGrant
from synapse_channel.core.federation_store import peer_to_dict
from synapse_channel.core.federation_wire import bundle_fingerprint, decode_federation_offer
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.protocol import MessageType

_MATERIAL = FederationPeer(
    domain_id="lab-a",
    namespaces=frozenset({"lab-a/shared"}),
    certificate_pins=frozenset({"sha256:aa"}),
    signing_key_ids=frozenset({"key-1"}),
    scope_grants=(ScopeGrant("read_board", "lab-a/shared"),),
)


def _write_offer(tmp_path: Path, material: FederationPeer = _MATERIAL) -> Path:
    """Write the offered bundle material to a file and return its path."""
    offer = tmp_path / "offer.json"
    offer.write_text(json.dumps(peer_to_dict(material)), encoding="utf-8")
    return offer


async def _connect(uri: str, name: str) -> ClientConnection:
    """Open a raw client socket, drain the welcome, and register with a heartbeat."""
    websocket = await connect(uri)
    await read_until_type(websocket, "welcome")
    await send_json(websocket, sender=name, type="heartbeat")
    return websocket


async def _request_offer(uri: str, *, expect: str) -> dict[str, Any]:
    """Send one federation-offer request and return the reply frame of ``expect`` type."""
    async with await _connect(uri, "peer-ops") as ws:
        await send_json(ws, sender="peer-ops", type=MessageType.FEDERATION_OFFER_REQUEST)
        return await read_until_type(ws, expect)


async def test_serves_the_configured_bundle_material(tmp_path: Path) -> None:
    hub = SynapseHub(hub_id="syn-a", federation_offer_path=_write_offer(tmp_path))
    async with running_hub(hub) as (_, uri):
        frame = await _request_offer(uri, expect=MessageType.FEDERATION_OFFER)
    fetched = decode_federation_offer(frame)
    assert fetched == _MATERIAL
    assert bundle_fingerprint(fetched) == bundle_fingerprint(_MATERIAL)


async def test_a_string_path_is_accepted(tmp_path: Path) -> None:
    hub = SynapseHub(hub_id="syn-a", federation_offer_path=str(_write_offer(tmp_path)))
    async with running_hub(hub) as (_, uri):
        frame = await _request_offer(uri, expect=MessageType.FEDERATION_OFFER)
    assert decode_federation_offer(frame) == _MATERIAL


async def test_an_unconfigured_hub_answers_with_an_error() -> None:
    hub = SynapseHub(hub_id="syn-a")
    async with running_hub(hub) as (_, uri):
        frame = await _request_offer(uri, expect=MessageType.ERROR)
    assert frame["payload"] == "No federation offer is configured on this hub."


async def test_a_missing_offer_file_answers_with_an_error(tmp_path: Path) -> None:
    hub = SynapseHub(hub_id="syn-a", federation_offer_path=tmp_path / "absent.json")
    async with running_hub(hub) as (_, uri):
        frame = await _request_offer(uri, expect=MessageType.ERROR)
    assert frame["payload"] == "The federation offer on this hub is unavailable."


async def test_a_non_json_offer_file_answers_with_an_error(tmp_path: Path) -> None:
    offer = tmp_path / "offer.json"
    offer.write_text("{not json", encoding="utf-8")
    hub = SynapseHub(hub_id="syn-a", federation_offer_path=offer)
    async with running_hub(hub) as (_, uri):
        frame = await _request_offer(uri, expect=MessageType.ERROR)
    assert frame["payload"] == "The federation offer on this hub is unavailable."


async def test_a_malformed_bundle_answers_with_an_error(tmp_path: Path) -> None:
    offer = tmp_path / "offer.json"
    offer.write_text(json.dumps({"namespaces": ["lab-a/shared"]}), encoding="utf-8")
    hub = SynapseHub(hub_id="syn-a", federation_offer_path=offer)
    async with running_hub(hub) as (_, uri):
        frame = await _request_offer(uri, expect=MessageType.ERROR)
    assert frame["payload"] == "The federation offer on this hub is unavailable."


async def test_rotated_material_serves_without_a_restart(tmp_path: Path) -> None:
    offer = _write_offer(tmp_path)
    rotated = FederationPeer(
        domain_id="lab-a",
        signing_key_ids=frozenset({"key-2"}),
        certificate_pins=frozenset({"sha256:bb"}),
    )
    hub = SynapseHub(hub_id="syn-a", federation_offer_path=offer)
    async with running_hub(hub) as (_, uri):
        first = await _request_offer(uri, expect=MessageType.FEDERATION_OFFER)
        offer.write_text(json.dumps(peer_to_dict(rotated)), encoding="utf-8")
        second = await _request_offer(uri, expect=MessageType.FEDERATION_OFFER)
    assert decode_federation_offer(first) == _MATERIAL
    assert decode_federation_offer(second) == rotated
