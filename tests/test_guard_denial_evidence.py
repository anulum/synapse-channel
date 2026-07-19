# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — authenticated durable guard-denial evidence regressions

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from websockets.asyncio.client import connect

from hub_e2e_helpers import read_until_type, running_hub, send_json
from synapse_channel.core.auth import TokenAuthenticator
from synapse_channel.core.durable_ingress import DurableIngressQuota
from synapse_channel.core.guard_evidence import GuardEvidenceError, parse_guard_denial
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
