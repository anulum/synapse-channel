# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — governed online identity-key enrolment on real websockets (SOL4-ID-01)
"""An operator enrols an identity key on a live hub; every gate refuses on its own.

Each hub requires identity binding against an operator trust bundle that holds
only the operator's machine key. A new seat cannot register until the operator
enrols its key. After a governed ``identity_enroll`` the seat registers at once,
with no restart; a restarted hub still admits it from the hub-owned store.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import stat
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import pytest

from _platform_caps import requires_posix_mode_bits
from hub_e2e_helpers import Recorder, running_hub
from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.acl import IDENTITY_ENROLL, AclPolicy, AclRule
from synapse_channel.core.handlers.identity_enrollments import KEY_ROTATED_CLOSE_CODE
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.identity_binding import load_identity_trust_bundle
from synapse_channel.core.identity_enrollments import DEFAULT_ENROLLMENT_WINDOW_SECONDS
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType
from synapse_channel.core.role_grants import RoleGrants
from synapse_channel.machine_identity import ensure_machine_identity

OPERATOR = "OPS/operator"
SEAT = "PROJ/seat"


class Machines:
    """Isolated machine identities, one directory per label."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def kwargs(self, label: str) -> dict[str, Any]:
        identity = ensure_machine_identity(base=self.root / label)
        return {"identity_key_path": str(identity.key_path), "identity_key_id": identity.key_id}

    def public(self, label: str) -> tuple[str, str]:
        identity = ensure_machine_identity(base=self.root / label)
        return identity.key_id, identity.public_key


def _trust(tmp_path: Path, machines: Machines, *members: tuple[str, str]) -> Path:
    keys = []
    for label, name in members:
        key_id, public = machines.public(label)
        keys.append({"key_id": key_id, "public_key": public, "senders": [name]})
    path = tmp_path / "identity-trust.json"
    path.write_text(json.dumps({"keys": keys}), encoding="utf-8")
    return path


def _hub(
    tmp_path: Path,
    machines: Machines,
    *,
    roles: dict[str, frozenset[str]] | None = None,
    acl_pattern: str = "PROJ/*",
    acl_namespace: str = "OPS",
    namespaces: tuple[str, ...] = ("PROJ",),
    rate: int = 10,
    enrollments: bool = True,
    static: tuple[tuple[str, str], ...] = (("operator", OPERATOR),),
    require_binding: bool = True,
    acl: bool = True,
    clock: Callable[[], float] | None = None,
    window: float = DEFAULT_ENROLLMENT_WINDOW_SECONDS,
) -> SynapseHub:
    return SynapseHub(
        clock=clock,
        identity_enrollment_window_seconds=window,
        journal=EventStore(tmp_path / "events.db"),
        identity_trust_bundle=load_identity_trust_bundle(_trust(tmp_path, machines, *static)),
        require_identity_binding=require_binding,
        identity_pin_path=tmp_path / "pins.json",
        acl_policy=(
            AclPolicy([AclRule(IDENTITY_ENROLL, "agent", acl_pattern, acl_namespace, "enroller")])
            if acl
            else None
        ),
        role_grants=RoleGrants(
            roles if roles is not None else {"OPS/identity-enroller": frozenset({OPERATOR})}
        ),
        identity_enrollment_path=tmp_path / "enrolled.json" if enrollments else None,
        identity_enrollment_namespaces=namespaces,
        identity_enrollment_rate=rate,
    )


@contextlib.asynccontextmanager
async def _connected(
    name: str, uri: str, key: dict[str, Any]
) -> AsyncIterator[tuple[SynapseAgent, Recorder]]:
    """Connect ``name`` and require the hub to admit it (its own presence update)."""
    recorder = Recorder()
    agent = SynapseAgent(name, recorder, uri=uri, verbose=False, machine_identity=False, **key)
    task = asyncio.create_task(agent.connect())
    try:
        assert await agent.wait_until_ready(timeout=3.0)
        await recorder.wait_for(
            lambda m: m.get("type") == "presence_update" and m.get("agent") == name
        )
        yield agent, recorder
    finally:
        agent.running = False
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def _refused(name: str, uri: str, key: dict[str, Any]) -> str:
    """Return the close reason of a registration the hub refuses."""
    agent = SynapseAgent(name, None, uri=uri, verbose=False, machine_identity=False, **key)
    task = asyncio.create_task(agent.connect())
    try:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 3.0
        while loop.time() < deadline and agent.last_close_code is None:
            await asyncio.sleep(0.01)
        assert agent.last_close_code is not None, "the hub admitted the registration"
        return agent.last_close_reason
    finally:
        agent.running = False
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def _enrol(recorder: Recorder, agent: SynapseAgent, **fields: Any) -> dict[str, Any]:
    recorder.messages.clear()
    await agent.send_message(MessageType.IDENTITY_ENROLL, target="System", **fields)
    return await recorder.wait_for(lambda m: m.get("type") == MessageType.IDENTITY_ENROLL_RESULT)


