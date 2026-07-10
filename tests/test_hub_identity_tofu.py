# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — trust-on-first-use identity pinning, end to end

"""The ownership keystone's durable half, proven over real websockets.

Zero configuration, zero operator input: a fresh data home provisions the
machine keypair on first use, the production client signs its registration
with it and presents the public half, the hub verifies the proof and pins the
name to the key. From then on the name binds only to a connection proving
possession of that key — across reconnects *and* hub restarts — while names
that never sign keep classic first-come semantics. Every test drives the real
client against a live in-process hub; nothing is stubbed.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from hub_e2e_helpers import running_hub
from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.hub import SynapseHub
from synapse_channel.machine_identity import (
    MACHINE_KEY_ID_PREFIX,
    ensure_machine_identity,
    machine_identity_agent_kwargs,
)

NAME = "PROJ/tofu-owner"
IDENTITY_CLOSE = 4013


async def _run_until_closed_or_ready(
    agent: SynapseAgent, *, timeout: float = 3.0
) -> asyncio.Task[None]:
    """Start the agent and wait until it is ready or the hub closed it."""
    task = asyncio.create_task(agent.connect())
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if agent.ready_event.is_set() or agent.last_close_code is not None:
            break
        await asyncio.sleep(0.01)
    return task


async def _await_refused(agent: SynapseAgent, *, timeout: float = 3.0) -> tuple[int | None, str]:
    """Wait for the hub to close the agent's socket; return code and reason."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline and agent.last_close_code is None:
        await asyncio.sleep(0.01)
    return agent.last_close_code, agent.last_close_reason


async def _await_bound(hub: SynapseHub, name: str, *, timeout: float = 3.0) -> None:
    """Poll the live registry until ``name`` is bound."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if hub.clients.agent_sockets.get(name) is not None:
            return
        await asyncio.sleep(0.01)
    raise TimeoutError(f"{name} did not bind on the hub")


async def _await_unbound(hub: SynapseHub, name: str, *, timeout: float = 3.0) -> None:
    """Poll the live registry until ``name`` is no longer bound."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if hub.clients.agent_sockets.get(name) is None:
            return
        await asyncio.sleep(0.01)
    raise TimeoutError(f"{name} did not unbind on the hub")


async def _close(agent: SynapseAgent, task: asyncio.Task[None]) -> None:
    """Stop an agent and cancel its connection task."""
    agent.running = False
    task.cancel()


def _machine(tmp_path: Path, label: str) -> dict[str, Any]:
    """Provision (or reuse) a distinct machine identity under ``tmp_path``."""
    return machine_identity_agent_kwargs(base=tmp_path / label)


async def test_zero_config_first_use_pins_the_name_to_the_machine_key(tmp_path: Path) -> None:
    """The pip-install acceptance: fresh data home, no flags, full protection."""
    pins = tmp_path / "pins.json"
    hub = SynapseHub(hub_id="syn-tofu", identity_pin_path=pins)
    async with running_hub(hub) as (_, uri):
        kwargs = _machine(tmp_path, "machine-a")
        assert kwargs, "the machine identity did not provision"
        owner = SynapseAgent(NAME, None, uri=uri, verbose=False, **kwargs)
        task = await _run_until_closed_or_ready(owner)
        await _await_bound(hub, NAME)
        loop = asyncio.get_event_loop()
        deadline = loop.time() + 3.0
        while loop.time() < deadline and hub._identity_pins.pinned(NAME) is None:
            await asyncio.sleep(0.01)
        pin = hub._identity_pins.pinned(NAME)
        assert pin is not None
        assert pin.key_id.startswith(MACHINE_KEY_ID_PREFIX)
        assert pins.is_file(), "the pin was not persisted"
        await _close(owner, task)


async def test_a_different_machine_key_is_refused_on_a_pinned_name(tmp_path: Path) -> None:
    hub = SynapseHub(hub_id="syn-tofu", identity_pin_path=tmp_path / "pins.json")
    async with running_hub(hub) as (_, uri):
        owner = SynapseAgent(NAME, None, uri=uri, verbose=False, **_machine(tmp_path, "machine-a"))
        owner_task = await _run_until_closed_or_ready(owner)
        await _await_bound(hub, NAME)
        await _close(owner, owner_task)
        await _await_unbound(hub, NAME)

        stranger = SynapseAgent(
            NAME, None, uri=uri, verbose=False, **_machine(tmp_path, "machine-b")
        )
        stranger_task = await _run_until_closed_or_ready(stranger)
        code, reason = await _await_refused(stranger)
        assert code == IDENTITY_CLOSE
        assert reason == "identity pin mismatch"
        stranger_task.cancel()


