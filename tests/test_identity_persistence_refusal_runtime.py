# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — governed identity persistence refusal and recovery
"""Exercise real identity storage faults through signed operator connections."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from hub_e2e_helpers import AgentHandle, Recorder, close_agents, http_get, running_hub
from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.acl import IDENTITY_ENROLL, PIN_RECLAIM, AclPolicy, AclRule
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.identity_binding import load_identity_trust_bundle
from synapse_channel.core.identity_enrollments import write_enrolled_keys
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.role_grants import RoleGrants
from synapse_channel.dashboard import start_dashboard_server
from synapse_channel.machine_identity import ensure_machine_identity

_OPERATOR = "OPS/operator"
_OBSERVER = "OPS/observer"
_SEAT = "PROJ/seat"
_CANARY = "PRIVATE_IDENTITY_STORAGE_CANARY"


async def _connect(root: Path, label: str, name: str, uri: str) -> AgentHandle:
    """Connect an isolated machine key through the normal signed registration."""
    identity = ensure_machine_identity(base=root / label)
    recorder = Recorder()
    agent = SynapseAgent(
        name,
        recorder,
        uri=uri,
        verbose=False,
        machine_identity=False,
        identity_key_path=str(identity.key_path),
        identity_key_id=identity.key_id,
    )
    handle = AgentHandle(agent, recorder, asyncio.create_task(agent.connect()))
    assert await agent.wait_until_ready(timeout=3.0)
    await recorder.wait_for(lambda frame: frame.get("agent") == name)
    return handle


async def _refused(root: Path, label: str, uri: str) -> None:
    """Prove that the actual registration gate still refuses an untrusted key."""
    identity = ensure_machine_identity(base=root / label)
    agent = SynapseAgent(
        _SEAT,
        None,
        uri=uri,
        verbose=False,
        machine_identity=False,
        identity_key_path=str(identity.key_path),
        identity_key_id=identity.key_id,
    )
    handle = AgentHandle(agent, Recorder(), asyncio.create_task(agent.connect()))
    try:
        await asyncio.wait_for(_wait_close(agent), timeout=3.0)
        assert "identity" in agent.last_close_reason
    finally:
        await handle.close()


async def _wait_close(agent: SynapseAgent) -> None:
    """Wait for the public close outcome without changing the client's state."""
    while agent.last_close_code is None:
        await asyncio.sleep(0.01)


async def _request(
    operator: AgentHandle, message_type: str, fields: dict[str, Any]
) -> dict[str, Any]:
    """Send a governed public verb and await its private typed result."""
    operator.recorder.messages.clear()
    await operator.agent.send_message(message_type, **fields)
    return await operator.recorder.wait_for(
        lambda frame: frame.get("type") == message_type + "_result"
    )


def _hub(root: Path, store: EventStore, *, reclaim: bool) -> SynapseHub:
    """Build the real operator policy and trust bundle for this workflow."""
    keys = []
    for label, name in (("operator", _OPERATOR), ("observer", _OBSERVER)):
        identity = ensure_machine_identity(base=root / "machines" / label)
        keys.append(
            {"key_id": identity.key_id, "public_key": identity.public_key, "senders": [name]}
        )
    trust_path = root / "trust.json"
    trust_path.write_text(json.dumps({"keys": keys}), encoding="utf-8")
    trust = load_identity_trust_bundle(trust_path)
    return SynapseHub(
        journal=store,
        identity_pin_path=root / _CANARY / "pins.json",
        identity_trust_bundle=None if reclaim else trust,
        require_identity_binding=not reclaim,
        acl_policy=AclPolicy(
            [
                AclRule(
                    PIN_RECLAIM if reclaim else IDENTITY_ENROLL,
                    "agent",
                    _SEAT,
                    "OPS",
                    "governed persistence recovery",
                ),
            ]
        ),
        role_grants=RoleGrants({"OPS/identity-enroller": frozenset({_OPERATOR})}),
        identity_enrollment_path=None if reclaim else root / _CANARY / "enrolled.json",
        identity_enrollment_namespaces=("PROJ",),
        identity_enrollment_rate=2,
    )


