# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — a signing connection whose key stops being trusted is closed
"""Re-check signing connections when their keys change or expire."""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from hub_e2e_helpers import Recorder, running_hub
from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.frame_signature_gate import KEY_UNTRUSTED, FrameSignatureGate
from synapse_channel.core.frame_signing import FrameSigningSessions
from synapse_channel.core.handlers.identity_enrollments import KEY_ROTATED_CLOSE_CODE
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.hub_identity_gate import IDENTITY_BINDING_CLOSE_CODE
from synapse_channel.core.identity_keys import write_signing_key
from synapse_channel.core.message_auth import (
    EventSignatureKey,
    EventSignatureTrustBundle,
    MessageReplayCache,
)
from test_hub_identity_enrollment import (
    OPERATOR,
    SEAT,
    Machines,
    _connected,
    _enrol,
    _hub,
    _revoke,
    _seat_request,
)

if TYPE_CHECKING:
    from pathlib import Path

PROMPT_CLOSE_SECONDS = 3.0
"""Well inside the first periodic re-check, so only the immediate one can meet it."""

SELF_ENROLLER_ROLES = {
    "OPS/identity-enroller": frozenset({OPERATOR}),
    "PROJ/identity-enroller": frozenset({SEAT}),
}


async def _until(condition: Callable[[], bool], seconds: float = 10.0) -> None:
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, "condition not reached in time"
        await asyncio.sleep(0.01)


def _untrusted(recorder: Recorder) -> list[dict[str, Any]]:
    return [m for m in recorder.messages if m.get("verification_result") == KEY_UNTRUSTED]


async def _enrol_seat(uri: str, machines: Machines) -> None:
    async with _connected(OPERATOR, uri, machines.kwargs("operator")) as (operator, inbox):
        assert (await _enrol(inbox, operator, **_seat_request(machines)))["applied"]


async def test_revoking_its_own_key_closes_a_signing_connection(tmp_path: Path) -> None:
    machines = Machines(tmp_path / "machines")
    hub = _hub(tmp_path, machines, roles=SELF_ENROLLER_ROLES, acl_pattern="*", acl_namespace="")
    hub.require_signed_frames = True
    seat_key_id, _ = machines.public("seat")
    async with running_hub(hub) as (_hub_ref, uri):
        await _enrol_seat(uri, machines)
        async with _connected(SEAT, uri, machines.kwargs("seat")) as (seat, seat_inbox):
            own = await _revoke(
                seat_inbox, seat, name=SEAT, key_id=seat_key_id, reason="retiring this key"
            )
            await _until(lambda: seat.last_close_code is not None, PROMPT_CLOSE_SECONDS)
    assert hub.journal is not None
    hub.journal.close()
    # The requester learns the result first, then why its connection ends.
    assert own["applied"] is True
    assert seat.last_close_code == IDENTITY_BINDING_CLOSE_CODE
    assert seat.last_close_reason == "identity key no longer trusted"
    assert len(_untrusted(seat_inbox)) == 1
    assert len(hub.frame_signing) == 0


async def test_rotating_its_own_key_closes_the_connection_that_proved_the_old_one(
    tmp_path: Path,
) -> None:
    machines = Machines(tmp_path / "machines")
    hub = _hub(tmp_path, machines, roles=SELF_ENROLLER_ROLES, acl_pattern="*", acl_namespace="")
    hub.require_signed_frames = True
    old_key_id, _ = machines.public("seat")
    async with running_hub(hub) as (_hub_ref, uri):
        await _enrol_seat(uri, machines)
        async with _connected(SEAT, uri, machines.kwargs("seat")) as (seat, seat_inbox):
            own = await _enrol(
                seat_inbox, seat, **_seat_request(machines, "seat-2"), expected_key_id=old_key_id
            )
            await _until(lambda: seat.last_close_code is not None, PROMPT_CLOSE_SECONDS)
        # The new key registers and signs as usual.
        async with _connected(SEAT, uri, machines.kwargs("seat-2")) as (renewed, renewed_inbox):
            await renewed.send_message("who_request", target="System")
            await renewed_inbox.wait_for(lambda m: m.get("type") == "who_snapshot")
            assert renewed.last_close_code is None
    assert hub.journal is not None
    hub.journal.close()
    assert own["applied"] is True
    assert seat.last_close_code == IDENTITY_BINDING_CLOSE_CODE
    assert len(_untrusted(seat_inbox)) == 1
    assert hub.counters.auth_failures == 1


async def test_an_operator_revocation_still_closes_with_the_rotation_code(tmp_path: Path) -> None:
    machines = Machines(tmp_path / "machines")
    hub = _hub(tmp_path, machines)
    hub.require_signed_frames = True
    seat_key_id, _ = machines.public("seat")
    async with running_hub(hub) as (_hub_ref, uri):
        async with _connected(OPERATOR, uri, machines.kwargs("operator")) as (operator, inbox):
            assert (await _enrol(inbox, operator, **_seat_request(machines)))["applied"]
            async with _connected(SEAT, uri, machines.kwargs("seat")) as (seat, _seat_inbox):
                revoked = await _revoke(
                    inbox, operator, name=SEAT, key_id=seat_key_id, reason="seat retired"
                )
                await _until(lambda: seat.last_close_code is not None)
            # The operator's own connection is untouched and still signs.
            await operator.send_message("who_request", target="System")
            await inbox.wait_for(lambda m: m.get("type") == "who_snapshot")
            assert operator.last_close_code is None
    assert hub.journal is not None
    hub.journal.close()
    assert revoked["applied"] is True
    assert seat.last_close_code == KEY_ROTATED_CLOSE_CODE