async def test_an_unsigned_claim_cannot_bypass_the_pin(tmp_path: Path) -> None:
    """Omitting the signature must not slip past the pinned name."""
    hub = SynapseHub(hub_id="syn-tofu", identity_pin_path=tmp_path / "pins.json")
    async with running_hub(hub) as (_, uri):
        owner = SynapseAgent(NAME, None, uri=uri, verbose=False, **_machine(tmp_path, "machine-a"))
        owner_task = await _run_until_closed_or_ready(owner)
        await _await_bound(hub, NAME)
        await _close(owner, owner_task)
        await _await_unbound(hub, NAME)

        unsigned = SynapseAgent(
            NAME, None, uri=uri, verbose=False, takeover=True, machine_identity=False
        )
        unsigned_task = await _run_until_closed_or_ready(unsigned)
        code, reason = await _await_refused(unsigned)
        assert code == IDENTITY_CLOSE
        assert reason == "identity pin mismatch"
        unsigned_task.cancel()


async def test_the_owner_re_takes_its_pinned_name_across_a_hub_restart(tmp_path: Path) -> None:
    """The durable half: the pin file outlives the hub process."""
    pins = tmp_path / "pins.json"
    async with running_hub(SynapseHub(hub_id="syn-tofu", identity_pin_path=pins)) as (hub, uri):
        owner = SynapseAgent(NAME, None, uri=uri, verbose=False, **_machine(tmp_path, "machine-a"))
        task = await _run_until_closed_or_ready(owner)
        await _await_bound(hub, NAME)
        await _close(owner, task)

    restarted = SynapseHub(hub_id="syn-tofu-2", identity_pin_path=pins)
    async with running_hub(restarted) as (hub2, uri2):
        stranger = SynapseAgent(
            NAME, None, uri=uri2, verbose=False, **_machine(tmp_path, "machine-b")
        )
        stranger_task = await _run_until_closed_or_ready(stranger)
        code, _reason = await _await_refused(stranger)
        assert code == IDENTITY_CLOSE
        stranger_task.cancel()

        owner_again = SynapseAgent(
            NAME, None, uri=uri2, verbose=False, **_machine(tmp_path, "machine-a")
        )
        owner_task = await _run_until_closed_or_ready(owner_again)
        await _await_bound(hub2, NAME)
        assert owner_again.last_close_code is None
        await _close(owner_again, owner_task)


async def test_names_that_never_sign_keep_classic_first_come_semantics(tmp_path: Path) -> None:
    hub = SynapseHub(hub_id="syn-tofu", identity_pin_path=tmp_path / "pins.json")
    async with running_hub(hub) as (_, uri):
        classic = SynapseAgent(NAME, None, uri=uri, verbose=False, machine_identity=False)
        task = await _run_until_closed_or_ready(classic)
        await _await_bound(hub, NAME)
        assert hub._identity_pins.pinned(NAME) is None
        await _close(classic, task)
        await _await_unbound(hub, NAME)

        next_comer = SynapseAgent(NAME, None, uri=uri, verbose=False, machine_identity=False)
        next_task = await _run_until_closed_or_ready(next_comer)
        await _await_bound(hub, NAME)
        assert next_comer.last_close_code is None
        assert hub._identity_pins.pinned(NAME) is None
        await _close(next_comer, next_task)


async def test_a_broken_proof_is_refused_and_never_pins(tmp_path: Path) -> None:
    """A signature that fails to verify must not be admitted or recorded.

    Drives the wire directly: the frame is signed with machine A's key but
    presents machine B's public half, so the self-contained proof cannot
    verify — the mismatch a confused (or lying) client would produce.
    """
    import json as jsonlib

    from websockets.asyncio.client import connect
    from websockets.exceptions import ConnectionClosed

    from synapse_channel.core.identity_keys import (
        load_signing_key,
        public_key_b64,
        sign_registration,
    )
    from synapse_channel.core.protocol import build_envelope

    hub = SynapseHub(hub_id="syn-tofu", identity_pin_path=tmp_path / "pins.json")
    async with running_hub(hub) as (_, uri):
        kwargs_a = _machine(tmp_path, "machine-a")
        kwargs_b = _machine(tmp_path, "machine-b")
        foreign_public = public_key_b64(load_signing_key(str(kwargs_b["identity_key_path"])))

        async with connect(uri) as websocket:
            await websocket.recv()  # welcome
            frame = build_envelope(NAME, "heartbeat", target="System", payload="online")
            frame["identity_public_key"] = foreign_public
            frame = sign_registration(
                frame,
                private_key=load_signing_key(str(kwargs_a["identity_key_path"])),
                key_id=str(kwargs_a["identity_key_id"]),
                nonce="tofu-liar-nonce",
                sequence=1,
            )
            await websocket.send(jsonlib.dumps(frame))
            closed_code: int | None = None
            try:
                while True:
                    await asyncio.wait_for(websocket.recv(), timeout=3.0)
            except ConnectionClosed as exc:
                closed_code = getattr(exc.rcvd, "code", None)
            assert closed_code == IDENTITY_CLOSE
        assert hub._identity_pins.pinned(NAME) is None


