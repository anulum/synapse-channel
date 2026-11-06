# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — a hub that requires every state-changing frame to be signed
"""Exercise --require-signed-frames on a real hub over real sockets."""

from __future__ import annotations

import base64
import json
import sys
import time
import uuid
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

from cli_processes_helpers import _hub_ns
from hub_e2e_helpers import read_json, read_until_type, running_hub
from synapse_channel import cli_processes
from synapse_channel.core.frame_signature import sign_session_frame
from synapse_channel.core.frame_signature_gate import (
    BAD_SIGNATURE_LIMIT,
    NO_IDENTITY,
    READ_ONLY_TYPES,
    FrameSignatureGate,
    GateVerdict,
    bundle_key,
    pin_key,
)
from synapse_channel.core.frame_signing import FrameSigningSessions
from synapse_channel.core.handlers import VERBS
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.identity_keys import sign_registration
from synapse_channel.core.identity_pins import IdentityPinStore
from synapse_channel.core.message_auth import (
    EventSignatureKey,
    EventSignatureTrustBundle,
    MessageReplayCache,
    sign_event_frame,
)

SENDER = "proj/claude"
KEY = Ed25519PrivateKey.generate()


def _bundle(*, revoked: bool = False) -> EventSignatureTrustBundle:
    key = EventSignatureKey.from_private_key(
        key_id="k", private_key=KEY, senders=frozenset({SENDER}), revoked=revoked
    )
    return EventSignatureTrustBundle(
        keys={"k": key}, replay_cache=MessageReplayCache(window_seconds=30.0, max_entries=64)
    )


def _hub(*, required: bool = True) -> SynapseHub:
    return SynapseHub(
        hub_id="fsg",
        identity_trust_bundle=_bundle(),
        require_identity_binding=True,
        require_signed_frames=required,
    )


class _Session:
    """A raw client that registers with the bundle key and signs what it sends."""

    def __init__(self, websocket: ClientConnection, welcome: dict[str, Any]) -> None:
        self.websocket = websocket
        self.nonce = str(welcome["session_nonce"])
        self.hub_id = str(welcome["hub_id"])
        self.seq = 0

    def signed(self, **frame: Any) -> dict[str, Any]:
        self.seq += 1
        return sign_session_frame(
            {"sender": SENDER, **frame},
            key_id="k",
            private_key=KEY,
            hub_id=self.hub_id,
            session_nonce=self.nonce,
            seq=self.seq,
        )

    async def send(self, frame: dict[str, Any]) -> None:
        await self.websocket.send(json.dumps(frame))


async def _register(websocket: ClientConnection) -> _Session:
    welcome = await read_until_type(websocket, "welcome")
    registration = sign_event_frame(
        {"sender": SENDER, "type": "heartbeat", "target": "System", "payload": "online"},
        key_id="k",
        private_key=KEY,
        nonce=uuid.uuid4().hex,
        sequence=1,
        signed_at=time.time(),
    )
    await websocket.send(json.dumps(registration))
    session = _Session(websocket, welcome)
    await session.send({"sender": SENDER, "type": "who_request"})
    await read_until_type(websocket, "who_snapshot")
    return session


def _claim(task: str) -> dict[str, Any]:
    return {"type": "claim", "target": "System", "task_id": task}


async def test_a_signed_mutation_is_admitted_and_an_unsigned_one_refused() -> None:
    hub = _hub()
    async with running_hub(hub) as (_, uri):
        async with connect(uri) as websocket:
            session = await _register(websocket)
            await session.send(session.signed(**_claim("signed")))
            await read_until_type(websocket, "claim_granted")
            await session.send({"sender": SENDER, **_claim("unsigned")})
            refused = await read_until_type(websocket, "error")
    assert "signed" in hub.state.claims and "unsigned" not in hub.state.claims
    assert refused["verification_result"] == "frame_signature_required"
    assert hub.counters.auth_failures == 1


