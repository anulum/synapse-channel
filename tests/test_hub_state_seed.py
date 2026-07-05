# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — unit tests for resuming or building the hub's durable state

from __future__ import annotations

from pathlib import Path

import pytest

from synapse_channel.core.hub_state_seed import SeededHubState, seed_hub_state
from synapse_channel.core.ledger import Blackboard
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.state import SynapseState


def _seed(
    journal: EventStore | None,
    *,
    max_history: int = 100,
    compact_hint_threshold: int = 1000,
) -> SeededHubState:
    return seed_hub_state(
        journal,
        default_ttl_seconds=3600.0,
        max_history=max_history,
        max_progress=50,
        max_progress_per_author=10,
        max_progress_per_task=10,
        max_claims_per_agent=8,
        max_offers_per_agent=8,
        max_paths_per_claim=8,
        compact_hint_threshold=compact_hint_threshold,
    )


def _populated_store(tmp_path: Path) -> EventStore:
    store = EventStore(tmp_path / "events.db")
    store.append("message", {"sender": "A", "target": "all", "payload": "hi", "type": "chat"})
    store.append("message", {"sender": "B", "target": "all", "payload": "yo", "type": "chat"})
    return store


def test_seed_starts_fresh_without_a_journal() -> None:
    seeded = _seed(None)

    assert isinstance(seeded.state, SynapseState)
    assert seeded.chat_history == []
    assert isinstance(seeded.blackboard, Blackboard)
    assert seeded.message_seq == 0
    assert seeded.finding_counts == {}
    assert seeded.idempotency_seed == ()


def test_seed_resumes_from_an_empty_journal(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store = EventStore(tmp_path / "events.db")
    with caplog.at_level("WARNING", logger="synapse.hub"):
        seeded = _seed(store, compact_hint_threshold=1000)

    assert isinstance(seeded.state, SynapseState)
    assert seeded.message_seq == 0
    assert seeded.chat_history == []
    assert caplog.records == []  # an empty log is well under the hint threshold
    store.close()


def test_seed_warns_when_the_log_exceeds_the_hint_threshold(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store = _populated_store(tmp_path)
    with caplog.at_level("WARNING", logger="synapse.hub"):
        _seed(store, compact_hint_threshold=1)  # two records is over the threshold

    assert "never auto-compacted" in caplog.text
    assert all(record.name == "synapse.hub" for record in caplog.records)
    store.close()


def test_seed_is_silent_below_the_hint_threshold(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store = _populated_store(tmp_path)
    with caplog.at_level("WARNING", logger="synapse.hub"):
        _seed(store, compact_hint_threshold=10)  # two records is under the threshold

    assert caplog.records == []
    store.close()


def test_seed_trims_replayed_history_to_the_bound(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # The replayed history is sliced to the last max_history entries; on an empty
    # log the slice is empty, exercising the trim regardless of content.
    store = _populated_store(tmp_path)
    with caplog.at_level("WARNING", logger="synapse.hub"):
        seeded = _seed(store, max_history=1, compact_hint_threshold=1000)

    assert len(seeded.chat_history) <= 1
    store.close()