async def test_operator_bundle_enforcement_still_takes_precedence(tmp_path: Path) -> None:
    """--require-identity-binding keeps its fail-closed bundle posture."""
    hub = SynapseHub(
        hub_id="syn-tofu",
        identity_pin_path=tmp_path / "pins.json",
        require_identity_binding=True,
        identity_trust_bundle=None,
    )
    async with running_hub(hub) as (_, uri):
        # A machine-key proof is NOT an operator enrolment: with binding
        # required and no bundle, the gate fails closed even for a valid
        # trust-on-first-use credential.
        signer = SynapseAgent(NAME, None, uri=uri, verbose=False, **_machine(tmp_path, "m"))
        task = await _run_until_closed_or_ready(signer)
        code, reason = await _await_refused(signer)
        assert code == IDENTITY_CLOSE
        assert reason == "identity binding failed"
        assert hub._identity_pins.pinned(NAME) is None
        task.cancel()


async def test_a_hub_without_cryptography_degrades_open_instead_of_crashing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A core-only hub admits signed registrations unverified, with one warning.

    The frame is signed while the primitives are importable, then every
    ``cryptography`` import is blocked before the hub verifies — the exact
    state of a hub venv holding only the core dependency. Refusing would brick
    every signing client; crashing would kill the frame handler. It must
    admit, warn once, and record no pin it could not verify.
    """
    import json as jsonlib
    import logging
    import sys

    from websockets.asyncio.client import connect

    from synapse_channel.core.identity_keys import load_signing_key, sign_registration
    from synapse_channel.core.protocol import build_envelope

    kwargs = _machine(tmp_path, "machine-a")
    frame = build_envelope(NAME, "heartbeat", target="System", payload="online")
    machine = ensure_machine_identity(base=tmp_path / "machine-a")
    frame["identity_public_key"] = machine.public_key
    frame = sign_registration(
        frame,
        private_key=load_signing_key(str(kwargs["identity_key_path"])),
        key_id=str(kwargs["identity_key_id"]),
        nonce="tofu-degrade-nonce",
        sequence=1,
    )

    hub = SynapseHub(hub_id="syn-tofu", identity_pin_path=tmp_path / "pins.json")
    async with running_hub(hub) as (_, uri):
        for name in list(sys.modules):
            if name == "cryptography" or name.startswith("cryptography."):
                monkeypatch.delitem(sys.modules, name)
        monkeypatch.setitem(sys.modules, "cryptography", None)
        with caplog.at_level(logging.WARNING, logger="synapse.hub"):
            async with connect(uri) as websocket:
                await websocket.recv()  # welcome
                await websocket.send(jsonlib.dumps(frame))
                await _await_bound(hub, NAME)
        assert hub.clients.agent_sockets.get(NAME) is not None
        assert hub._identity_pins.pinned(NAME) is None
        assert any("trust-on-first-use" in record.message for record in caplog.records)


async def test_a_pinned_name_stays_usable_by_default_constructed_agents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The incident 2026-07-10T1603 regression, end to end on a real hub.

    An arm-style connect (explicit machine kwargs) pins the name on first use.
    Before F9 slice 0, every OTHER verb built its agent without the machine
    identity and was then refused ``signature missing`` — the seat locked
    itself out of its own name. A plain default construction must now present
    the same machine key and be admitted; only a deliberate opt-out is still
    refused, proving the pin keeps guarding.
    """
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    pins = tmp_path / "pins.json"
    hub = SynapseHub(hub_id="syn-tofu", identity_pin_path=pins)
    async with running_hub(hub) as (_, uri):
        # 1. The arm path (explicit kwargs, as cli_arm wires them) mints the pin.
        arm_style = SynapseAgent(
            NAME, None, uri=uri, verbose=False, **machine_identity_agent_kwargs()
        )
        arm_task = await _run_until_closed_or_ready(arm_style)
        await _await_bound(hub, NAME)
        pin = hub._identity_pins.pinned(NAME)
        assert pin is not None and pin.key_id.startswith(MACHINE_KEY_ID_PREFIX)
        await _close(arm_style, arm_task)
        await _await_unbound(hub, NAME)

        # 2. The send path: a plain construction, no identity kwargs anywhere.
        plain = SynapseAgent(NAME, None, uri=uri, verbose=False)
        plain_task = await _run_until_closed_or_ready(plain)
        await _await_bound(hub, NAME)
        assert plain.last_close_code is None
        await _close(plain, plain_task)
        await _await_unbound(hub, NAME)

        # 3. A deliberate opt-out is still refused: the pin keeps guarding.
        opted_out = SynapseAgent(NAME, None, uri=uri, verbose=False, machine_identity=False)
        opted_out_task = await _run_until_closed_or_ready(opted_out)
        code, reason = await _await_refused(opted_out)
        assert code == IDENTITY_CLOSE
        assert "identity" in reason
        opted_out_task.cancel()