async def test_replays_copies_and_unknown_types_are_refused() -> None:
    hub = _hub()
    async with running_hub(hub) as (_, uri):
        async with connect(uri) as websocket:
            session = await _register(websocket)
            frame = session.signed(**_claim("once"))
            await session.send(frame)
            await read_until_type(websocket, "claim_granted")
            await session.send(frame)
            replayed = await read_until_type(websocket, "error")
            foreign = sign_session_frame(
                {"sender": SENDER, **_claim("copied")},
                key_id="k",
                private_key=KEY,
                hub_id=session.hub_id,
                session_nonce="another-connection",
                seq=99,
            )
            await session.send(foreign)
            copied = await read_until_type(websocket, "error")
            await session.send({"sender": SENDER, "type": "made_up_verb"})
            unknown = await read_until_type(websocket, "error")
    assert replayed["verification_result"] == "frame_signature_replayed"
    assert copied["verification_result"] == "frame_signature_wrong_session"
    assert unknown["verification_result"] == "frame_signature_required"
    assert "copied" not in hub.state.claims


async def test_repeated_bad_frames_close_the_connection() -> None:
    hub = _hub()
    async with running_hub(hub) as (_, uri):
        async with connect(uri) as websocket:
            session = await _register(websocket)
            for number in range(BAD_SIGNATURE_LIMIT):
                await session.send({"sender": SENDER, **_claim(f"bad-{number}")})
            with pytest.raises(ConnectionClosed):
                for _ in range(BAD_SIGNATURE_LIMIT + 5):
                    await read_json(websocket)
            assert websocket.close_code == 4013
    assert not hub.state.claims


async def test_a_key_that_is_no_longer_trusted_closes_the_connection() -> None:
    hub = _hub()
    async with running_hub(hub) as (_, uri):
        async with connect(uri) as websocket:
            session = await _register(websocket)
            hub._identity_gate.replace_trust_bundle(_bundle(revoked=True))
            await session.send(session.signed(**_claim("after-revoke")))
            refused = await read_until_type(websocket, "error")
            with pytest.raises(ConnectionClosed):
                await read_json(websocket)
            assert websocket.close_code == 4013
    assert refused["verification_result"] == "frame_signature_key_untrusted"
    assert "after-revoke" not in hub.state.claims


async def test_reads_pass_unsigned_and_a_reconnect_starts_a_new_session() -> None:
    hub = _hub()
    async with running_hub(hub) as (_, uri):
        async with connect(uri) as websocket:
            first = await _register(websocket)
            await first.send({"sender": SENDER, "type": "state_request"})
            await read_until_type(websocket, "state_snapshot")
        async with connect(uri) as websocket:
            second = await _register(websocket)
            assert second.nonce != first.nonce
            await second.send(second.signed(**_claim("fresh")))
            await read_until_type(websocket, "claim_granted")


async def test_without_the_requirement_unsigned_mutations_pass() -> None:
    hub = _hub(required=False)
    async with running_hub(hub) as (_, uri):
        async with connect(uri) as websocket:
            await _register(websocket)
            await websocket.send(json.dumps({"sender": SENDER, **_claim("plain")}))
            await read_until_type(websocket, "claim_granted")


async def test_a_connection_without_a_proven_key_is_refused_at_registration(
    tmp_path: Path,
) -> None:
    hub = SynapseHub(
        hub_id="fsg-tofu", identity_pin_path=tmp_path / "pins.json", require_signed_frames=True
    )
    async with running_hub(hub) as (_, uri):
        async with connect(uri) as websocket:
            await read_until_type(websocket, "welcome")
            await websocket.send(
                json.dumps({"sender": "P/plain", "type": "heartbeat", "target": "System"})
            )
            refused = await read_until_type(websocket, "error")
            with pytest.raises(ConnectionClosed):
                await read_json(websocket)
            assert websocket.close_code == 4013
    assert refused["verification_result"] == "frame_signature_no_identity"
    assert "P/plain" not in hub.clients.agent_sockets


def test_only_verbs_that_change_nothing_pass_unsigned() -> None:
    read_only = {name for name, spec in VERBS.items() if not spec.mutates}
    assert read_only == READ_ONLY_TYPES
    assert "heartbeat" not in READ_ONLY_TYPES and "chat" not in READ_ONLY_TYPES


