# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — the forward event feed over an encrypted journal
"""A consumer reads an SQLCipher journal's feed through the hub, holding no key."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest

from hub_e2e_helpers import AgentHandle, close_agents, connect_agent, running_hub
from synapse_channel.core.at_rest_keys import generate_key_file
from synapse_channel.core.feed import payload_sha256
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import record_finding
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.persistence_sqlcipher import (
    SqlCipherUnavailableError,
    sqlcipher_available,
)


async def _reply(handle: AgentHandle, msg_type: str, reply_type: str, **fields: Any) -> Any:
    """Send one frame to the hub and return its correlated reply."""
    request_id = uuid.uuid4().hex
    await handle.agent.send_message(msg_type, target="System", request_id=request_id, **fields)
    return await handle.recorder.wait_for(
        lambda m: m.get("type") == reply_type and m.get("request_id") == request_id,
        timeout=10.0,
    )


async def test_an_encrypted_journal_feeds_a_consumer_that_holds_no_key(tmp_path: Path) -> None:
    """With a SQLCipher driver the hub feeds an encrypted journal to a consumer without the key.

    Without the driver no encrypted journal can be opened for the hub: the keyed
    open is refused with the documented error and creates nothing.
    """
    key = generate_key_file(tmp_path / "hub.key")
    db = tmp_path / "hub.db"
    if not sqlcipher_available():
        with pytest.raises(SqlCipherUnavailableError, match="sqlcipher"):
            EventStore(db, key_file=key)
        assert not db.exists()
        return
    journal = EventStore(db, key_file=key)
    record_finding(journal, {"text": "kept under the key", "note": "ü"})
    query = {
        "version": 1,
        "consumer": "R/consumer",
        "hub_id": "",
        "journal_instance": "",
        "after_seq": 0,
        "kinds": ["finding"],
        "limit": 10,
    }
    try:
        async with running_hub(SynapseHub(journal=journal, hub_id="feed-hub")) as (_, uri):
            consumer = await connect_agent("R/consumer", uri)
            try:
                snapshot = await _reply(
                    consumer, "history_request", "history_snapshot", feed_query=query
                )
                page = snapshot["feed_page"]
                result = await _reply(
                    consumer,
                    "feed_ack",
                    "feed_ack_result",
                    consumer="R/consumer",
                    hub_id=page["hub_id"],
                    journal_instance=page["journal_instance"],
                    acked_seq=page["next_after"],
                )
            finally:
                await close_agents(consumer)
        instance = journal.journal_instance()
    finally:
        journal.close()

    assert instance is not None and page["journal_instance"] == instance.instance
    assert [row["payload"] for row in page["rows"]] == [{"text": "kept under the key", "note": "ü"}]
    assert page["rows"][0]["payload_sha256"] == payload_sha256(page["rows"][0]["payload"])
    assert result["feed_ack"]["recorded"] is True
    assert b"kept under the key" not in db.read_bytes()
