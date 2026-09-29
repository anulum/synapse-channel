# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — a peer hub proves its id with a registration signature
"""A hub-to-hub frame signed by an enrolled key passes an identity-bound hub without mTLS.

Behind a TLS-terminating proxy a peer hub's client certificate never reaches
the hub, so only a registration signature can prove its id. The pull tests run
the real multi-hub fetch against a real hub that requires identity binding. The
forwarding transports are checked on the real wire: each frame a real
websocket server receives verifies against the trust bundle.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from websockets.asyncio.server import ServerConnection, serve

from cli_processes_helpers import _hub_ns
from cli_processes_hub_helpers import _close_runner
from hub_e2e_helpers import running_hub
from synapse_channel import cli_processes
from synapse_channel.cli import build_parser
from synapse_channel.core.dead_letter_forwarding_transport import forward_dead_letter
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.identity_binding import load_identity_trust_bundle, verify_registration
from synapse_channel.core.identity_keys import (
    IdentityKeyError,
    generate_signing_key,
    public_key_b64,
    write_signing_key,
)
from synapse_channel.core.message_auth import SignedEventVerificationResult
from synapse_channel.core.message_forward_transport import MessageForwardPeer, forward_message
from synapse_channel.core.message_forward_wire import MessageForwardRequest
from synapse_channel.core.multihub_claim_transport import forward_claim
from synapse_channel.core.multihub_claim_wire import ClaimForwardRequest
from synapse_channel.core.multihub_transport import MultiHubFetchError, network_fetcher
from synapse_channel.core.multihub_watch import MultiHubWatch
from synapse_channel.core.operator_relay_transport import relay_operator_action
from synapse_channel.core.operator_relay_wire import RelayActionRequest
from synapse_channel.core.peer_identity import (
    PeerRegistrationSigner,
    load_peer_registration_signer,
    signed,
)

PEER = "hub-a"


def _signer(tmp_path: Path, label: str) -> tuple[PeerRegistrationSigner, str]:
    key = generate_signing_key()
    path = tmp_path / f"{label}.pem"
    write_signing_key(path, key)
    return load_peer_registration_signer(path, f"{label}-key"), public_key_b64(key)


def _trust(tmp_path: Path, key_id: str, public_key: str) -> Any:
    path = tmp_path / "identity-trust.json"
    path.write_text(
        json.dumps({"keys": [{"key_id": key_id, "public_key": public_key, "senders": [PEER]}]}),
        encoding="utf-8",
    )
    return load_identity_trust_bundle(path)


def test_the_signer_needs_a_key_id_and_keeps_rising(tmp_path: Path) -> None:
    signer, public = _signer(tmp_path, "a")
    trust = _trust(tmp_path, signer.key_id, public)
    with pytest.raises(ValueError, match="non-empty key id"):
        load_peer_registration_signer(tmp_path / "a.pem", "  ")
    with pytest.raises(IdentityKeyError):
        load_peer_registration_signer(tmp_path / "missing.pem", "k")
    frame = {"type": "t", "sender": PEER}
    assert signed(frame, None) is frame
    first = signer.sign(dict(frame))
    second = signer.sign(dict(frame))
    for proof in (first, second):  # a fresh nonce and sequence each time, both valid
        assert (
            verify_registration(proof, trust_bundle=trust, now=time.time(), required_sender=PEER)
            is SignedEventVerificationResult.VALID
        )
    assert first != second


async def test_an_enrolled_signature_admits_the_pull_and_nothing_else_does(
    tmp_path: Path,
) -> None:
    signer, public = _signer(tmp_path, "a")
    stranger, _ = _signer(tmp_path, "b")
    hub = SynapseHub(
        identity_trust_bundle=_trust(tmp_path, signer.key_id, public),
        require_identity_binding=True,
    )
    async with running_hub(hub) as (_hub_ref, uri):
        events = await network_fetcher(uri, local_id=PEER, signer=signer)(0)
        with pytest.raises(MultiHubFetchError):
            await network_fetcher(uri, local_id=PEER)(0)
        with pytest.raises(MultiHubFetchError):
            await network_fetcher(uri, local_id=PEER, signer=stranger)(0)
    assert list(events) == []


@contextlib.asynccontextmanager
async def _capturing_server() -> AsyncIterator[tuple[str, list[dict[str, Any]]]]:
    """A real websocket server that records each first frame, then hangs up."""
    frames: list[dict[str, Any]] = []

    async def handler(connection: ServerConnection) -> None:
        frames.append(json.loads(await connection.recv()))
        await connection.close()

    async with serve(handler, "127.0.0.1", 0) as server:
        port = next(iter(server.sockets)).getsockname()[1]
        yield f"ws://127.0.0.1:{port}", frames


async def test_every_forwarding_transport_signs_its_frame(tmp_path: Path) -> None:
    signer, public = _signer(tmp_path, "a")
    trust = _trust(tmp_path, signer.key_id, public)
    async with _capturing_server() as (uri, frames):
        with contextlib.suppress(Exception):
            await forward_claim(
                ClaimForwardRequest(namespace="P", claimant="P/x", task_id="T", claim={}),
                uri=uri,
                local_id=PEER,
                signer=signer,
                timeout=2.0,
            )
        with contextlib.suppress(Exception):
            await relay_operator_action(
                RelayActionRequest(
                    action="release",
                    namespace="P",
                    task_id="T",
                    operator="P/op",
                    origin_hub_id=PEER,
                ),
                uri=uri,
                local_id=PEER,
                signer=signer,
                timeout=2.0,
            )
        with contextlib.suppress(Exception):
            await forward_dead_letter(
                {"target": "P/x", "count": 1, "origin_hub_id": PEER, "owner_hub_id": "hub-b"},
                uri=uri,
                local_id=PEER,
                signer=signer,
                timeout=2.0,
            )
        with contextlib.suppress(Exception):
            await forward_message(
                MessageForwardRequest(forward_id="f1", kind="who", sender_seat="P/x"),
                peer=MessageForwardPeer(uri=uri, signer=signer),
                local_id=PEER,
                timeout=2.0,
            )
        deadline = asyncio.get_running_loop().time() + 3.0
        while len(frames) < 4 and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)
    assert len(frames) == 4
    for frame in frames:
        assert (
            verify_registration(frame, trust_bundle=trust, now=time.time(), required_sender=PEER)
            is SignedEventVerificationResult.VALID
        ), frame["type"]


async def test_the_watch_polls_an_identity_bound_peer_only_when_signing(tmp_path: Path) -> None:
    signer, public = _signer(tmp_path, "a")
    hub = SynapseHub(
        identity_trust_bundle=_trust(tmp_path, signer.key_id, public),
        require_identity_binding=True,
    )
    async with running_hub(hub) as (_hub_ref, uri):
        signing = MultiHubWatch({"hub-b": uri}, local_id=PEER, signer=signer)
        unsigned = MultiHubWatch({"hub-b": uri}, local_id=PEER)
        assert await signing.poll_once() == {"hub-b": None}
        failed = await unsigned.poll_once()
    assert failed["hub-b"] is not None


def test_the_hub_flags_load_the_key_and_reach_every_peer_map(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    key = generate_signing_key()
    write_signing_key(tmp_path / "hub.pem", key)
    captured: dict[str, Any] = {}

    def build_hub(**kwargs: Any) -> SynapseHub:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    args = build_parser().parse_args(
        ["hub", "--peer-identity-key", str(tmp_path / "hub.pem"), "--peer-identity-key-id", "k"]
    )
    namespace = _hub_ns(
        peer_identity_key=args.peer_identity_key,
        peer_identity_key_id=args.peer_identity_key_id,
        hub_id=PEER,
        namespace_owner=["OWNED=hub-b"],
        claim_peer=["hub-b=ws://127.0.0.1:1"],
        relay_peer=["hub-b=ws://127.0.0.1:1"],
        message_peer=["hub-b=ws://127.0.0.1:1"],
    )
    assert cli_processes._cmd_hub(namespace, runner=_close_runner, hub_factory=build_hub) == 0
    signers = {
        name: captured[name]["hub-b"].signer
        for name in ("claim_peers", "relay_peers", "message_peers")
    }
    assert {signer.key_id for signer in signers.values()} == {"k"}
    assert len({id(signer) for signer in signers.values()}) == 1  # one signer for every map

    half = _hub_ns(peer_identity_key=str(tmp_path / "hub.pem"))
    assert cli_processes._cmd_hub(half, runner=_close_runner) == 2
    assert "must be configured together" in capsys.readouterr().err
    missing = _hub_ns(peer_identity_key=str(tmp_path / "absent.pem"), peer_identity_key_id="k")
    assert cli_processes._cmd_hub(missing, runner=_close_runner) == 2
    assert "--peer-identity-key:" in capsys.readouterr().err