def _audit(tmp_path: Path) -> list[dict[str, Any]]:
    store = EventStore(tmp_path / "events.db")
    try:
        return [
            dict(event.payload)
            for event in store.read_all()
            if event.kind == EventKind.IDENTITY_ENROLLMENT
        ]
    finally:
        store.close()


def _seat_request(machines: Machines, label: str = "seat", **extra: Any) -> dict[str, Any]:
    key_id, public = machines.public(label)
    return {"name": SEAT, "key_id": key_id, "public_key": public, "reason": "new seat", **extra}


async def test_an_enrolled_key_registers_at_once_and_survives_a_restart(tmp_path: Path) -> None:
    machines = Machines(tmp_path / "machines")
    hub = _hub(tmp_path, machines)
    async with running_hub(hub) as (_hub_ref, uri):
        assert "identity binding failed" in await _refused(SEAT, uri, machines.kwargs("seat"))
        async with _connected(OPERATOR, uri, machines.kwargs("operator")) as (operator, inbox):
            result = await _enrol(inbox, operator, **_seat_request(machines))
        assert result["applied"] is True, result
        async with _connected(SEAT, uri, machines.kwargs("seat")):
            pass
    assert hub.journal is not None
    hub.journal.close()
    audit = _audit(tmp_path)
    assert [entry["status"] for entry in audit] == ["approved", "applied"]
    assert audit[-1]["operator"] == OPERATOR and audit[-1]["name"] == SEAT
    stored = json.loads((tmp_path / "enrolled.json").read_text(encoding="utf-8"))
    assert [entry["senders"] for entry in stored["keys"]] == [[SEAT]]

    restarted = _hub(tmp_path, machines)
    async with running_hub(restarted) as (_hub_ref, uri):
        async with _connected(SEAT, uri, machines.kwargs("seat")):
            pass
    assert restarted.journal is not None
    restarted.journal.close()


@requires_posix_mode_bits
async def test_the_enrolment_store_is_owner_only(tmp_path: Path) -> None:
    machines = Machines(tmp_path / "machines")
    hub = _hub(tmp_path, machines)
    async with running_hub(hub) as (_hub_ref, uri):
        async with _connected(OPERATOR, uri, machines.kwargs("operator")) as (operator, inbox):
            assert (await _enrol(inbox, operator, **_seat_request(machines)))["applied"]
    assert hub.journal is not None
    hub.journal.close()
    assert stat.S_IMODE((tmp_path / "enrolled.json").stat().st_mode) == 0o600


@pytest.mark.parametrize(
    ("overrides", "detail"),
    [
        ({"enrollments": False}, "online enrolment is disabled"),
        ({"acl_pattern": "OTHER/*"}, "not authorised to enrol"),
        ({"acl": False}, "not authorised to enrol"),
        ({"roles": {}}, "'identity-enroller' role grant"),
        ({"namespaces": ("OTHER",)}, "does not allow enrolment in the name's namespace"),
        ({"rate": 0}, "rate limit"),
    ],
)
async def test_each_authority_gate_refuses_on_its_own(
    tmp_path: Path, overrides: dict[str, Any], detail: str
) -> None:
    machines = Machines(tmp_path / "machines")
    hub = _hub(tmp_path, machines, **overrides)
    async with running_hub(hub) as (_hub_ref, uri):
        async with _connected(OPERATOR, uri, machines.kwargs("operator")) as (operator, inbox):
            result = await _enrol(inbox, operator, **_seat_request(machines))
        assert "identity binding failed" in await _refused(SEAT, uri, machines.kwargs("seat"))
    assert result["applied"] is False
    assert detail in result["payload"]
    assert hub.journal is not None
    hub.journal.close()
    audit = _audit(tmp_path)
    authorised = overrides.get("namespaces") is not None or overrides.get("rate") == 0
    # only an authorised operator's refusals reach the durable audit
    assert [entry["status"] for entry in audit] == (["denied"] if authorised else [])
    assert not (tmp_path / "enrolled.json").exists()


