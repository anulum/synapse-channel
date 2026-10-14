# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — store-backed dashboard feeds open SQLCipher DBs

from __future__ import annotations

import asyncio
import json
from http.client import HTTPConnection
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from hub_e2e_helpers import running_hub
from synapse_channel import SynapseHub
from synapse_channel.core.at_rest import generate_key_file
from synapse_channel.core.merkle import proof_from_json, verify_inclusion
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.persistence_sqlcipher import sqlcipher_available
from synapse_channel.dashboard import start_dashboard_server
from synapse_channel.dashboard_store_feeds import build_events_tail, event_store_key

pytestmark = pytest.mark.skipif(
    not sqlcipher_available(),
    reason="sqlcipher3-binary not installed",
)


def test_events_tail_reads_encrypted_store_via_key_context(tmp_path: Path) -> None:
    key = generate_key_file(tmp_path / "hub.key")
    db = tmp_path / "hub.db"
    store = EventStore(db, key_file=key)
    store.append("chat", {"text": "feed-enc"})
    store.close()

    with event_store_key(key):
        doc = build_events_tail(db, since=0, limit=10)
    events = doc["events"]
    assert isinstance(events, list)
    assert len(events) == 1
    event = events[0]
    assert isinstance(event, dict)
    payload = event["payload"]
    assert isinstance(payload, dict)
    assert payload["text"] == "feed-enc"


def test_events_tail_without_key_fails_on_encrypted_store(tmp_path: Path) -> None:
    from synapse_channel.core.persistence_sqlcipher import SqlCipherKeyError

    key = generate_key_file(tmp_path / "hub.key")
    db = tmp_path / "hub.db"
    EventStore(db, key_file=key).close()
    with pytest.raises(SqlCipherKeyError):
        build_events_tail(db, since=0, limit=10)


def _http_document(url: str, token: str) -> tuple[int, dict[str, object]]:
    """Read one real authenticated dashboard response, including refusals."""
    address = urlsplit(url)
    assert address.hostname is not None
    connection = HTTPConnection(address.hostname, address.port, timeout=5.0)
    try:
        path = address.path + ("?" + address.query if address.query else "")
        connection.request("GET", path, headers={"Authorization": "Bearer " + token})
        response = connection.getresponse()
        code = response.status
        body = response.read().decode("utf-8")
    finally:
        connection.close()
    document: dict[str, object] = json.loads(body) if code == 200 else {"refusal": body}
    return code, document


@pytest.mark.asyncio
@pytest.mark.parametrize("encrypted", [False, True])
@pytest.mark.parametrize("feed", ["/metrics.json", "/merkle-proof.json?seq=1"])
async def test_authenticated_metrics_and_merkle_use_selected_store_key(
    tmp_path: Path, encrypted: bool, feed: str
) -> None:
    """HTTP projections preserve plaintext and real SQLCipher store semantics."""
    key = generate_key_file(tmp_path / "hub.key") if encrypted else None
    db = tmp_path / "hub.db"
    store = EventStore(db, key_file=key)
    try:
        store.append("chat", {"text": "dashboard-feed-sample"}, ts=100.0)
    finally:
        store.close()
    token = "dashboard-feed-test-credential"
    hub = SynapseHub(hub_id="dashboard-feed-test")
    try:
        async with running_hub(hub) as (_, uri):
            server = start_dashboard_server(
                host="127.0.0.1",
                port=0,
                uri=uri,
                name="dashboard-feed-test-reader",
                token=None,
                ready_timeout=1.0,
                response_timeout=1.0,
                refresh_seconds=0,
                allow_non_loopback=False,
                reliability_db=db,
                reliability_db_key_file=key,
                dashboard_token=token,
            )
            try:
                code, document = await asyncio.to_thread(_http_document, server.url(feed), token)
            finally:
                server.close()
    finally:
        hub.close()
    assert code == 200, document
    if feed == "/metrics.json":
        log = document["log"]
        assert isinstance(log, dict)
        assert log["total_events"] == 1
        assert document["events_by_kind"] == {"chat": 1}
    else:
        assert document["present"] is True
        assert document["tree_size"] == 1
        assert verify_inclusion(proof_from_json(document))
