# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — a hub that admits only registrations that prove a key
"""Exercise ``--require-identity-proof`` on real hubs and at startup."""

from __future__ import annotations

import asyncio
import contextlib
import json
import sys
import time
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

import pytest
from websockets.asyncio.client import connect

from cli_processes_helpers import _hub_ns
from hub_e2e_helpers import Recorder, running_hub
from synapse_channel import cli_processes
from synapse_channel.cli import build_parser
from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.hub_identity_gate import (
    IDENTITY_BINDING_CLOSE_CODE,
    IDENTITY_PROOF_REQUIRED,
    HubIdentityGate,
)
from synapse_channel.core.identity_keys import load_signing_key, sign_registration
from synapse_channel.core.protocol import build_envelope
from synapse_channel.machine_identity import ensure_machine_identity, machine_identity_agent_kwargs

NAME = "PROJ/proof"


async def _until(condition: Callable[[], bool], seconds: float = 5.0) -> None:
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, "condition not reached in time"
        await asyncio.sleep(0.01)


@contextlib.asynccontextmanager
async def _agent(uri: str, **kwargs: Any) -> Any:
    recorder = Recorder()
    agent = SynapseAgent(NAME, recorder, uri=uri, verbose=False, **kwargs)
    task = asyncio.create_task(agent.connect())
    try:
        yield agent, recorder
    finally:
        agent.running = False
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def _proof_hub(tmp_path: Path, **extra: Any) -> SynapseHub:
    return SynapseHub(
        hub_id="proof",
        identity_pin_path=tmp_path / "pins.json",
        require_identity_proof=True,
        **extra,
    )


async def test_a_registration_without_a_proof_is_refused(tmp_path: Path) -> None:
    hub = _proof_hub(tmp_path)
    async with running_hub(hub) as (_, uri):
        async with _agent(uri, machine_identity=False) as (agent, recorder):
            await _until(lambda: agent.last_close_code is not None)
    assert agent.last_close_code == IDENTITY_BINDING_CLOSE_CODE
    assert agent.last_close_reason == "identity proof required"
    (error,) = [m for m in recorder.messages if m.get("type") == "error"]
    assert error["verification_result"] == IDENTITY_PROOF_REQUIRED
    assert "machine identity" in error["payload"]
    assert NAME not in hub.clients.agent_sockets
    assert hub.identity_pins.pinned(NAME) is None


async def test_a_machine_identity_registration_is_admitted_and_pinned(tmp_path: Path) -> None:
    hub = _proof_hub(tmp_path)
    async with running_hub(hub) as (_, uri):
        kwargs = machine_identity_agent_kwargs(base=tmp_path / "machine")
        async with _agent(uri, **kwargs) as (agent, _recorder):
            assert await agent.wait_until_ready(timeout=5.0)
            await _until(lambda: NAME in hub.clients.agent_sockets)
            assert agent.last_close_code is None
    pin = hub.identity_pins.pinned(NAME)
    assert pin is not None
    assert pin.key_id == kwargs["identity_key_id"]


def _signed_registration(tmp_path: Path, *, with_public_key: bool, nonce: str) -> dict[str, Any]:
    kwargs = machine_identity_agent_kwargs(base=tmp_path / "machine")
    frame = build_envelope(NAME, "heartbeat", target="System", payload="online")
    if with_public_key:
        frame["identity_public_key"] = ensure_machine_identity(base=tmp_path / "machine").public_key
    return sign_registration(
        frame,
        private_key=load_signing_key(str(kwargs["identity_key_path"])),
        key_id=str(kwargs["identity_key_id"]),
        nonce=nonce,
        sequence=1,
    )


async def _register_raw(uri: str, frame: dict[str, Any]) -> list[dict[str, Any]]:
    """Send one registration over a raw socket; return the frames until the hub closes."""
    received: list[dict[str, Any]] = []
    async with connect(uri) as websocket:
        received.append(json.loads(await websocket.recv()))  # welcome
        await websocket.send(json.dumps(frame))
        with contextlib.suppress(Exception):
            async for raw in websocket:
                received.append(json.loads(raw))
    return received


async def test_a_signature_without_its_public_key_is_no_proof(tmp_path: Path) -> None:
    hub = _proof_hub(tmp_path)
    frame = _signed_registration(tmp_path, with_public_key=False, nonce="proof-no-key")
    async with running_hub(hub) as (_, uri):
        received = await asyncio.wait_for(_register_raw(uri, frame), timeout=5.0)
    errors = [m for m in received if m.get("type") == "error"]
    assert [m["verification_result"] for m in errors] == [IDENTITY_PROOF_REQUIRED]
    assert hub.identity_pins.pinned(NAME) is None


