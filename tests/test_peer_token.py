# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — outbound peer tokens read from a file at each connection attempt
"""A replaced token file is sent on the next attempt by every outbound transport."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import ServerConnection, serve

from hub_e2e_helpers import read_until_type, running_hub
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.message_forward_transport import (
    MessageForwardPeer,
    MessageForwardTransportError,
    forward_message,
)
from synapse_channel.core.message_forward_wire import MessageForwardRequest
from synapse_channel.core.multihub_claim_transport import ClaimForwardError, forward_claim
from synapse_channel.core.multihub_claim_wire import ClaimForwardRequest
from synapse_channel.core.multihub_transport import MultiHubFetchError, network_fetcher
from synapse_channel.core.operator_relay_transport import (
    RelayTransportError,
    relay_operator_action,
)
from synapse_channel.core.operator_relay_wire import RelayActionRequest
from synapse_channel.core.peer_token import (
    PeerTokenFile,
    current_peer_token,
    mask_peer_token,
)
from synapse_channel.core.secret_files import SecretFileError


def _write(path: Path, value: str, *, mode: int = 0o600) -> Path:
    """Replace ``path`` with a new inode holding ``value``, as an operator rotation does."""
    staging = path.with_name(path.name + ".new")
    staging.write_text(value + "\n", encoding="utf-8")
    staging.chmod(mode)
    os.replace(staging, path)
    return path


def test_a_token_file_is_read_again_and_its_recent_values_are_masked(tmp_path: Path) -> None:
    token = PeerTokenFile(_write(tmp_path / "peer.token", "first-token"), flag="--x-token-file")
    assert current_peer_token(token) == "first-token"
    _write(tmp_path / "peer.token", "second-token")
    assert current_peer_token(token) == "second-token"
    assert current_peer_token(token) == "second-token"
    text = "peer said first-token then second-token"
    assert mask_peer_token(text, token) == "peer said <token> then <token>"
    assert token.path == tmp_path / "peer.token" and token.flag == "--x-token-file"
    assert "first-token" not in repr(token) and "second-token" not in repr(token)
    same = PeerTokenFile(tmp_path / "peer.token", flag="--x-token-file")
    assert token == same and hash(token) == hash(same)
    assert token != PeerTokenFile(tmp_path / "peer.token", flag="--y-token-file")
    assert token != "first-token"


def test_fixed_and_absent_tokens_are_returned_and_masked_as_given() -> None:
    assert current_peer_token("fixed") == "fixed" and current_peer_token(None) is None
    assert mask_peer_token("echo fixed", "fixed") == "echo <token>"
    assert mask_peer_token("echo fixed", None) == "echo fixed"


@pytest.mark.parametrize("fault", ["missing", "shared", "empty"])
def test_an_unusable_token_file_is_refused_naming_its_option(tmp_path: Path, fault: str) -> None:
    path = tmp_path / "peer.token"
    if fault == "shared":
        _write(path, "secret-value", mode=0o644)
    elif fault == "empty":
        _write(path, "")
    with pytest.raises(SecretFileError, match="--claim-peer-token-file") as error:
        PeerTokenFile(path, flag="--claim-peer-token-file").read()
    assert "secret-value" not in str(error.value)


_Attempt = Callable[[str, PeerTokenFile], Awaitable[object]]


async def _claim(uri: str, token: PeerTokenFile) -> object:
    request = ClaimForwardRequest(
        namespace="PROJ", claimant="PROJ/alice", task_id="t1", claim={"task_id": "t1"}
    )
    return await forward_claim(request, uri=uri, local_id="origin", token=token, timeout=2)


async def _relay(uri: str, token: PeerTokenFile) -> object:
    request = RelayActionRequest(
        action="release",
        namespace="PROJ",
        task_id="t1",
        operator="ops",
        origin_hub_id="origin",
        reason="rotation",
        idem_key="relay-1",
    )
    return await relay_operator_action(request, uri=uri, local_id="origin", token=token, timeout=2)


async def _message(uri: str, token: PeerTokenFile) -> object:
    request = MessageForwardRequest(
        forward_id="f1", kind="chat", sender_seat="PROJ/alice", target_seat="PROJ/bob", body={}
    )
    peer = MessageForwardPeer(uri, token=token)
    return await forward_message(request, peer=peer, local_id="origin", timeout=2)


async def _watch(uri: str, token: PeerTokenFile) -> object:
    return await network_fetcher(uri, local_id="origin", token=token, timeout=2)(0)


_KINDS: dict[str, tuple[_Attempt, type[Exception]]] = {
    "claim": (_claim, ClaimForwardError),
    "relay": (_relay, RelayTransportError),
    "message": (_message, MessageForwardTransportError),
    "watch": (_watch, MultiHubFetchError),
}


@pytest.mark.parametrize("kind", sorted(_KINDS))
async def test_each_transport_sends_the_file_token_current_at_each_attempt(
    tmp_path: Path, kind: str
) -> None:
    attempt, failure = _KINDS[kind]
    seen: list[object] = []

    async def peer(socket: ServerConnection) -> None:
        seen.append(json.loads(await asyncio.wait_for(socket.recv(), 3)).get("token"))
        await socket.close(code=1011, reason="test peer records only")

    path = _write(tmp_path / "peer.token", "token-before-rotation")
    token = PeerTokenFile(path, flag=f"--{kind}-token-file")
    async with serve(peer, "127.0.0.1", 0) as server:
        uri = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"
        with pytest.raises(failure):
            await attempt(uri, token)
        _write(path, "token-after-rotation")
        with pytest.raises(failure):
            await attempt(uri, token)
    assert seen == ["token-before-rotation", "token-after-rotation"]


@pytest.mark.parametrize("kind", sorted(_KINDS))
async def test_an_unreadable_token_file_fails_the_attempt_before_connecting(
    tmp_path: Path, kind: str
) -> None:
    attempt, failure = _KINDS[kind]
    connected: list[str] = []

    async def peer(socket: ServerConnection) -> None:
        connected.append("connected")

    path = _write(tmp_path / "peer.token", "leaked-if-read", mode=0o644)
    async with serve(peer, "127.0.0.1", 0) as server:
        uri = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"
        with pytest.raises(failure, match="token unavailable") as error:
            await attempt(uri, PeerTokenFile(path, flag="--peer-token-file"))
    assert connected == [] and "leaked-if-read" not in str(error.value)


async def test_the_hub_masks_every_recent_file_token_a_peer_echoes(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = _write(tmp_path / "peer.token", "a" * 24)

    async def peer(socket: ServerConnection) -> None:
        sent = json.loads(await asyncio.wait_for(socket.recv(), 3))["token"]
        await socket.send(json.dumps({"type": "error", "payload": f"echo {sent}"}))

    async with serve(peer, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        hub = SynapseHub(
            hub_id="origin",
            message_peers={
                "peer": MessageForwardPeer(
                    f"ws://127.0.0.1:{port}",
                    token=PeerTokenFile(path, flag="--message-peer-token-file"),
                )
            },
        )
        async with running_hub(hub) as (_, uri), connect(uri) as sender:
            await read_until_type(sender, "welcome")
            await sender.send(
                json.dumps({"sender": "PROJ/sender", "type": "heartbeat", "protocol_version": 3})
            )
            for rotated in (False, True):
                if rotated:
                    _write(path, "b" * 24)
                await sender.send(
                    json.dumps({"sender": "PROJ/sender", "type": "who_request", "hub": "peer"})
                )
                await read_until_type(sender, "error")
    records = [r.getMessage() for r in caplog.records if r.name == "synapse.message_forward"]
    assert len(records) == 2 and all("echo <token>" in message for message in records)
    assert "a" * 24 not in caplog.text and "b" * 24 not in caplog.text
