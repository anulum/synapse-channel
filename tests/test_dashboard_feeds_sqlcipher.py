# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — store-backed dashboard feeds open SQLCipher DBs

from __future__ import annotations

from pathlib import Path

import pytest

from synapse_channel.core.at_rest import generate_key_file
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.persistence_sqlcipher import sqlcipher_available
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