async def test_without_the_flag_an_unproven_registration_is_still_admitted(
    tmp_path: Path,
) -> None:
    hub = SynapseHub(hub_id="proof-off", identity_pin_path=tmp_path / "pins.json")
    async with running_hub(hub) as (_, uri):
        async with _agent(uri, machine_identity=False) as (agent, _recorder):
            assert await agent.wait_until_ready(timeout=5.0)
            await _until(lambda: NAME in hub.clients.agent_sockets)
            assert agent.last_close_code is None


async def test_a_proof_that_cannot_be_verified_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = _signed_registration(tmp_path, with_public_key=True, nonce="proof-no-crypto")
    hub = _proof_hub(tmp_path)
    async with running_hub(hub) as (_, uri):
        for name in list(sys.modules):
            if name == "cryptography" or name.startswith("cryptography."):
                monkeypatch.delitem(sys.modules, name)
        monkeypatch.setitem(sys.modules, "cryptography", None)
        received = await asyncio.wait_for(_register_raw(uri, frame), timeout=5.0)
    errors = [m for m in received if m.get("type") == "error"]
    assert [m["verification_result"] for m in errors] == [IDENTITY_PROOF_REQUIRED]
    assert NAME not in hub.clients.agent_sockets


class _Socket:
    """Record the close a gate applies, standing in for a connection."""

    def __init__(self) -> None:
        self.closed: list[tuple[int, str]] = []

    async def close(self, *, code: int, reason: str) -> None:
        self.closed.append((code, reason))


async def test_without_a_pin_store_the_requirement_still_refuses() -> None:
    sent: list[dict[str, Any]] = []
    required = [True]

    async def send_json(_socket: Any, frame: dict[str, Any]) -> None:
        sent.append(frame)

    gate = HubIdentityGate(
        require_identity_binding=False,
        identity_trust_bundle=None,
        send_json=send_json,
        system=lambda payload, **fields: {"payload": payload, **fields},
        require_identity_proof=lambda: required[0],
    )
    refused = _Socket()
    assert await gate.verify_identity(NAME, {"sender": NAME}, refused) is False
    assert refused.closed == [(IDENTITY_BINDING_CLOSE_CODE, "identity proof required")]
    assert sent[0]["verification_result"] == IDENTITY_PROOF_REQUIRED
    required[0] = False
    admitted = _Socket()
    assert await gate.verify_identity(NAME, {"sender": NAME}, admitted) is True
    assert admitted.closed == []


def test_startup_refuses_the_requirement_without_the_signature_library(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    served: list[bool] = []

    def runner(coroutine: Coroutine[Any, Any, None]) -> None:
        served.append(True)
        coroutine.close()

    assert cli_processes._cmd_hub(_hub_ns(require_identity_proof=True), runner=runner) == 0
    assert served == [True]
    monkeypatch.setitem(sys.modules, "cryptography", None)
    assert cli_processes._cmd_hub(_hub_ns(require_identity_proof=True), runner=runner) == 2
    assert "--require-identity-proof needs the encryption extra" in capsys.readouterr().err
    assert served == [True]


def test_the_command_line_flag_reaches_the_hub(tmp_path: Path) -> None:
    built: list[SynapseHub] = []

    def hub_factory(**kwargs: Any) -> SynapseHub:
        hub = SynapseHub(**kwargs)
        built.append(hub)
        return hub

    def runner(coroutine: Coroutine[Any, Any, None]) -> None:
        coroutine.close()

    parser = build_parser()
    pins = str(tmp_path / "pins.json")
    for flags, expected in (([], False), (["--require-identity-proof"], True)):
        args = parser.parse_args(["hub", "--identity-pins", pins, *flags])
        assert cli_processes._cmd_hub(args, runner=runner, hub_factory=hub_factory) == 0
        assert built[-1].require_identity_proof is expected


def test_the_requirement_is_stored_as_a_plain_bool(tmp_path: Path) -> None:
    options: dict[str, Any] = {"require_identity_proof": 1}
    hub = SynapseHub(identity_pin_path=tmp_path / "pins.json", **options)
    assert hub.require_identity_proof is True
