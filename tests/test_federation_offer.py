# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — dedicated unit tests for the federation-offer serving handler

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from synapse_channel.core.federation import FederationPeer, ScopeGrant
from synapse_channel.core.federation_store import peer_to_dict
from synapse_channel.core.federation_wire import encode_federation_offer
from synapse_channel.core.handlers import federation_offer as fed_offer
from synapse_channel.core.protocol import MessageType

if TYPE_CHECKING:
    from synapse_channel.core.hub import SynapseHub

_MATERIAL = FederationPeer(
    domain_id="lab-a",
    namespaces=frozenset({"lab-a/shared"}),
    certificate_pins=frozenset({"sha256:aa"}),
    signing_key_ids=frozenset({"key-1"}),
    scope_grants=(ScopeGrant("read_board", "lab-a/shared"),),
)


class _FakeHub:
    """A minimal SynapseHub stand-in exposing the offer path and transport."""

    def __init__(self, federation_offer_path: Any) -> None:
        self.federation_offer_path = federation_offer_path
        self.sent: list[dict[str, Any]] = []

    def _system(self, text: str, **fields: Any) -> dict[str, Any]:
        return {"text": text, **fields}

    async def _send_json(self, websocket: Any, payload: dict[str, Any]) -> None:
        self.sent.append(payload)


def _as_hub(hub: _FakeHub) -> SynapseHub:
    """Present the structural fake as a concrete hub without a type: ignore."""
    return cast("SynapseHub", hub)


def _write_offer(tmp_path: Path) -> Path:
    offer = tmp_path / "offer.json"
    offer.write_text(json.dumps(peer_to_dict(_MATERIAL)), encoding="utf-8")
    return offer


class TestReadOffer:
    """Cover the per-request bundle loader."""

    def test_valid_bundle_round_trips(self, tmp_path: Path) -> None:
        peer = fed_offer._read_offer(_write_offer(tmp_path))
        assert peer.domain_id == "lab-a"
        assert peer.namespaces == frozenset({"lab-a/shared"})


class TestHandleFederationOfferRequest:
    """Cover the three serving branches of the offer request handler."""

    async def test_serves_the_configured_bundle(self, tmp_path: Path) -> None:
        hub = _FakeHub(_write_offer(tmp_path))
        await fed_offer.handle_federation_offer_request(_as_hub(hub), "peer-op", {}, object())
        payload = hub.sent[0]
        assert payload["msg_type"] == MessageType.FEDERATION_OFFER
        assert payload["target"] == "peer-op"
        assert payload["text"] == "Federation-bundle offer"
        expected = encode_federation_offer(fed_offer._read_offer(_write_offer(tmp_path)))
        for key, value in expected.items():
            assert payload[key] == value

    async def test_no_configured_offer_answers_with_error(self) -> None:
        hub = _FakeHub(None)
        await fed_offer.handle_federation_offer_request(_as_hub(hub), "peer-op", {}, object())
        payload = hub.sent[0]
        assert payload["msg_type"] == MessageType.ERROR
        assert payload["target"] == "peer-op"
        assert "No federation offer is configured" in payload["text"]

    async def test_missing_file_answers_with_generic_error(self, tmp_path: Path) -> None:
        hub = _FakeHub(tmp_path / "absent.json")
        await fed_offer.handle_federation_offer_request(_as_hub(hub), "peer-op", {}, object())
        payload = hub.sent[0]
        assert payload["msg_type"] == MessageType.ERROR
        assert "unavailable" in payload["text"]

    async def test_invalid_json_answers_with_generic_error(self, tmp_path: Path) -> None:
        bad = tmp_path / "offer.json"
        bad.write_text("{not json", encoding="utf-8")
        hub = _FakeHub(bad)
        await fed_offer.handle_federation_offer_request(_as_hub(hub), "peer-op", {}, object())
        assert hub.sent[0]["msg_type"] == MessageType.ERROR
        assert "unavailable" in hub.sent[0]["text"]

    async def test_malformed_bundle_answers_with_generic_error(self, tmp_path: Path) -> None:
        # A well-formed JSON object that is not a valid bundle (no domain id).
        malformed = tmp_path / "offer.json"
        malformed.write_text(json.dumps({"namespaces": ["lab-a/shared"]}), encoding="utf-8")
        hub = _FakeHub(malformed)
        await fed_offer.handle_federation_offer_request(_as_hub(hub), "peer-op", {}, object())
        assert hub.sent[0]["msg_type"] == MessageType.ERROR
        assert "unavailable" in hub.sent[0]["text"]


class TestSendError:
    """Cover the private error frame helper."""

    async def test_error_frame_is_addressed_to_the_requester(self) -> None:
        hub = _FakeHub(None)
        await fed_offer._send_error(_as_hub(hub), "peer-op", object(), "boom")
        payload = hub.sent[0]
        assert payload["msg_type"] == MessageType.ERROR
        assert payload["target"] == "peer-op"
        assert payload["text"] == "boom"
