# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — authenticated durable guard-denial evidence regressions

from __future__ import annotations

import asyncio
import functools
import json
from pathlib import Path
from typing import Any

import pytest
from websockets.asyncio.client import connect

from hub_e2e_helpers import read_until_type, running_hub, send_json
from synapse_channel import SynapseAgent
from synapse_channel.core.auth import TokenAuthenticator
from synapse_channel.core.durable_ingress import DurableIngressQuota
from synapse_channel.core.guard_evidence import GuardEvidenceError, parse_guard_denial
from synapse_channel.core.handlers.guard_evidence import handle_guard_denial
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore
from synapse_channel.guard_evidence import guard_denial_digests, submit_guard_denial

TOKEN = "guard-token"
ACTOR = "secret-provider-actor"
SESSION = "secret-provider-session"
CALL = "secret-provider-tool-call"
RAW_PATH = "secret/repository/path.py"


def _frame(*, call_sha256: str | None = None, idem_key: str = "guard-1") -> dict[str, Any]:
    actor_digest, call_digest, scope_digest = guard_denial_digests(
        provider="codex",
        identity=ACTOR,
        session_id=SESSION,
        tool_use_id=CALL,
        paths=[RAW_PATH],
    )
    return {
        "sender": "guard-evidence/reporter-1",
        "target": "System",
        "type": "guard_denial",
        "payload": "",
        "actor_sha256": actor_digest,
        "call_sha256": call_sha256 or call_digest,
        "scope_sha256": scope_digest,
        "provider": "codex",
        "reason_code": "GUARD_NO_CLAIM",
        "path_count": 1,
        "idem_key": idem_key,
    }


def _guard_events(store: EventStore) -> list[dict[str, Any]]:
    return [event.payload for event in store.read_all() if event.kind == EventKind.GUARD_DENIAL]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("reason_code", "UNKNOWN"),
        ("reason_code", []),
        ("provider", "unknown"),
        ("provider", {}),
        ("path_count", True),
        ("path_count", 513),
        ("actor_sha256", "A" * 64),
        ("call_sha256", "short"),
        ("scope_sha256", None),
    ],
)
def test_guard_denial_schema_rejects_unbounded_or_unknown_values(field: str, value: object) -> None:
    frame = _frame()
    frame[field] = value
    with pytest.raises(GuardEvidenceError):
        parse_guard_denial(frame)


async def test_secured_durable_hub_records_content_minimized_denial_and_reopens(
    tmp_path: Path,
) -> None:
    db = tmp_path / "guard.db"
    store = EventStore(db)
    hub = SynapseHub(
        hub_id="guard-test",
        authenticator=TokenAuthenticator([TOKEN]),
        journal=store,
    )
    frame = _frame()
    async with running_hub(hub) as (_, uri):
        async with connect(uri) as websocket:
            await send_json(
                websocket,
                sender=frame["sender"],
                type="heartbeat",
                payload="online",
                token=TOKEN,
            )
            await read_until_type(websocket, "welcome")
            await websocket.send(json.dumps(frame))
            recorded = await read_until_type(websocket, "guard_denial_recorded")

    assert recorded["call_sha256"] == frame["call_sha256"]
    events = _guard_events(store)
    assert len(events) == 1
    payload_text = json.dumps(events[0], sort_keys=True)
    assert events[0]["decision"] == "deny"
    assert events[0]["reason_code"] == "GUARD_NO_CLAIM"
    assert events[0]["credential_principal_sha256"]
    for secret in (ACTOR, SESSION, CALL, RAW_PATH, TOKEN, frame["sender"]):
        assert secret not in payload_text
    store.close()

    reopened = EventStore(db)
    try:
        assert len(_guard_events(reopened)) == 1
        restarted = SynapseHub(
            hub_id="guard-test-restarted",
            authenticator=TokenAuthenticator([TOKEN]),
            journal=reopened,
        )
        assert restarted.journal_corrupt_rows == ()
    finally:
        reopened.close()


