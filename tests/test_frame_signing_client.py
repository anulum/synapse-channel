# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — shipped clients sign frames when the hub requires it
"""Exercise client-side frame signing against real hubs."""

from __future__ import annotations

import asyncio
import socket
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from hub_e2e_helpers import running_hub
from synapse_channel.cli_messaging_send import _send
from synapse_channel.client.agent import SynapseAgent
from synapse_channel.client.frame_signing_client import (
    ClientFrameSession,
    session_from_welcome,
    sign_outbound,
)
from synapse_channel.core.frame_canonical import FrameCanonicalError
from synapse_channel.core.frame_signature import FrameSignatureResult, verify_session_frame
from synapse_channel.core.hub import SynapseHub
from synapse_channel.machine_identity import machine_identity_agent_kwargs


def test_a_welcome_asks_for_a_session_only_when_it_requires_signatures() -> None:
    welcome = {"hub_id": "h", "session_nonce": "n", "frame_signatures": "required"}
    assert session_from_welcome(welcome) == ClientFrameSession("h", "n")
    assert session_from_welcome({**welcome, "frame_signatures": "accepted"}) is None
    assert session_from_welcome({"hub_id": "h", "session_nonce": "n"}) is None
    assert session_from_welcome({**welcome, "session_nonce": ""}) is None
    assert session_from_welcome({**welcome, "hub_id": 5}) is None


def test_outbound_frames_are_numbered_and_an_unsignable_frame_is_refused() -> None:
    key = Ed25519PrivateKey.generate()
    session = ClientFrameSession("h", "n")
    first = sign_outbound({"type": "who_request"}, session, key_id="k", private_key=key)
    second = sign_outbound({"type": "claim"}, session, key_id="k", private_key=key)
    assert [first["frame_signature"]["seq"], second["frame_signature"]["seq"]] == [1, 2]
    assert verify_session_frame(
        second, public_key=key.public_key(), key_id="k", hub_id="h", session_nonce="n", last_seq=1
    ) == (FrameSignatureResult.VALID, 2)
    with pytest.raises(FrameCanonicalError, match="I-JSON"):
        sign_outbound({"type": "claim", "size": 2**60}, session, key_id="k", private_key=key)
    assert session.seq == 2


async def _until(condition: Callable[[], bool], seconds: float = 5.0) -> None:
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, "condition not reached in time"
        await asyncio.sleep(0.01)


async def test_a_shipped_agent_signs_on_a_hub_that_requires_it(tmp_path: Path) -> None:
    hub = SynapseHub(
        hub_id="fsc", identity_pin_path=tmp_path / "pins.json", require_signed_frames=True
    )
    async with running_hub(hub) as (_, uri):
        kwargs = machine_identity_agent_kwargs(base=tmp_path / "machine")
        agent = SynapseAgent("P/agent", None, uri=uri, verbose=False, **kwargs)
        task = asyncio.create_task(agent.connect())
        try:
            assert await agent.wait_until_ready(timeout=5)
            assert agent.frame_session is not None
            await agent.claim("signed-task", paths=["src/a.py"])
            await _until(lambda: "signed-task" in hub.state.claims)
            await agent.send_message("heartbeat", target="System")
            await agent.send_message("who_request", target="System")
            await asyncio.sleep(0.2)
            first_session = agent.frame_session
            assert first_session.seq >= 3
        finally:
            agent.running = False
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert hub.counters.auth_failures == 0


async def test_an_agent_sends_plain_frames_to_a_hub_that_does_not_ask(tmp_path: Path) -> None:
    hub = SynapseHub(hub_id="fsc-plain", identity_pin_path=tmp_path / "pins.json")
    async with running_hub(hub) as (_, uri):
        kwargs = machine_identity_agent_kwargs(base=tmp_path / "machine")
        agent = SynapseAgent("P/agent", None, uri=uri, verbose=False, **kwargs)
        task = asyncio.create_task(agent.connect())
        try:
            assert await agent.wait_until_ready(timeout=5)
            assert agent.frame_session is None
            await agent.claim("plain-task")
            await _until(lambda: "plain-task" in hub.state.claims)
        finally:
            agent.running = False
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_the_send_command_signs_with_the_machine_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    hub = SynapseHub(
        hub_id="fsc-cli", identity_pin_path=tmp_path / "pins.json", require_signed_frames=True
    )
    async with running_hub(hub) as (_, uri):
        code = await _send(uri=uri, name="P/cli", target="all", message="hello", wait_seconds=0.0)
    assert code == 0
    assert hub.counters.auth_failures == 0
    assert "frame signature refused" not in capsys.readouterr().out


async def test_an_old_client_is_told_to_upgrade(tmp_path: Path) -> None:
    hub = SynapseHub(
        hub_id="fsc-old", identity_pin_path=tmp_path / "pins.json", require_signed_frames=True
    )
    async with running_hub(hub) as (_, uri):
        kwargs: dict[str, Any] = machine_identity_agent_kwargs(base=tmp_path / "machine")
        agent = SynapseAgent("P/old", None, uri=uri, verbose=False, **kwargs)
        task = asyncio.create_task(agent.connect())
        try:
            assert await agent.wait_until_ready(timeout=5)
            await _until(lambda: "P/old" in hub.clients.agent_sockets)
            # An older client keeps registering with its key but sends plain frames.
            agent.frame_session = None
            replies: list[dict[str, Any]] = []

            async def collect(data: dict[str, Any]) -> None:
                replies.append(data)

            agent.callback = collect
            await agent.send_message("who_request", target="System")
            await _until(lambda: any(r.get("type") == "error" for r in replies))
        finally:
            agent.running = False
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    (error,) = [reply for reply in replies if reply.get("type") == "error"]
    assert error["verification_result"] == "frame_signature_required"
    assert "upgrade the client" in error["payload"]


async def test_a_new_connection_attempt_drops_the_old_signing_session(tmp_path: Path) -> None:
    # A frame sent before the next welcome must not be signed for the ended connection.
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    agent = SynapseAgent("P/agent", None, uri=f"ws://127.0.0.1:{port}", verbose=False)
    agent.frame_session = ClientFrameSession("old-hub", "old-nonce", seq=7)
    await agent.connect()
    assert agent.frame_session is None
