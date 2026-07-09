# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real hub + SQLCipher event-store end-to-end tests
"""Exercise the production hub journal path over a real SQLCipher EventStore.

These tests open a live hub, claim over WebSocket, restart on the same encrypted
database, and assert claim enforcement still holds — the same surface operators
use with ``synapse hub --db … --db-key-file …``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from websockets.asyncio.client import ClientConnection, connect

from hub_e2e_helpers import read_until_type, running_hub, send_json
from synapse_channel.core.at_rest import generate_key_file
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.persistence_sqlcipher import sqlcipher_available

pytestmark = pytest.mark.skipif(
    not sqlcipher_available(),
    reason="sqlcipher3-binary not installed (pip install synapse-channel[sqlcipher])",
)


async def _connect_agent(uri: str, name: str) -> ClientConnection:
    websocket = await connect(uri)
    await read_until_type(websocket, "welcome")
    await send_json(websocket, sender=name, type="heartbeat")
    return websocket


async def test_encrypted_hub_persists_claim_and_rejects_conflict_after_restart(
    tmp_path: Path,
) -> None:
    """Claim on a SQLCipher journal, restart hub, second agent denied on same path."""
    key_path = generate_key_file(tmp_path / "hub.key")
    db = tmp_path / "hub.db"

    store_a = EventStore(db, key_file=key_path)
    assert store_a.encrypted is True
    hub_a = SynapseHub(default_ttl_seconds=3600.0, hub_id="syn-enc-a", journal=store_a)
    async with running_hub(hub_a) as (_, uri):
        async with await _connect_agent(uri, "A") as ws:
            await send_json(ws, sender="A", type="claim", task_id="ENC-1", paths=["src/sqlcipher"])
            await read_until_type(ws, "claim_granted")
            await send_json(ws, sender="A", type="chat", payload="enc-body-marker")
            await read_until_type(ws, "chat")
    store_a.close()

    # offline file must not leak chat body
    assert b"enc-body-marker" not in db.read_bytes()

    store_b = EventStore(db, key_file=key_path)
    hub_b = SynapseHub(default_ttl_seconds=3600.0, hub_id="syn-enc-b", journal=store_b)
    assert "ENC-1" in hub_b.state.claims
    assert hub_b.state.claims["ENC-1"].paths == ("src/sqlcipher",)

    async with running_hub(hub_b) as (_, uri):
        async with await _connect_agent(uri, "B") as ws:
            await send_json(ws, sender="B", type="claim", task_id="ENC-2", paths=["src/sqlcipher"])
            denied = await read_until_type(ws, "claim_denied")
    store_b.close()

    assert denied["type"] == "claim_denied"
    assert "ENC-1" in str(denied.get("payload") or denied)


async def test_encrypted_hub_journal_kinds_include_claim_and_chat(tmp_path: Path) -> None:
    """Authoritative journal rows for claim+chat exist after a live encrypted hub run."""
    key_path = generate_key_file(tmp_path / "hub.key")
    db = tmp_path / "hub.db"
    store = EventStore(db, key_file=key_path)
    hub = SynapseHub(default_ttl_seconds=300.0, hub_id="syn-enc", journal=store)
    async with running_hub(hub) as (_, uri):
        async with await _connect_agent(uri, "A") as ws:
            await send_json(ws, sender="A", type="claim", task_id="K1", paths=["src"])
            await read_until_type(ws, "claim_granted")
            await send_json(ws, sender="A", type="chat", payload="hello-enc")
            await read_until_type(ws, "chat")
    kinds = {event.kind for event in store.read_all()}
    store.close()
    assert "claim" in kinds
    assert "chat" in kinds