async def test_open_hub_refuses_guard_evidence_without_journalling(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "open.db")
    hub = SynapseHub(hub_id="open-guard-test", journal=store)
    async with running_hub(hub) as (_, uri):
        async with connect(uri) as websocket:
            await read_until_type(websocket, "welcome")
            await websocket.send(json.dumps(_frame()))
            error = await read_until_type(websocket, "error")

    assert error["error_code"] == "guard_evidence_unavailable"
    assert _guard_events(store) == []
    store.close()


async def test_guard_evidence_duplicate_is_durable_at_most_once(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "duplicate.db")
    hub = SynapseHub(
        hub_id="guard-test",
        authenticator=TokenAuthenticator([TOKEN]),
        journal=store,
    )
    frame = _frame()
    async with running_hub(hub) as (_, uri):
        async with connect(uri) as websocket:
            await send_json(
                websocket,
                sender=frame["sender"],
                type="heartbeat",
                payload="online",
                token=TOKEN,
            )
            await read_until_type(websocket, "welcome")
            await websocket.send(json.dumps(frame))
            first = await read_until_type(websocket, "guard_denial_recorded")
            await websocket.send(json.dumps(frame))
            duplicate = await read_until_type(websocket, "guard_denial_recorded")

    assert duplicate["audit_seq"] == first["audit_seq"]
    assert len(_guard_events(store)) == 1
    store.close()


async def _submit(uri: str, frame: dict[str, Any]) -> dict[str, Any]:
    """Authenticate and send one denial frame, returning the next refusal or record."""
    async with connect(uri) as websocket:
        await send_json(
            websocket,
            sender=str(frame["sender"]),
            type="heartbeat",
            payload="online",
            token=TOKEN,
        )
        await read_until_type(websocket, "welcome")
        await websocket.send(json.dumps(frame))
        recorded = await read_until_type(websocket, "guard_denial_recorded")
        return recorded


async def test_guard_evidence_replays_after_restart_and_conflicts_when_changed(
    tmp_path: Path,
) -> None:
    """After a restart the same denial is returned once; a changed call conflicts."""
    db = tmp_path / "replay.db"
    store = EventStore(db)
    hub = SynapseHub(
        hub_id="guard-test",
        authenticator=TokenAuthenticator([TOKEN]),
        journal=store,
    )
    frame = _frame()
    async with running_hub(hub) as (_, uri):
        first = await _submit(uri, frame)
    assert len(_guard_events(store)) == 1
    store.close()

    reopened = EventStore(db)
    restarted = SynapseHub(
        hub_id="guard-test",
        authenticator=TokenAuthenticator([TOKEN]),
        journal=reopened,
    )
    changed = _frame(call_sha256="f" * 64, idem_key="guard-1")
    async with running_hub(restarted) as (_, uri):
        replayed = await _submit(uri, frame)
        async with connect(uri) as websocket:
            await send_json(
                websocket,
                sender=str(changed["sender"]),
                type="heartbeat",
                payload="online",
                token=TOKEN,
            )
            await read_until_type(websocket, "welcome")
            await websocket.send(json.dumps(changed))
            conflict = await read_until_type(websocket, "error")
    assert replayed["audit_seq"] == first["audit_seq"]
    assert replayed["call_sha256"] == frame["call_sha256"]
    assert conflict["error_code"] == "idempotency_conflict"
    assert conflict["payload"] == "Idempotency key was already used for a different request."
    assert len(_guard_events(reopened)) == 1
    reopened.close()


