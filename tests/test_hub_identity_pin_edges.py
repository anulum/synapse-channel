# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — pinned-name edges: a pin without a key id, a hub losing cryptography
"""A pin naming no key id verifies nothing; a pinned name fails closed without crypto."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any

import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from hub_e2e_helpers import running_hub
from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.identity_keys import load_signing_key, sign_registration
from synapse_channel.core.protocol import build_envelope
from synapse_channel.machine_identity import ensure_machine_identity
from test_hub_identity_tofu import (
    IDENTITY_CLOSE,
    NAME,
    _await_bound,
    _await_unbound,
    _close,
    _machine,
    _run_until_closed_or_ready,
)


async def test_a_pin_without_a_key_id_refuses_a_frame_signed_without_one(tmp_path: Path) -> None:
    """An empty pinned key id never verifies, even a frame signed by that key under no id."""
    machine = ensure_machine_identity(base=tmp_path / "machine-a")
    pins = tmp_path / "pins.json"
    pins.write_text(
        json.dumps({"pins": {NAME: {"key_id": "", "public_key": machine.public_key}}}),
        encoding="utf-8",
    )
    frame = _signed_frame(tmp_path, "empty-key-id", 1, key_id="")
    hub = SynapseHub(hub_id="syn-tofu", identity_pin_path=pins)
    async with running_hub(hub) as (_, uri):
        async with connect(uri) as websocket:
            await websocket.recv()  # welcome
            await websocket.send(json.dumps(frame))
            await _frames_until_closed(websocket)
        assert (websocket.close_code, websocket.close_reason) == (
            IDENTITY_CLOSE,
            "identity pin mismatch",
        )
        assert hub.clients.agent_sockets.get(NAME) is None


async def _frames_until_closed(websocket: Any) -> list[dict[str, Any]]:
    """Collect frames until the hub closes; a silent hub fails within 10 s, never hangs."""
    frames: list[dict[str, Any]] = []
    with pytest.raises(ConnectionClosed):
        while True:
            frames.append(json.loads(await asyncio.wait_for(websocket.recv(), timeout=10.0)))
    return frames


def _signed_frame(
    tmp_path: Path,
    nonce: str,
    sequence: int,
    *,
    key_id: str | None = None,
    name: str = NAME,
) -> dict[str, Any]:
    kwargs = _machine(tmp_path, "machine-a")
    frame = build_envelope(name, "heartbeat", target="System", payload="online")
    frame["identity_public_key"] = ensure_machine_identity(base=tmp_path / "machine-a").public_key
    return sign_registration(
        frame,
        private_key=load_signing_key(str(kwargs["identity_key_path"])),
        key_id=str(kwargs["identity_key_id"]) if key_id is None else key_id,
        nonce=nonce,
        sequence=sequence,
    )


OTHER = "PROJ/tofu-unpinned"


async def test_without_cryptography_a_pinned_name_fails_closed_and_others_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A pinned owner is refused as unverifiable; unpinned signers pass with one warning."""
    pinned_frame = _signed_frame(tmp_path, "pinned-unverifiable", 10)
    other_frames = [
        _signed_frame(tmp_path, f"unpinned-{index}", index + 20, name=OTHER) for index in range(2)
    ]
    hub = SynapseHub(hub_id="syn-tofu", identity_pin_path=tmp_path / "pins.json")
    async with running_hub(hub) as (_, uri):
        owner = SynapseAgent(NAME, None, uri=uri, verbose=False, **_machine(tmp_path, "machine-a"))
        owner_task = await _run_until_closed_or_ready(owner)
        await _await_bound(hub, NAME)
        await _close(owner, owner_task)
        await _await_unbound(hub, NAME)
        pin = hub.identity_pins.pinned(NAME)
        assert pin is not None

        for name in list(sys.modules):
            if name == "cryptography" or name.startswith("cryptography."):
                monkeypatch.delitem(sys.modules, name)
        monkeypatch.setitem(sys.modules, "cryptography", None)
        with caplog.at_level(logging.WARNING, logger="synapse.hub"):
            async with connect(uri) as websocket:
                await websocket.recv()  # welcome
                await websocket.send(json.dumps(pinned_frame))
                frames = await _frames_until_closed(websocket)
            assert (websocket.close_code, websocket.close_reason) == (
                IDENTITY_CLOSE,
                "identity pin unverifiable",
            )
            assert [frame["payload"] for frame in frames] == [
                f"identity pin: name {NAME!r} is pinned to key {pin.key_id}, but this hub "
                "cannot verify it because the optional cryptography package is not "
                "installed. Restore it (pip install synapse-channel[encryption]) and "
                "restart the hub; the pin is unchanged."
            ]
            assert hub.clients.agent_sockets.get(NAME) is None
            for frame in other_frames:
                async with connect(uri) as websocket:
                    await websocket.recv()  # welcome
                    await websocket.send(json.dumps(frame))
                    await _await_bound(hub, OTHER)
                await _await_unbound(hub, OTHER)
        warnings = [record for record in caplog.records if "trust-on-first-use" in record.message]
        assert len(warnings) == 1
        assert hub.identity_pins.pinned(NAME) == pin
        assert hub.identity_pins.pinned(OTHER) is None