def test_keys_are_looked_up_afresh() -> None:
    public = base64.b64encode(KEY.public_key().public_bytes_raw()).decode("ascii")
    pins = IdentityPinStore()
    pins.pin(SENDER, key_id="m1", public_key=public)
    assert pin_key(pins, "m1", SENDER) is not None
    assert pin_key(pins, "m2", SENDER) is None
    assert pin_key(None, "m1", SENDER) is None
    assert pin_key(pins, "m1", "other") is None
    assert bundle_key(_bundle(), "k", SENDER, time.time()) is not None
    assert bundle_key(_bundle(revoked=True), "k", SENDER, time.time()) is None
    assert bundle_key(_bundle(), "k", "other", time.time()) is None
    assert bundle_key(_bundle(), "missing", SENDER, time.time()) is None
    assert bundle_key(None, "k", SENDER, time.time()) is None
    expiring = EventSignatureTrustBundle(
        keys={
            "k": EventSignatureKey.from_private_key(
                key_id="k", private_key=KEY, senders=frozenset({SENDER}), expires_at=10.0
            )
        },
        replay_cache=MessageReplayCache(window_seconds=30.0, max_entries=8),
    )
    assert bundle_key(expiring, "k", SENDER, 11.0) is None
    assert bundle_key(expiring, "k", SENDER, 9.0) is not None


def test_startup_refuses_peers_and_a_missing_signature_library(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    served: list[bool] = []

    def runner(coroutine: Coroutine[Any, Any, None]) -> None:
        served.append(True)
        coroutine.close()

    assert cli_processes._cmd_hub(_hub_ns(require_signed_frames=True), runner=runner) == 0
    assert served == [True]
    for peer in ({"relay_peer": ["peer=wss://x"]}, {"message_peer": ["peer=wss://x"]}):
        namespace = _hub_ns(require_signed_frames=True, **peer)
        assert cli_processes._cmd_hub(namespace, runner=runner) == 2
        assert "cannot serve multi-hub peers" in capsys.readouterr().err
    monkeypatch.setitem(sys.modules, "cryptography", None)
    assert cli_processes._cmd_hub(_hub_ns(require_signed_frames=True), runner=runner) == 2
    assert "needs the encryption extra" in capsys.readouterr().err
    assert served == [True]


async def test_a_tofu_connection_signs_with_its_pinned_key(tmp_path: Path) -> None:
    hub = SynapseHub(
        hub_id="fsg-pin", identity_pin_path=tmp_path / "pins.json", require_signed_frames=True
    )
    machine = Ed25519PrivateKey.generate()
    public = base64.b64encode(machine.public_key().public_bytes_raw()).decode("ascii")
    async with running_hub(hub) as (_, uri):
        async with connect(uri) as websocket:
            welcome = await read_until_type(websocket, "welcome")
            registration = sign_registration(
                {
                    "sender": "P/pinned",
                    "type": "heartbeat",
                    "target": "System",
                    "identity_public_key": public,
                },
                private_key=machine,
                key_id="machine:x",
                nonce=uuid.uuid4().hex,
                sequence=1,
            )
            await websocket.send(json.dumps(registration))
            signed = sign_session_frame(
                {"sender": "P/pinned", **_claim("pinned")},
                key_id="machine:x",
                private_key=machine,
                hub_id=str(welcome["hub_id"]),
                session_nonce=str(welcome["session_nonce"]),
                seq=1,
            )
            await websocket.send(json.dumps(signed))
            await read_until_type(websocket, "claim_granted")
    assert "pinned" in hub.state.claims


async def _discard(_websocket: Any, _frame: dict[str, Any]) -> None:
    """Drop a frame the gate would send."""


async def _no_close(_websocket: Any, *, code: int, reason: str) -> None:
    """Pretend to close a connection."""


def test_a_bound_connection_without_a_record_is_closed() -> None:
    sessions = FrameSigningSessions()
    gate = FrameSignatureGate(
        sessions=sessions,
        hub_id="h",
        enabled=lambda: True,
        bundle=lambda: None,
        pins=None,
        send_json=_discard,
        system=lambda *args, **kwargs: {},
        close_socket=_no_close,
        count_refusal=lambda: None,
    )
    outcome = gate.check(object(), "A", "claim", {"type": "claim"})
    assert (outcome.verdict, outcome.reason) == (GateVerdict.CLOSE, NO_IDENTITY)
    assert gate.check(object(), "A", "who_request", {}).verdict is GateVerdict.ADMIT