async def test_an_unproven_requester_is_refused(tmp_path: Path) -> None:
    machines = Machines(tmp_path / "machines")
    hub = _hub(tmp_path, machines, require_binding=False)
    async with running_hub(hub) as (_hub_ref, uri):
        # no identity key at all: a token-less, unsigned socket under OPERATOR's name
        async with _connected(OPERATOR, uri, {}) as (operator, inbox):
            result = await _enrol(inbox, operator, **_seat_request(machines))
    assert hub.journal is not None
    hub.journal.close()
    assert result["applied"] is False
    assert "cryptographically proven requester" in result["payload"]


async def test_request_validation_refuses_before_anything_changes(tmp_path: Path) -> None:
    machines = Machines(tmp_path / "machines")
    hub = _hub(tmp_path, machines, static=(("operator", OPERATOR), ("fixed", "PROJ/fixed")))
    operator_key_id, operator_public = machines.public("operator")
    fixed_key_id, _ = machines.public("fixed")
    seat = _seat_request(machines)
    cases: list[tuple[dict[str, Any], str]] = [
        ({**seat, "name": "PROJ"}, "not authorised"),  # authority is checked first
        ({**seat, "name": "PROJ/"}, "<project>/<id>"),
        ({**seat, "key_id": "bad key"}, "key id must be"),
        ({**seat, "public_key": "not-base64!"}, "public key must be"),
        ({**seat, "public_key": "AAAA"}, "public key must be"),
        ({**seat, "public_key": 7}, "public key must be"),
        ({**seat, "reason": "  "}, "non-empty operator reason"),
        ({**seat, "reason": "x" * 501}, "exceeds 500"),
        ({**seat, "expires_at": 1.0}, "future time"),
        ({**seat, "expires_at": "soon"}, "future time"),
        ({**seat, "expires_at": True}, "future time"),
        ({**seat, "key_id": operator_key_id}, "already in use"),
        ({**seat, "name": "PROJ/fixed"}, "already covers this name"),
        ({**seat, "expected_key_id": "nothing-yet"}, "no current enrolled key"),
    ]
    async with running_hub(hub) as (_hub_ref, uri):
        async with _connected(OPERATOR, uri, machines.kwargs("operator")) as (operator, inbox):
            results = [
                (await _enrol(inbox, operator, **fields), detail) for fields, detail in cases
            ]
    assert hub.journal is not None
    hub.journal.close()
    for result, detail in results:
        assert result["applied"] is False
        assert detail in result["payload"], (detail, result["payload"])
    assert fixed_key_id and operator_public
    assert not (tmp_path / "enrolled.json").exists()


async def test_rotation_needs_the_current_key_and_evicts_the_old_socket(tmp_path: Path) -> None:
    machines = Machines(tmp_path / "machines")
    hub = _hub(tmp_path, machines)
    old_key_id, _ = machines.public("seat")
    new_request = _seat_request(machines, "seat-2", reason="rotate")
    async with running_hub(hub) as (_hub_ref, uri):
        async with _connected(OPERATOR, uri, machines.kwargs("operator")) as (operator, inbox):
            assert (await _enrol(inbox, operator, **_seat_request(machines)))["applied"]
            seat = SynapseAgent(
                SEAT,
                None,
                uri=uri,
                verbose=False,
                machine_identity=False,
                **machines.kwargs("seat"),
            )
            seat_task = asyncio.create_task(seat.connect())
            try:
                assert await seat.wait_until_ready(timeout=3.0)
                blind = await _enrol(inbox, operator, **new_request)
                wrong = await _enrol(inbox, operator, **new_request, expected_key_id="other")
                rotated = await _enrol(inbox, operator, **new_request, expected_key_id=old_key_id)
                loop = asyncio.get_running_loop()
                deadline = loop.time() + 3.0
                while loop.time() < deadline and seat.last_close_code is None:
                    await asyncio.sleep(0.01)
                assert seat.last_close_code == KEY_ROTATED_CLOSE_CODE
            finally:
                seat.running = False
                seat_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await seat_task
        assert "identity binding failed" in await _refused(SEAT, uri, machines.kwargs("seat"))
        async with _connected(SEAT, uri, machines.kwargs("seat-2")):
            pass
    assert hub.journal is not None
    hub.journal.close()
    assert "rotate it by naming the current key id" in blind["payload"]
    assert "rotate it by naming the current key id" in wrong["payload"]
    assert rotated["applied"] is True
    stored = json.loads((tmp_path / "enrolled.json").read_text(encoding="utf-8"))
    assert {entry["key_id"]: entry["revoked"] for entry in stored["keys"]} == {
        old_key_id: True,
        new_request["key_id"]: False,
    }
    assert _audit(tmp_path)[-1]["evicted_live_socket"] is True


