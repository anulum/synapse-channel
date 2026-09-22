# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — dashboard attention feed HTTP boundary
"""Check authenticated browser access to the owner-local attention queue."""

from __future__ import annotations

import json
import time
from pathlib import Path

from dashboard_helpers import _http_get
from synapse_channel.core.approvals import format_approval_note
from synapse_channel.core.attention import project_hub_attention
from synapse_channel.core.attention_store import sync_evidence
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore
from synapse_channel.dashboard import start_dashboard_server


def test_attention_feed_uses_same_store_and_requires_dashboard_bearer(tmp_path: Path) -> None:
    hub_path = tmp_path / "hub.db"
    queue = tmp_path / "private" / "queue.db"
    hub = EventStore(hub_path)
    try:
        hub.append(
            EventKind.LEDGER_PROGRESS,
            {
                "kind": "approval",
                "author": "operator:one",
                "text": format_approval_note(
                    subject="TASK-7", state="requested", reason="secret task body"
                ),
            },
            durable=True,
        )
        now = time.time()
        sync_evidence(
            queue, source="hub", evidence=project_hub_attention(hub.iter_events(), now=now), now=now
        )
    finally:
        hub.close()
    server = start_dashboard_server(
        host="127.0.0.1",
        port=0,
        uri="ws://127.0.0.1:1",
        name="SYNAPSE-CHANNEL/dashboard",
        token=None,
        ready_timeout=0.01,
        response_timeout=0.01,
        refresh_seconds=5,
        allow_non_loopback=False,
        dashboard_token="attention-secret",
        attention_store=queue,
    )
    try:
        denied, _, _ = _http_get(server.url("/attention.json"))
        allowed, content_type, body = _http_get(
            server.url("/attention.json"), authorization="Bearer attention-secret"
        )
    finally:
        server.close()
    assert denied == 401
    assert allowed == 200 and content_type == "application/json"
    report = json.loads(body)
    assert report["alerts"][0]["key"] == "approval:TASK-7"
    assert report["state"] == "active"
    assert "secret task body" not in body
    assert str(queue) not in body


def test_attention_feed_missing_store_fails_visible(tmp_path: Path) -> None:
    server = start_dashboard_server(
        host="127.0.0.1",
        port=0,
        uri="ws://127.0.0.1:1",
        name="SYNAPSE-CHANNEL/dashboard",
        token=None,
        ready_timeout=0.01,
        response_timeout=0.01,
        refresh_seconds=5,
        allow_non_loopback=False,
        dashboard_token="attention-secret",
        attention_store=tmp_path / "missing.db",
    )
    try:
        status, _, body = _http_get(
            server.url("/attention.json"), authorization="Bearer attention-secret"
        )
    finally:
        server.close()
    assert status == 503
    assert "unavailable" in body


def test_attention_feed_corrupt_store_fails_visible(tmp_path: Path) -> None:
    queue = tmp_path / "private" / "queue.db"
    queue.parent.mkdir(mode=0o700)
    queue.write_text("not sqlite")
    queue.chmod(0o600)
    server = start_dashboard_server(
        host="127.0.0.1",
        port=0,
        uri="ws://127.0.0.1:1",
        name="SYNAPSE-CHANNEL/dashboard",
        token=None,
        ready_timeout=0.01,
        response_timeout=0.01,
        refresh_seconds=5,
        allow_non_loopback=False,
        dashboard_token="attention-secret",
        attention_store=queue,
    )
    try:
        status, _, body = _http_get(
            server.url("/attention.json"), authorization="Bearer attention-secret"
        )
    finally:
        server.close()
    assert status == 503
    assert "unavailable" in body


def test_attention_feed_unconfigured_is_absent() -> None:
    server = start_dashboard_server(
        host="127.0.0.1",
        port=0,
        uri="ws://127.0.0.1:1",
        name="SYNAPSE-CHANNEL/dashboard",
        token=None,
        ready_timeout=0.01,
        response_timeout=0.01,
        refresh_seconds=5,
        allow_non_loopback=False,
        dashboard_token="attention-secret",
    )
    try:
        status, _, body = _http_get(
            server.url("/attention.json"), authorization="Bearer attention-secret"
        )
    finally:
        server.close()
    assert status == 404
    assert "not configured" in body