async def test_a_listening_connection_is_closed_once_its_key_expires(tmp_path: Path) -> None:
    key = Ed25519PrivateKey.generate()
    key_path = tmp_path / "listener.key"
    write_signing_key(key_path, key)
    expiring = EventSignatureKey.from_private_key(
        key_id="short",
        private_key=key,
        senders=frozenset({"P/listener"}),
        expires_at=time.time() + 1.5,
    )
    hub = SynapseHub(
        hub_id="recheck-expiry",
        identity_trust_bundle=EventSignatureTrustBundle(
            keys={"short": expiring},
            replay_cache=MessageReplayCache(window_seconds=30.0, max_entries=64),
        ),
        require_identity_binding=True,
        require_signed_frames=True,
        identity_pin_path=tmp_path / "pins.json",
    )
    recorder = Recorder()
    async with running_hub(hub) as (_hub_ref, uri):
        agent = SynapseAgent(
            "P/listener",
            recorder,
            uri=uri,
            verbose=False,
            machine_identity=False,
            identity_key_path=str(key_path),
            identity_key_id="short",
        )
        task = asyncio.create_task(agent.connect())
        try:
            assert await agent.wait_until_ready(timeout=5.0)
            # It sends nothing more; the periodic re-check alone must close it.
            await _until(lambda: agent.last_close_code is not None, seconds=15.0)
        finally:
            agent.running = False
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
    assert agent.last_close_code == IDENTITY_BINDING_CLOSE_CODE
    assert len(_untrusted(recorder)) == 1


class _Sent:
    """Collect what a gate sends and closes, standing in for the hub's transport."""

    def __init__(self) -> None:
        self.frames: list[dict[str, Any]] = []
        self.closed: list[tuple[Any, int, str]] = []

    async def send_json(self, _websocket: Any, frame: dict[str, Any]) -> None:
        self.frames.append(frame)

    async def close_socket(self, websocket: Any, *, code: int, reason: str) -> None:
        self.closed.append((websocket, code, reason))


def _gate(
    *, now: list[float], enabled: list[bool], bundle: EventSignatureTrustBundle, sent: _Sent
) -> tuple[FrameSignatureGate, FrameSigningSessions]:
    sessions = FrameSigningSessions()
    refusals: list[int] = []
    gate = FrameSignatureGate(
        sessions=sessions,
        hub_id="h",
        enabled=lambda: enabled[0],
        bundle=lambda: bundle,
        pins=None,
        send_json=sent.send_json,
        system=lambda payload, **fields: {"payload": payload, **fields},
        close_socket=sent.close_socket,
        count_refusal=lambda: refusals.append(1),
        clock=lambda: now[0],
    )
    return gate, sessions


def _expiring_bundle(expires_at: float) -> EventSignatureTrustBundle:
    entry = EventSignatureKey.from_private_key(
        key_id="k",
        private_key=Ed25519PrivateKey.generate(),
        senders=frozenset({"P/a"}),
        expires_at=expires_at,
    )
    return EventSignatureTrustBundle(
        keys={"k": entry},
        replay_cache=MessageReplayCache(window_seconds=30.0, max_entries=64),
    )


async def test_a_recheck_closes_only_untrusted_connections_and_each_once() -> None:
    now, enabled, sent = [100.0], [True], _Sent()
    gate, sessions = _gate(now=now, enabled=enabled, bundle=_expiring_bundle(150.0), sent=sent)
    expiring, unknown = object(), object()
    sessions.record(expiring, "P/a", "k", "bundle")
    sessions.record(unknown, "P/b", "missing", "bundle")
    assert await gate.recheck() == 1
    assert sent.closed == [(unknown, IDENTITY_BINDING_CLOSE_CODE, "identity key no longer trusted")]
    assert sent.frames[0]["verification_result"] == KEY_UNTRUSTED
    assert sent.frames[0]["target"] == "P/b"
    now[0] = 151.0
    assert await gate.recheck() == 1
    assert [closed[0] for closed in sent.closed] == [unknown, expiring]
    assert await gate.recheck() == 0
    assert len(sessions) == 0


async def test_a_recheck_does_nothing_while_signed_frames_are_not_required() -> None:
    now, enabled, sent = [200.0], [False], _Sent()
    gate, sessions = _gate(now=now, enabled=enabled, bundle=_expiring_bundle(150.0), sent=sent)
    sessions.record(object(), "P/a", "k", "bundle")
    assert await gate.recheck() == 0
    assert (sent.frames, sent.closed, len(sessions)) == ([], [], 1)


async def test_the_recheck_loop_runs_until_cancelled() -> None:
    now, enabled, sent = [100.0], [True], _Sent()
    gate, sessions = _gate(now=now, enabled=enabled, bundle=_expiring_bundle(150.0), sent=sent)
    socket = object()
    sessions.record(socket, "P/a", "k", "bundle")
    loop = asyncio.create_task(gate.recheck_loop(interval=0.01))
    try:
        await asyncio.sleep(0.05)
        assert sent.closed == []
        now[0] = 151.0
        await _until(lambda: bool(sent.closed), seconds=5.0)
    finally:
        loop.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await loop
    assert loop.cancelled()
    assert [closed[0] for closed in sent.closed] == [socket]