async def test_an_enroller_rotates_only_its_own_key_and_stays_connected(tmp_path: Path) -> None:
    machines = Machines(tmp_path / "machines")
    roles = {
        "OPS/identity-enroller": frozenset({OPERATOR}),
        "PROJ/identity-enroller": frozenset({SEAT}),
    }
    hub = _hub(tmp_path, machines, roles=roles, acl_pattern="*", acl_namespace="")
    old_key_id, _ = machines.public("seat")
    async with running_hub(hub) as (_hub_ref, uri):
        async with _connected(OPERATOR, uri, machines.kwargs("operator")) as (operator, inbox):
            assert (await _enrol(inbox, operator, **_seat_request(machines)))["applied"]
        async with _connected(SEAT, uri, machines.kwargs("seat")) as (seat, seat_inbox):
            extra = await _enrol(seat_inbox, seat, **_seat_request(machines, "seat-2"))
            own = await _enrol(
                seat_inbox, seat, **_seat_request(machines, "seat-2"), expected_key_id=old_key_id
            )
            assert seat.last_close_code is None
    assert hub.journal is not None
    hub.journal.close()
    assert "only rotate its own key" in extra["payload"]
    assert own["applied"] is True
    assert _audit(tmp_path)[-1]["evicted_live_socket"] is False


def test_the_hub_needs_a_trust_bundle_and_a_journal(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="--identity-trust and --db"):
        SynapseHub(identity_enrollment_path=tmp_path / "enrolled.json")


async def test_a_store_that_cannot_be_written_changes_nothing(tmp_path: Path) -> None:
    machines = Machines(tmp_path / "machines")
    hub = _hub(tmp_path, machines)
    (tmp_path / "enrolled.json").mkdir()  # after start-up: the atomic replace will fail
    async with running_hub(hub) as (_hub_ref, uri):
        async with _connected(OPERATOR, uri, machines.kwargs("operator")) as (operator, inbox):
            result = await _enrol(inbox, operator, **_seat_request(machines))
        assert "identity binding failed" in await _refused(SEAT, uri, machines.kwargs("seat"))
    assert hub.journal is not None
    hub.journal.close()
    assert result["applied"] is False
    assert "could not persist the enrolment store" in result["payload"]
    assert [entry["status"] for entry in _audit(tmp_path)] == ["approved", "not_applied"]


async def test_an_applied_enrolment_updates_the_keys_and_the_effective_bundle(
    tmp_path: Path,
) -> None:
    machines = Machines(tmp_path / "machines")
    hub = _hub(tmp_path, machines)
    seat_key_id, _public = machines.public("seat")
    assert hub.identity_trust_bundle is not None
    assert seat_key_id not in hub.identity_trust_bundle.keys
    async with running_hub(hub) as (_hub_ref, uri):
        async with _connected(OPERATOR, uri, machines.kwargs("operator")) as (operator, inbox):
            result = await _enrol(inbox, operator, **_seat_request(machines))
    assert hub.journal is not None
    hub.journal.close()
    assert result["applied"] is True, result
    assert set(hub.enrolled_identity_keys) == {seat_key_id}
    assert hub.identity_trust_bundle is not None
    assert seat_key_id in hub.identity_trust_bundle.keys
    # the operator's own bundle is the base of every merge and never gains a key
    assert hub.static_identity_trust is not None
    assert seat_key_id not in hub.static_identity_trust.keys