async def test_guard_evidence_has_a_fixed_credential_quota(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "quota.db")
    hub = SynapseHub(
        hub_id="guard-test",
        authenticator=TokenAuthenticator([TOKEN]),
        journal=store,
    )
    hub.guard_evidence_quota = DurableIngressQuota(
        max_events=1,
        max_bytes=4096,
        window_seconds=60.0,
    )
    first = _frame()
    second = _frame(call_sha256="f" * 64, idem_key="guard-2")
    async with running_hub(hub) as (_, uri):
        async with connect(uri) as websocket:
            await send_json(
                websocket,
                sender=first["sender"],
                type="heartbeat",
                payload="online",
                token=TOKEN,
            )
            await read_until_type(websocket, "welcome")
            await websocket.send(json.dumps(first))
            await read_until_type(websocket, "guard_denial_recorded")
            await websocket.send(json.dumps(second))
            error = await read_until_type(websocket, "error")

    assert error["error_code"] == "guard_evidence_rate_limited"
    assert len(_guard_events(store)) == 1
    store.close()


async def test_one_shot_reporter_requires_token_and_records_on_real_hub(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "reporter.db")
    hub = SynapseHub(
        hub_id="guard-test",
        authenticator=TokenAuthenticator([TOKEN]),
        journal=store,
    )
    async with running_hub(hub) as (_, uri):
        assert not await submit_guard_denial(
            provider="codex",
            identity=ACTOR,
            session_id=SESSION,
            tool_use_id=CALL,
            paths=[RAW_PATH],
            reason_code="GUARD_NO_CLAIM",
            uri=uri,
            token=None,
            timeout=1.0,
        )
        assert await submit_guard_denial(
            provider="codex",
            identity=ACTOR,
            session_id=SESSION,
            tool_use_id=CALL,
            paths=[RAW_PATH],
            reason_code="GUARD_NO_CLAIM",
            uri=uri,
            token=TOKEN,
            timeout=1.0,
        )

    assert len(_guard_events(store)) == 1
    store.close()


def _report(uri: str, **overrides: Any) -> dict[str, Any]:
    arguments: dict[str, Any] = {
        "provider": "codex",
        "identity": ACTOR,
        "session_id": SESSION,
        "tool_use_id": CALL,
        "paths": [RAW_PATH],
        "reason_code": "GUARD_NO_CLAIM",
        "uri": uri,
        "token": TOKEN,
        "timeout": 2.0,
    }
    arguments.update(overrides)
    return arguments


@pytest.mark.parametrize("timeout", ["soon", None, float("nan"), float("inf"), 0.0, -1.0, 30.5])
async def test_reporter_refuses_an_unusable_timeout_before_connecting(
    tmp_path: Path, timeout: object
) -> None:
    """A timeout outside (0, 30] fails closed before any agent exists, even on a ready hub."""
    store = EventStore(tmp_path / "timeout.db")
    hub = SynapseHub(
        hub_id="guard-test",
        authenticator=TokenAuthenticator([TOKEN]),
        journal=store,
    )
    built: list[str] = []

    def recording_factory(name: str, *args: Any, **kwargs: Any) -> SynapseAgent:
        built.append(name)
        return SynapseAgent(name, *args, **kwargs)

    async with running_hub(hub) as (_, uri):
        assert await submit_guard_denial(
            **_report(uri, timeout=1.0), agent_factory=recording_factory
        )
        assert len(built) == 1
        # A different call, so only the timeout can stop a second record.
        assert not await submit_guard_denial(
            **_report(uri, timeout=timeout, session_id="other-session", tool_use_id="other-call"),
            agent_factory=recording_factory,
        )

    assert len(built) == 1
    assert len(_guard_events(store)) == 1
    store.close()


async def test_reporter_reports_false_when_the_hub_refuses_the_credential_or_the_evidence(
    tmp_path: Path,
) -> None:
    """A wrong token and an unknown reason are both answered by the hub and recorded nowhere."""
    store = EventStore(tmp_path / "refused.db")
    hub = SynapseHub(
        hub_id="guard-test",
        authenticator=TokenAuthenticator([TOKEN]),
        journal=store,
    )
    async with running_hub(hub) as (_, uri):
        assert not await submit_guard_denial(**_report(uri, token="not-the-token"))
        assert not await submit_guard_denial(**_report(uri, reason_code="UNKNOWN"))

    assert _guard_events(store) == []
    store.close()


