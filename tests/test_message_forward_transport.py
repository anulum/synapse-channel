# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — message-peer configuration and the forwarding network client
"""The forwarding client must tell "try again" from "the peer said no" and trust no reply."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from hub_e2e_helpers import _free_port
from message_forward_peer_helpers import error_frame, result_frame, scripted_peer
from multihub_tls_helpers import certificate_authority, issue_identity
from synapse_channel.core.message_forward_transport import (
    MessageForwardPeer,
    MessageForwardRejectedError,
    MessageForwardTransportError,
    forward_message,
    parse_message_peers,
)
from synapse_channel.core.message_forward_wire import MessageForwardRequest

_PIN = "sha256:" + "ab" * 32


def _request(forward_id: str = "f-1") -> MessageForwardRequest:
    return MessageForwardRequest(
        forward_id=forward_id,
        kind="chat",
        sender_seat="PROJ/alice",
        target_seat="PROJ/bob",
        body={"payload": "hi"},
    )


def test_parse_message_peers_builds_open_and_pinned_routes(tmp_path: Path) -> None:
    """Unpinned routes use the default transport; pinned ones get a pinned connector."""
    open_peers = parse_message_peers(["laptop=ws://laptop:8876"], token="t")
    assert open_peers == {"laptop": MessageForwardPeer(uri="ws://laptop:8876", token="t")}
    assert open_peers["laptop"].connector is None
    ca_key, ca_cert = certificate_authority("transport-test-ca")
    client = issue_identity(tmp_path, "client", ca_key=ca_key, ca_cert=ca_cert)
    pinned = parse_message_peers(
        [" laptop = wss://laptop:8876 "],
        pins={"laptop": _PIN},
        client_certificate_file=str(client.cert),
        client_key_file=str(client.key),
    )
    assert pinned["laptop"].uri == "wss://laptop:8876"
    assert pinned["laptop"].connector is not None


@pytest.mark.parametrize(
    ("values", "kwargs", "message"),
    [
        (["laptop"], {}, "must use HUB_ID=URI"),
        (["=ws://x"], {}, "must use HUB_ID=URI"),
        (["laptop="], {}, "must use HUB_ID=URI"),
        (["bad hub=ws://x"], {}, "not a valid hub id"),
        (["laptop=http://x"], {}, "must be ws:// or wss://"),
        (["laptop=ws://x", "laptop=ws://y"], {}, "names hub 'laptop' twice"),
        (["laptop=ws://x"], {"client_certificate_file": "c.pem"}, "configured together"),
        (
            ["laptop=wss://x"],
            {"client_certificate_file": "c.pem", "client_key_file": "c.key"},
            "requires --message-peer-pin",
        ),
        (["laptop=wss://x"], {"pins": {"other": _PIN}}, "not configured by --message-peer"),
    ],
)
def test_parse_message_peers_refuses_ambiguous_or_weaker_configuration(
    values: list[str], kwargs: dict[str, Any], message: str
) -> None:
    """Every configuration error is reported at startup, before any socket opens."""
    with pytest.raises(ValueError, match=message):
        parse_message_peers(values, **kwargs)


async def test_forward_carries_the_token_and_decodes_a_binary_result() -> None:
    """The token rides the first frame and a binary-framed result is accepted."""
    async with scripted_peer(
        lambda frame: result_frame(frame["forward_id"], result={"seq": 1}).encode()
    ) as peer:
        result = await forward_message(
            _request(), peer=MessageForwardPeer(uri=peer.uri, token="secret"), local_id="edge"
        )
    assert result.disposition == "accepted"
    assert result.result == {"seq": 1}
    assert peer.received[0]["token"] == "secret"
    assert peer.received[0]["sender"] == "edge"
    assert peer.received[0]["type"] == "multihub_message_forward"


async def test_an_error_frame_rejects_the_forward_for_good() -> None:
    """A peer that does not know the frame answers with an error: retrying cannot help."""
    async with scripted_peer(error_frame) as peer:
        with pytest.raises(MessageForwardRejectedError, match="Unknown message type"):
            await forward_message(_request(), peer=MessageForwardPeer(uri=peer.uri), local_id="e")


@pytest.mark.parametrize(
    ("reply", "message"),
    [
        (lambda frame: None, "did not answer"),
        (lambda frame: result_frame("someone-else"), "answered forward 'someone-else'"),
        (lambda frame: json.dumps(["not", "an", "object"]), "not a JSON object"),
        (lambda frame: result_frame(frame["forward_id"], disposition="maybe"), "failed"),
    ],
)
async def test_unanswered_or_untrustworthy_replies_are_transport_failures(
    reply: Any, message: str
) -> None:
    """Silence, a foreign answer or a malformed reply never counts as delivery."""
    async with scripted_peer(reply) as peer:
        with pytest.raises(MessageForwardTransportError, match=message):
            await forward_message(
                _request(), peer=MessageForwardPeer(uri=peer.uri), local_id="edge", timeout=0.3
            )


async def test_frames_before_the_result_are_skipped() -> None:
    """Presence and chat frames a hub fans out on the connection do not end the wait."""
    presence = json.dumps({"type": "presence_update", "payload": "joined"})
    async with scripted_peer(
        lambda frame: [presence, presence, result_frame(frame["forward_id"])]
    ) as peer:
        result = await forward_message(
            _request(), peer=MessageForwardPeer(uri=peer.uri), local_id="edge"
        )
    assert result.forward_id == "f-1"


async def test_an_unreachable_peer_is_a_transport_failure() -> None:
    """A refused connection is retryable, not a verdict."""
    port = _free_port()
    with pytest.raises(MessageForwardTransportError, match="failed"):
        await forward_message(
            _request(), peer=MessageForwardPeer(uri=f"ws://localhost:{port}"), local_id="edge"
        )