async def test_an_applied_enrolment_counts_against_the_rate_limit(tmp_path: Path) -> None:
    machines = Machines(tmp_path / "machines")
    hub = _hub(tmp_path, machines, rate=1)
    async with running_hub(hub) as (_hub_ref, uri):
        async with _connected(OPERATOR, uri, machines.kwargs("operator")) as (operator, inbox):
            first = await _enrol(inbox, operator, **_seat_request(machines))
            second = await _enrol(
                inbox, operator, **_seat_request(machines, "other", name="PROJ/other")
            )
    assert hub.journal is not None
    hub.journal.close()
    assert first["applied"] is True, first
    assert second["applied"] is False
    assert "rate limit" in second["payload"]


async def test_the_rate_limit_window_follows_the_hub_clock(tmp_path: Path) -> None:
    machines = Machines(tmp_path / "machines")
    now = [1000.0]
    hub = _hub(tmp_path, machines, rate=1, clock=lambda: now[0], window=60.0)
    other = _seat_request(machines, "other", name="PROJ/other")
    async with running_hub(hub) as (_hub_ref, uri):
        async with _connected(OPERATOR, uri, machines.kwargs("operator")) as (operator, inbox):
            first = await _enrol(inbox, operator, **_seat_request(machines))
            inside = await _enrol(inbox, operator, **other)
            now[0] += 61.0
            after = await _enrol(inbox, operator, **other)
    assert hub.journal is not None
    hub.journal.close()
    assert first["applied"] is True, first
    assert inside["applied"] is False and "rate limit" in inside["payload"]
    assert after["applied"] is True, after


async def test_a_pinned_requester_is_proven_and_its_pin_key_is_audited(tmp_path: Path) -> None:
    machines = Machines(tmp_path / "machines")
    # no binding requirement: the operator is proven by the key the hub pinned at first use
    hub = _hub(tmp_path, machines, require_binding=False)
    operator_key_id, _public = machines.public("operator")
    async with running_hub(hub) as (_hub_ref, uri):
        async with _connected(OPERATOR, uri, machines.kwargs("operator")) as (operator, inbox):
            result = await _enrol(inbox, operator, **_seat_request(machines))
    assert hub.journal is not None
    hub.journal.close()
    assert result["applied"] is True, result
    audit = _audit(tmp_path)
    assert [entry["status"] for entry in audit] == ["approved", "applied"]
    assert audit[-1]["operator_key_id"] == operator_key_id


def test_replacing_enrolled_keys_needs_a_static_trust_bundle() -> None:
    hub = SynapseHub()
    with pytest.raises(ValueError, match="identity trust bundle"):
        hub.replace_enrolled_identity_keys({})
    assert hub.enrolled_identity_keys == {}
    assert hub.identity_trust_bundle is None


async def _revoke(recorder: Recorder, agent: SynapseAgent, **fields: Any) -> dict[str, Any]:
    recorder.messages.clear()
    await agent.send_message(MessageType.IDENTITY_REVOKE, target="System", **fields)
    return await recorder.wait_for(lambda m: m.get("type") == MessageType.IDENTITY_REVOKE_RESULT)