async def test_reporter_reports_false_when_no_hub_listens(unused_tcp_port: int) -> None:
    """An unreachable hub ends the attempt at once instead of waiting for the timeout."""
    loop = asyncio.get_running_loop()
    started = loop.time()
    assert not await submit_guard_denial(**_report(f"ws://127.0.0.1:{unused_tcp_port}"))
    assert loop.time() - started < 1.5


async def test_reporter_reports_false_for_a_malformed_hub_address() -> None:
    """An address that is not a WebSocket URI fails closed and leaves no task running."""
    before = asyncio.all_tasks()
    assert not await submit_guard_denial(**_report("not a websocket uri"))
    assert asyncio.all_tasks() == before


async def test_reporter_gives_up_when_the_hub_never_completes_the_handshake() -> None:
    """A listener that accepts TCP but never answers ends the attempt at the phase timeout."""
    release = asyncio.Event()

    async def _stall(_reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await release.wait()
        writer.close()

    listener = await asyncio.start_server(_stall, "127.0.0.1", 0)
    port = int(listener.sockets[0].getsockname()[1])
    loop = asyncio.get_running_loop()
    started = loop.time()
    try:
        assert not await submit_guard_denial(**_report(f"ws://127.0.0.1:{port}", timeout=0.3))
    finally:
        release.set()
        listener.close()
        await listener.wait_closed()
    assert loop.time() - started < 2.0


async def test_reporter_contains_a_misconfigured_agent_factory() -> None:
    """A real agent that refuses its own configuration is reported as False, not raised."""
    misconfigured = functools.partial(SynapseAgent, capability_card_lifetime_seconds=0.0)
    before = asyncio.all_tasks()
    assert not await submit_guard_denial(**_report("ws://127.0.0.1:9"), agent_factory=misconfigured)
    assert asyncio.all_tasks() == before


async def test_evidence_without_an_idempotency_key_is_journalled_directly(tmp_path: Path) -> None:
    """A frame without idem_key takes the direct journal path and reports its sequence."""
    store = EventStore(tmp_path / "direct.db")
    hub = SynapseHub(
        hub_id="guard-test",
        authenticator=TokenAuthenticator([TOKEN]),
        journal=store,
    )
    frame = _frame()
    del frame["idem_key"]
    async with running_hub(hub) as (_, uri):
        async with connect(uri) as websocket:
            await send_json(
                websocket,
                sender=frame["sender"],
                type="heartbeat",
                payload="online",
                token=TOKEN,
            )
            await read_until_type(websocket, "welcome")
            await websocket.send(json.dumps(frame))
            recorded = await read_until_type(websocket, "guard_denial_recorded")

    events = [event for event in store.read_all() if event.kind == EventKind.GUARD_DENIAL]
    assert len(events) == 1
    assert recorded["audit_seq"] == events[0].seq
    assert recorded["call_sha256"] == frame["call_sha256"]
    store.close()


class _DirectHub(SynapseHub):
    """A real hub whose replies are kept, for a handler call that bypasses network ingress."""

    def __init__(self, store: EventStore) -> None:
        super().__init__(
            hub_id="guard-direct",
            authenticator=TokenAuthenticator([TOKEN]),
            journal=store,
        )
        self.sent: list[dict[str, Any]] = []

    async def send_json(self, _websocket: Any, data: dict[str, Any]) -> None:
        self.sent.append(data)


async def test_direct_call_without_token_provenance_is_refused(tmp_path: Path) -> None:
    """A handler call whose socket never passed token ingress gets no evidence record.

    Network ingress always binds an ``auth-token:`` principal on an authenticated hub;
    a direct integration that skips it falls back to a legacy principal, which cannot
    attest the record's provenance and so is refused.
    """
    store = EventStore(tmp_path / "direct-call.db")
    hub = _DirectHub(store)
    frame = _frame()
    await handle_guard_denial(hub, frame["sender"], frame, object())

    assert hub.sent[-1]["error_code"] == "guard_evidence_unauthenticated"
    assert _guard_events(store) == []
    store.close()