@pytest.mark.parametrize("operation", ["reclaim", "break_glass", "enrol", "rotate", "revoke"])
@pytest.mark.parametrize("fault", ["parent", "replace"])
async def test_identity_storage_refusal_preserves_authority_and_recovers(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, operation: str, fault: str
) -> None:
    """A real failed write changes neither authority nor disk, then recovers once.

    Both failure locations exercise the actual atomic writer. Approved and
    not-applied records must stay distinct in the durable journal and dashboard
    feeds; private paths belong only in server diagnostics. A retry is a fresh
    audited request, and repeating an already applied change is a refusal.
    """
    reclaim = operation in {"reclaim", "break_glass"}
    root = tmp_path / "machines"
    storage = tmp_path / _CANARY
    storage.mkdir()
    path = storage / ("pins.json" if reclaim else "enrolled.json")
    if not reclaim:
        write_enrolled_keys(path, {})
    store = EventStore(tmp_path / "events.db")
    hub = _hub(tmp_path, store, reclaim=reclaim)
    identity = ensure_machine_identity(base=root / "seat")
    replacement = ensure_machine_identity(base=root / "replacement")
    fields: dict[str, Any] = {
        "name": _SEAT,
        "key_id": identity.key_id,
        "public_key": identity.public_key,
        "reason": "storage recovery",
    }
    message_type = "identity_enroll"
    detail = "could not persist the enrolment store"
    kind = EventKind.IDENTITY_ENROLLMENT
    if reclaim:
        message_type = "identity_pin_reclaim"
        detail = "could not persist the reclaimed pin table"
        kind = EventKind.IDENTITY_PIN_RECLAIM
        fields = {
            "pin_name": _SEAT,
            "expected_key_id": identity.key_id,
            "reason": "storage recovery",
            "break_glass": operation == "break_glass",
        }
    elif operation == "rotate":
        fields.update(
            key_id=replacement.key_id,
            public_key=replacement.public_key,
            expected_key_id=identity.key_id,
        )
    elif operation == "revoke":
        message_type = "identity_revoke"
        fields.pop("public_key")
    handles: list[AgentHandle] = []
    try:
        async with running_hub(hub) as (_, uri):
            operator = await _connect(root, "operator", _OPERATOR, uri)
            observer = await _connect(root, "observer", _OBSERVER, uri)
            handles.extend((operator, observer))
            if operation in {"rotate", "revoke"}:
                # Setup and recovery exhaust the two-change budget; the
                # failed write must not consume a successful-change slot.
                setup = await _request(
                    operator,
                    "identity_enroll",
                    {
                        "name": _SEAT,
                        "key_id": identity.key_id,
                        "public_key": identity.public_key,
                        "reason": "initial key",
                    },
                )
                assert setup["applied"] is True
            if operation != "enrol":
                target = await _connect(root, "seat", _SEAT, uri)
                handles.append(target)
                if operation == "reclaim":
                    await target.close()
            before = path.read_bytes()
            audits_before = [event for event in store.read_all() if event.kind == kind]
            observer.recorder.messages.clear()
            saved = tmp_path / "preserved-storage"
            if fault == "parent":
                storage.rename(saved)
                storage.write_text("unavailable storage", encoding="utf-8")
                preserved = saved / path.name
            else:
                path.rename(saved)
                path.mkdir()
                preserved = saved
            failed = await _request(operator, message_type, fields)
            assert failed["applied"] is False
            assert failed["payload"] == detail
            assert failed["target"] == _OPERATOR
            assert _CANARY not in json.dumps(operator.recorder.messages)
            assert preserved.read_bytes() == before
            assert _CANARY in caplog.text
            assert any(record.exc_info is not None for record in caplog.records)
            audits = [event for event in store.read_all() if event.kind == kind]
            assert [event.payload["status"] for event in audits[len(audits_before) :]] == [
                "approved",
                "not_applied",
            ]
            assert audits[-1].payload["detail"] == detail
            assert audits[-1].payload["approved_seq"] == audits[-2].seq == failed["audit_seq"]
            assert all(event.payload["applied"] is False for event in audits[-2:])
            assert _CANARY not in json.dumps([event.payload for event in audits])
            await operator.agent.chat("refusal-barrier", target=_OBSERVER)
            await observer.recorder.wait_for(
                lambda frame: frame.get("payload") == "refusal-barrier"
            )
            assert not any(frame.get("event_kind") == kind for frame in observer.recorder.messages)
            assert _CANARY not in json.dumps(observer.recorder.messages)
            if operation in {"break_glass", "rotate", "revoke"}:
                assert target.agent.last_close_code is None
                await target.agent.chat("old-key-still-live", target=_OBSERVER)
                await observer.recorder.wait_for(
                    lambda frame: frame.get("payload") == "old-key-still-live"
                )
            dashboard = start_dashboard_server(
                host="127.0.0.1",
                port=0,
                uri=uri,
                name="DASH",
                token=None,
                ready_timeout=0.1,
                response_timeout=0.1,
                refresh_seconds=5,
                allow_non_loopback=False,
                dashboard_token="isolated-test-viewer",
                reliability_db=tmp_path / "events.db",
            )
            try:
                for endpoint in ("/events.json?limit=1000", "/receipts.json?limit=1000"):
                    status, _, body = await http_get(
                        f"http://localhost:{dashboard.port}",
                        endpoint,
                        authorization="Bearer isolated-test-viewer",
                    )
                    assert status == 200
                    assert _CANARY not in body
                    assert "not_applied" in body or "not-applied" in body
                    if endpoint.startswith("/receipts"):
                        rows = [
                            row
                            for row in json.loads(body)["receipts"]
                            if row["source_event_kind"] == kind
                        ]
                        assert [row["status"] for row in rows[len(audits_before) :]] == [
                            "approved",
                            "not_applied",
                        ]
                        assert rows[-1]["subject"] == _SEAT
                        assert rows[-1]["actor"] == _OPERATOR
                        assert rows[-1]["payload"]["detail"] == detail
            finally:
                dashboard.close()
            repeated_failure = await _request(operator, message_type, fields)
            assert repeated_failure["applied"] is False
            assert repeated_failure["payload"] == detail
            assert preserved.read_bytes() == before
            if fault == "parent":
                storage.unlink()
                saved.rename(storage)
            else:
                path.rmdir()
                saved.rename(path)
            assert path.read_bytes() == before
            await _refused(root, "replacement", uri)
            recovered = await _request(operator, message_type, fields)
            assert recovered["applied"] is True, recovered
            await observer.recorder.wait_for(lambda frame: frame.get("event_kind") == kind)
            assert path.read_bytes() != before
            repeated = await _request(operator, message_type, fields)
            assert repeated["applied"] is False
            audits = [event for event in store.read_all() if event.kind == kind]
            assert (
                sum(event.payload["status"] == "applied" for event in audits)
                == len(audits_before) // 2 + 1
            )
            if operation in {"break_glass", "rotate", "revoke"}:
                await asyncio.wait_for(_wait_close(target.agent), timeout=3.0)
                assert target.agent.last_close_code == (4017 if reclaim else 4018)
            await close_agents(*handles)
        store.close()
        reopened = EventStore(tmp_path / "events.db")
        restarted = _hub(tmp_path, reopened, reclaim=reclaim)
        try:
            async with running_hub(restarted) as (_, uri):
                if operation == "revoke":
                    await _refused(root, "seat", uri)
                else:
                    label = "seat" if operation == "enrol" else "replacement"
                    accepted = await _connect(root, label, _SEAT, uri)
                    await accepted.close()
                    if operation in {"reclaim", "break_glass", "rotate"}:
                        await _refused(root, "seat", uri)
        finally:
            reopened.close()
    finally:
        await close_agents(*handles)
        store.close()