async def test_a_revoked_key_is_refused_and_its_socket_closed(tmp_path: Path) -> None:
    machines = Machines(tmp_path / "machines")
    hub = _hub(tmp_path, machines, static=(("operator", OPERATOR), ("fixed", "PROJ/fixed")))
    seat_key_id, _ = machines.public("seat")
    fixed_key_id, _ = machines.public("fixed")
    async with running_hub(hub) as (_hub_ref, uri):
        async with _connected(OPERATOR, uri, machines.kwargs("operator")) as (operator, inbox):
            assert (await _enrol(inbox, operator, **_seat_request(machines)))["applied"]
            revoke = {"name": SEAT, "key_id": seat_key_id, "reason": "laptop stolen"}
            refusals = [
                await _revoke(inbox, operator, **{**revoke, "name": "OTHER/x"}),
                await _revoke(inbox, operator, **{**revoke, "name": "PROJ/"}),
                await _revoke(inbox, operator, **{**revoke, "reason": " "}),
                await _revoke(inbox, operator, **{**revoke, "reason": "x" * 501}),
                await _revoke(inbox, operator, **{**revoke, "key_id": fixed_key_id}),
                await _revoke(inbox, operator, **{**revoke, "key_id": "unknown"}),
                await _revoke(inbox, operator, **{**revoke, "name": "PROJ/else"}),
            ]
            async with _connected(SEAT, uri, machines.kwargs("seat")) as (seat, _seat_inbox):
                revoked = await _revoke(inbox, operator, **revoke)
                loop = asyncio.get_running_loop()
                deadline = loop.time() + 3.0
                while loop.time() < deadline and seat.last_close_code is None:
                    await asyncio.sleep(0.01)
                assert seat.last_close_code == KEY_ROTATED_CLOSE_CODE
            again = await _revoke(inbox, operator, **revoke)
        assert "identity binding failed" in await _refused(SEAT, uri, machines.kwargs("seat"))
    assert hub.journal is not None
    hub.journal.close()
    expected = [
        "not authorised",
        "<project>/<id>",
        "non-empty operator reason",
        "exceeds 500",
        "revoked by editing that file",
        "no enrolled key has this id",
        "does not prove this name",
    ]
    for refusal, detail in zip(refusals, expected, strict=True):
        assert refusal["applied"] is False and detail in refusal["payload"], refusal["payload"]
    assert revoked["applied"] is True and "revoked" in revoked["payload"]
    assert "already revoked" in again["payload"]
    stored = json.loads((tmp_path / "enrolled.json").read_text(encoding="utf-8"))
    assert [(entry["key_id"], entry["revoked"]) for entry in stored["keys"]] == [
        (seat_key_id, True)
    ]
    applied = [entry for entry in _audit(tmp_path) if entry["status"] == "applied"]
    assert [entry["action"] for entry in applied] == ["enroll", "revoke"]
    assert applied[-1]["evicted_live_socket"] is True


async def test_revoking_one_key_keeps_the_other_enrolled_keys(tmp_path: Path) -> None:
    machines = Machines(tmp_path / "machines")
    hub = _hub(tmp_path, machines)
    seat_key_id, _ = machines.public("seat")
    other_key_id, _ = machines.public("other")
    async with running_hub(hub) as (_hub_ref, uri):
        async with _connected(OPERATOR, uri, machines.kwargs("operator")) as (operator, inbox):
            assert (await _enrol(inbox, operator, **_seat_request(machines)))["applied"]
            other = _seat_request(machines, "other", name="PROJ/other")
            assert (await _enrol(inbox, operator, **other))["applied"]
            revoked = await _revoke(
                inbox, operator, name=SEAT, key_id=seat_key_id, reason="laptop stolen"
            )
        assert "identity binding failed" in await _refused(SEAT, uri, machines.kwargs("seat"))
        async with _connected("PROJ/other", uri, machines.kwargs("other")):
            pass
    assert hub.journal is not None
    hub.journal.close()
    assert revoked["applied"] is True, revoked
    assert set(hub.enrolled_identity_keys) == {seat_key_id, other_key_id}
    stored = json.loads((tmp_path / "enrolled.json").read_text(encoding="utf-8"))
    assert sorted((entry["key_id"], entry["revoked"]) for entry in stored["keys"]) == sorted(
        [(seat_key_id, True), (other_key_id, False)]
    )


async def test_an_enroller_revoking_its_own_key_stays_connected(tmp_path: Path) -> None:
    machines = Machines(tmp_path / "machines")
    roles = {
        "OPS/identity-enroller": frozenset({OPERATOR}),
        "PROJ/identity-enroller": frozenset({SEAT}),
    }
    hub = _hub(tmp_path, machines, roles=roles, acl_pattern="*", acl_namespace="")
    seat_key_id, _ = machines.public("seat")
    async with running_hub(hub) as (_hub_ref, uri):
        async with _connected(OPERATOR, uri, machines.kwargs("operator")) as (operator, inbox):
            assert (await _enrol(inbox, operator, **_seat_request(machines)))["applied"]
        async with _connected(SEAT, uri, machines.kwargs("seat")) as (seat, seat_inbox):
            own = await _revoke(
                seat_inbox, seat, name=SEAT, key_id=seat_key_id, reason="retiring this key"
            )
            assert seat.last_close_code is None
    assert hub.journal is not None
    hub.journal.close()
    assert own["applied"] is True
    assert _audit(tmp_path)[-1]["evicted_live_socket"] is False
